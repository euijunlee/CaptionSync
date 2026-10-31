"""One-click automatic subtitle synchronisation.

The engine combines four ideas:

1. **Scale hypotheses** -- the subtitle timeline may be uniformly shifted, may
   drift because of an FPS conversion, or may drift because of editing.  A
   small set of plausible scale factors (1.0, common FPS ratios and the ratio
   implied by the first/last cue) is scored against the speech onsets.
2. **Monotonic matching** -- cue starts are matched to speech onsets with a
   weighted longest-increasing-subsequence, so a missing/extra line can never
   make two cues grab the same speech segment or cross each other.
3. **Robust affine fit** -- a RANSAC line fit converts the matched pairs into
   ``speech_time = scale * cue_time + offset`` and rejects bad matches.
4. **Piecewise repair** -- remaining residual steps (a scene that starts at a
   different offset) are found with binary segmentation, then cue boundaries
   are snapped to nearby speech starts/ends.

Everything is deterministic and works on plain cue/speech lists, so the
algorithms can be unit-tested without audio or a GUI.
"""

from __future__ import annotations

import math
import random
from bisect import bisect_left
from dataclasses import dataclass, field
from statistics import median

from .models import MIN_DURATION, Cue

__all__ = [
    "AutoSyncOptions",
    "AutoSyncPlan",
    "build_auto_plan",
    "apply_auto_plan",
]


# FPS values offered by the UI.  Only plausible conversion pairs are tested.
_FPS_VALUES = (23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 50.0, 59.94, 60.0)
_FPS_PAIRS = (
    (23.976, 24.0),
    (24.0, 23.976),
    (23.976, 25.0),
    (25.0, 23.976),
    (24.0, 25.0),
    (25.0, 24.0),
    (29.97, 30.0),
    (30.0, 29.97),
    (25.0, 50.0),
    (50.0, 25.0),
    (29.97, 59.94),
    (59.94, 29.97),
)


@dataclass
class AutoSyncOptions:
    """Tuning knobs for :func:`build_auto_plan`."""

    detect_fps: bool = True
    fix_drift: bool = True
    fix_segments: bool = True
    snap_boundaries: bool = True
    max_segments: int = 4
    match_tolerance: float = 0.35
    snap_window: float = 0.80
    snap_end_window: float = 1.20
    segment_threshold: float = 0.15
    min_segment_matches: int = 3
    min_confidence: float = 0.30


@dataclass
class AutoSyncPlan:
    """A complete, undo-friendly correction proposal.

    ``targets`` always has one ``(start, end)`` pair per input cue, even when
    nothing changes, so the UI can apply the whole plan atomically.
    """

    targets: list[tuple[float, float]]
    scale: float = 1.0
    offset: float = 0.0
    fps: tuple[float, float] | None = None
    segments: list[tuple[int, int, float]] = field(default_factory=list)
    boundary_snaps: int = 0
    matched: int = 0
    inliers: int = 0
    total: int = 0
    confidence: float = 0.0
    residual_median: float = 0.0
    residual_p90: float = 0.0
    changed: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        return (
            self.total > 0
            and self.matched >= 3
            and self.confidence >= 0.0
        )

    @property
    def scale_percent(self) -> float:
        return self.scale * 100.0

    def describe(self) -> list[str]:
        """Human readable report lines used by the UI."""

        lines: list[str] = []
        if abs(self.offset) >= 0.001 or abs(self.scale - 1.0) >= 0.0005:
            if self.fps:
                lines.append(
                    f"FPS {self.fps[0]:g} → {self.fps[1]:g} "
                    f"(비율 {self.scale_percent:.3f}%)"
                )
            elif abs(self.scale - 1.0) >= 0.0005:
                lines.append(f"시간 비율 {self.scale_percent:.3f}%")
            if abs(self.offset) >= 0.001:
                lines.append(f"전체 이동 {self.offset:+.3f}초")
        if self.segments:
            lines.append(f"구간 보정 {len(self.segments)}곳")
        if self.boundary_snaps:
            lines.append(f"시작·끝 스냅 {self.boundary_snaps}개")
        lines.append(f"음성 매칭 {self.matched}/{self.total}개")
        if self.matched:
            lines.append(
                f"남은 오차 중앙 {self.residual_median:.3f}초 · "
                f"90% {self.residual_p90:.3f}초"
            )
        lines.append(f"신뢰도 {self.confidence * 100:.0f}%")
        return lines


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------
def _sample(values: list[float], limit: int) -> list[float]:
    if len(values) <= limit:
        return list(values)
    if limit <= 1:
        return [values[0]]
    step = (len(values) - 1) / (limit - 1)
    return [values[int(round(i * step))] for i in range(limit)]


def _abs_percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(abs(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percent / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _nearest_starts(
    starts: list[float], target: float, tolerance: float, limit: int = 2
) -> list[tuple[int, float]]:
    """Return up to ``limit`` nearby indices as ``(index, distance)``."""

    if not starts:
        return []
    position = bisect_left(starts, target)
    candidates: list[tuple[int, float]] = []
    for index in range(max(0, position - 3), min(len(starts), position + 3)):
        distance = abs(starts[index] - target)
        if distance <= tolerance:
            candidates.append((index, distance))
    if not candidates and 0 <= position < len(starts):
        distance = abs(starts[position] - target)
        if distance <= tolerance:
            candidates.append((position, distance))
    candidates.sort(key=lambda item: item[1])
    return candidates[:limit]


def _polish_offset(
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    offset: float,
    window: float = 1.2,
    iterations: int = 3,
) -> float:
    """Iteratively re-centre the offset on the nearest speech onsets.

    The histogram gives a good coarse offset, but per-line jitter can split
    its votes across several bins.  Starting from that coarse value, this is a
    tiny ICP step: match locally, take the median residual, repeat.
    """

    for _ in range(iterations):
        deltas: list[float] = []
        for start in cue_starts:
            near = _nearest_starts(
                speech_starts, scale * start + offset, window, limit=1
            )
            if near:
                deltas.append(
                    speech_starts[near[0][0]] - (scale * start + offset)
                )
        if len(deltas) < 3:
            break
        change = median(deltas)
        offset += change
        if abs(change) < 0.002:
            break
    return offset


def _count_matches(
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    offset: float,
    tolerance: float,
) -> int:
    count = 0
    for start in cue_starts:
        if _nearest_starts(
            speech_starts, scale * start + offset, tolerance, limit=1
        ):
            count += 1
    return count


def _histogram_offset(
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    bin_size: float = 0.1,
) -> tuple[float, int]:
    """Vote for ``speech = scale * cue + offset`` over all pairs."""

    buckets: dict[int, int] = {}
    deltas: list[float] = []
    cues = _sample(cue_starts, 500)
    speech = _sample(speech_starts, 900)
    for cue in cues:
        mapped = scale * cue
        for onset in speech:
            delta = onset - mapped
            deltas.append(delta)
            key = int(round(delta / bin_size))
            buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return 0.0, 0
    best_key, best_votes = max(
        buckets.items(), key=lambda item: (item[1], -abs(item[0]))
    )
    near = [
        delta
        for delta in deltas
        if abs(delta - best_key * bin_size) <= 2.5 * bin_size
    ]
    if not near:
        return best_key * bin_size, best_votes
    return median(near), best_votes


def _candidate_scales(cue_starts: list[float], speech_starts: list[float]) -> list[float]:
    scales = [1.0]
    for sub_fps, video_fps in _FPS_PAIRS:
        scales.append(sub_fps / video_fps)
    if len(cue_starts) >= 3 and len(speech_starts) >= 3:
        ordered_cues = sorted(cue_starts)
        cue_span = ordered_cues[-1] - ordered_cues[0]
        speech_span = speech_starts[-1] - speech_starts[0]
        if cue_span > 1.0 and speech_span > 1.0:
            implied = speech_span / cue_span
            if 0.5 <= implied <= 2.0:
                scales.append(implied)
    # Deduplicate while keeping order (1.0 stays first).
    seen: set[float] = set()
    unique: list[float] = []
    for value in scales:
        key = round(value, 7)
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


def _fit_line(pairs: list[tuple[float, float]]) -> tuple[float, float, int] | None:
    """Robustly fit ``y = scale * x + offset`` and return inlier count."""

    if not pairs:
        return None
    if len(pairs) < 4:
        offsets = [y - x for x, y in pairs]
        return 1.0, median(offsets), len(pairs)

    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    x_span = max(xs) - min(xs)
    tolerance = 0.25
    rng = random.Random(20260214)
    best: tuple[int, float, float, float] | None = None

    for _ in range(400):
        i = rng.randrange(len(pairs))
        j = rng.randrange(len(pairs))
        if i == j:
            continue
        if abs(xs[i] - xs[j]) < max(2.0, x_span * 0.08):
            continue
        scale = (ys[j] - ys[i]) / (xs[j] - xs[i])
        if not (0.5 <= scale <= 2.0):
            continue
        offset = ys[i] - scale * xs[i]
        residuals = [abs(y - (scale * x + offset)) for x, y in pairs]
        inliers = sum(1 for value in residuals if value <= tolerance)
        score = (inliers, -median(residuals), -abs(scale - 1.0))
        if best is None or score > (best[0], best[1], best[2]):
            best = (inliers, -median(residuals), -abs(scale - 1.0), scale, offset)

    if best is None or best[0] < max(3, int(len(pairs) * 0.35)):
        offsets = [y - x for x, y in pairs]
        return 1.0, median(offsets), len(pairs)

    _, _, _, scale, offset = best
    residuals = [abs(y - (scale * x + offset)) for x, y in pairs]
    inlier_pairs = [
        pair for pair, value in zip(pairs, residuals) if value <= tolerance
    ]
    if len(inlier_pairs) < 3:
        return scale, offset, len(inlier_pairs)

    mean_x = sum(x for x, _ in inlier_pairs) / len(inlier_pairs)
    mean_y = sum(y for _, y in inlier_pairs) / len(inlier_pairs)
    denominator = sum((x - mean_x) ** 2 for x, _ in inlier_pairs)
    if denominator <= 1e-9:
        return 1.0, median(y - x for x, y in inlier_pairs), len(inlier_pairs)
    fitted_scale = sum(
        (x - mean_x) * (y - mean_y) for x, y in inlier_pairs
    ) / denominator
    if not (0.5 <= fitted_scale <= 2.0):
        fitted_scale = scale
    fitted_offset = mean_y - fitted_scale * mean_x
    return fitted_scale, fitted_offset, len(inlier_pairs)


# ----------------------------------------------------------------------
# Weighted monotonic matching (weighted LIS + Fenwick tree)
# ----------------------------------------------------------------------
class _MaxTree:
    def __init__(self, size: int) -> None:
        self.size = size
        self.tree = [0.0] * (size + 1)
        self.node = [-1] * (size + 1)

    def query(self, end: int) -> tuple[float, int]:
        """Best value among indices ``[0, end)``."""

        best_value = 0.0
        best_node = -1
        index = end
        while index > 0:
            if self.tree[index] >= best_value:
                best_value = self.tree[index]
                best_node = self.node[index]
            index -= index & -index
        return best_value, best_node

    def update(self, index: int, value: float, node: int) -> None:
        index += 1
        while index <= self.size:
            if value > self.tree[index]:
                self.tree[index] = value
                self.node[index] = node
            index += index & -index


def _match_cues(
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    offset: float,
    tolerance: float,
) -> list[tuple[int, int, float]]:
    """Match cues to speech onsets with a monotonic weighted subsequence."""

    if not cue_starts or not speech_starts:
        return []
    # nodes: (cue_index, speech_index, previous_node, total_weight, residual)
    nodes: list[tuple[int, int, int, float, float]] = []
    tree = _MaxTree(len(speech_starts))
    best_total = 0.0
    best_node = -1

    for cue_index, start in enumerate(cue_starts):
        predicted = scale * start + offset
        candidates = _nearest_starts(speech_starts, predicted, tolerance, limit=2)
        if not candidates:
            continue
        pending: list[tuple[int, float, float, float]] = []
        for speech_index, distance in candidates:
            previous_total, previous_node = tree.query(speech_index)
            weight = max(0.05, 1.0 - distance / max(0.001, tolerance))
            total = previous_total + weight
            pending.append((speech_index, distance, previous_node, total))
        for speech_index, distance, previous_node, total in pending:
            node_index = len(nodes)
            nodes.append(
                (cue_index, speech_index, previous_node, total, distance)
            )
            tree.update(speech_index, total, node_index)
            if total > best_total:
                best_total = total
                best_node = node_index

    pairs: list[tuple[int, int, float]] = []
    while best_node >= 0:
        cue_index, speech_index, previous_node, _, residual = nodes[best_node]
        pairs.append((cue_index, speech_index, residual))
        best_node = previous_node
    pairs.reverse()
    return pairs


def _model_quality(
    pairs: list[tuple[int, int, float]],
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    offset: float,
) -> float:
    if not pairs:
        return -1e9
    residuals = [
        speech_starts[speech_index] - (scale * cue_starts[cue_index] + offset)
        for cue_index, speech_index, _ in pairs
    ]
    return (
        len(pairs) * 10.0
        - _abs_percentile(residuals, 90)
        - abs(scale - 1.0) * 2.0
    )


def _refine(
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    offset: float,
    tolerance: float,
    allow_scale: bool = True,
) -> tuple[float, float, list[tuple[int, int, float]]]:
    pairs = _match_cues(cue_starts, speech_starts, scale, offset, tolerance)
    if not allow_scale:
        if pairs:
            offset = median(
                speech_starts[speech_index] - cue_starts[cue_index]
                for cue_index, speech_index, _ in pairs
            )
        pairs = _match_cues(
            cue_starts, speech_starts, 1.0, offset, tolerance
        )
        return 1.0, offset, pairs

    best_quality = _model_quality(
        pairs, cue_starts, speech_starts, scale, offset
    )
    for _ in range(4):
        if len(pairs) < 3:
            break
        fit = _fit_line(
            [(cue_starts[i], speech_starts[j]) for i, j, _ in pairs]
        )
        if fit is None:
            break
        new_scale, new_offset, _ = fit
        new_pairs = _match_cues(
            cue_starts, speech_starts, new_scale, new_offset, tolerance
        )
        if len(new_pairs) < 3:
            break
        quality = _model_quality(
            new_pairs, cue_starts, speech_starts, new_scale, new_offset
        )
        if quality <= best_quality + 1e-9:
            break
        best_quality = quality
        scale, offset, pairs = new_scale, new_offset, new_pairs
    return scale, offset, pairs


# ----------------------------------------------------------------------
# Piecewise residual repair
# ----------------------------------------------------------------------
def _find_splits(
    points: list[tuple[int, float]],
    threshold: float,
    min_count: int,
    max_splits: int,
) -> list[int]:
    if len(points) < min_count * 2:
        return []
    splits: list[int] = []
    blocks = [(0, len(points))]
    while len(splits) < max_splits:
        best: tuple[float, int, int, float] | None = None
        for block_index, (lo, hi) in enumerate(blocks):
            if hi - lo < min_count * 2:
                continue
            for cut in range(lo + min_count, hi - min_count + 1):
                if points[cut - 1][0] == points[cut][0]:
                    continue
                left = [value for _, value in points[lo:cut]]
                right = [value for _, value in points[cut:hi]]
                left_med = median(left)
                right_med = median(right)
                difference = abs(left_med - right_med)
                within = (
                    sum(abs(value - left_med) for value in left)
                    + sum(abs(value - right_med) for value in right)
                ) / max(1, hi - lo)
                if best is None or (difference, -within) > (
                    best[0],
                    -best[3],
                ):
                    best = (difference, cut, block_index, within)
        if best is None or best[0] < threshold:
            break
        _, cut, block_index, _within = best
        lo, hi = blocks[block_index]
        splits.append(points[cut][0])
        blocks[block_index : block_index + 1] = [(lo, cut), (cut, hi)]
    return sorted(set(splits))


def _cue_residuals(
    cue_starts: list[float],
    speech_starts: list[float],
    scale: float,
    offset: float,
    window: float = 4.0,
) -> list[tuple[int, float, float]]:
    """Nearest-onset residual for *every* cue, matched or not.

    Segment repair must see cues that the global model could not match at all
    (for example the second half of a film after a scene jump).  The residual
    list therefore uses a generous local window and also returns the matched
    speech start so the improvement can be measured after shifting.
    """

    result: list[tuple[int, float, float]] = []
    for cue_index, start in enumerate(cue_starts):
        predicted = scale * start + offset
        near = _nearest_starts(speech_starts, predicted, window, limit=1)
        if near:
            speech_start = speech_starts[near[0][0]]
            result.append((cue_index, speech_start - predicted, speech_start))
    return result


def _segment_shifts(
    residuals: list[tuple[int, float, float]],
    count: int,
    threshold: float,
    min_count: int,
    max_segments: int,
) -> list[tuple[int, int, float]]:
    if len(residuals) < min_count * 2:
        return []
    points = [(cue_index, residual) for cue_index, residual, _ in residuals]
    splits = _find_splits(points, threshold, min_count, max_segments)
    if not splits:
        return []
    boundaries = [0, *splits, count]
    result: list[tuple[int, int, float]] = []
    for first, last in zip(boundaries, boundaries[1:]):
        values = [
            residual
            for cue_index, residual, _ in residuals
            if first <= cue_index < last
        ]
        if len(values) < min_count:
            continue
        shift = median(values)
        if abs(shift) >= 0.10:
            result.append((first, last - 1, shift))
    return result


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def build_auto_plan(
    cues: list[Cue],
    segments: list[tuple[float, float]],
    options: AutoSyncOptions | None = None,
) -> AutoSyncPlan:
    """Build a complete automatic correction plan.

    ``segments`` are speech intervals ``(start, end)`` in media time, usually
    produced by the VAD in :mod:`captionsync.audio`.
    """

    options = options or AutoSyncOptions()
    count = len(cues)
    targets = [(cue.start, cue.end) for cue in cues]
    plan = AutoSyncPlan(targets=targets, total=count)
    if count == 0:
        plan.warnings.append("자막이 없습니다.")
        return plan
    if len(segments) == 0:
        plan.warnings.append(
            "음성 구간이 없어 자동 싱크를 계산할 수 없습니다. 미디어를 먼저 분석하세요."
        )
        return plan

    ordered_segments = sorted(
        (start, end) for start, end in segments if end > start
    )
    speech_starts = [start for start, end in ordered_segments]
    speech_ends = [end for start, end in ordered_segments]
    if len(speech_starts) < 2:
        plan.warnings.append("발화 시작점이 너무 적습니다.")
        return plan
    cue_starts = [cue.start for cue in cues]

    # 1. Pick the most plausible scale/offset hypothesis.  The scale prior is
    #    deliberately strong: a free slope fitted through per-line jitter
    #    always looks slightly better inside the sample, but makes the result
    #    worse on the next line.
    initial_offset, _votes = _histogram_offset(
        cue_starts, speech_starts, 1.0
    )
    initial_offset = _polish_offset(
        cue_starts, speech_starts, 1.0, initial_offset
    )
    initial_pairs = _match_cues(
        cue_starts, speech_starts, 1.0, initial_offset, 1.20
    )
    initial_p90 = _abs_percentile(
        [
            speech_starts[speech_index]
            - (cue_starts[cue_index] + initial_offset)
            for cue_index, speech_index, _ in initial_pairs
        ],
        90,
    )
    initial_half_gap = 0.0
    if len(initial_residuals := [
        speech_starts[speech_index]
        - (cue_starts[cue_index] + initial_offset)
        for cue_index, speech_index, _ in initial_pairs
    ]) >= 6:
        midpoint = len(initial_residuals) // 2
        initial_half_gap = abs(
            median(initial_residuals[:midpoint])
            - median(initial_residuals[midpoint:])
        )
    stable_unscaled = (
        len(initial_pairs) >= max(3, int(count * 0.75))
        and initial_p90 <= 0.65
        and initial_half_gap <= 0.20
    )

    best_scale = 1.0
    best_offset = initial_offset
    best_count = len(initial_pairs)
    best_quality = -1e9
    hypotheses = (1.0,) if stable_unscaled else _candidate_scales(cue_starts, speech_starts)
    for scale in hypotheses:
        offset, _votes = _histogram_offset(
            cue_starts, speech_starts, scale
        )
        offset = _polish_offset(cue_starts, speech_starts, scale, offset)
        hypothesis_pairs = _match_cues(
            cue_starts, speech_starts, scale, offset, 0.50
        )
        if len(hypothesis_pairs) < 3:
            continue
        residuals = [
            speech_starts[speech_index]
            - (scale * cue_starts[cue_index] + offset)
            for cue_index, speech_index, _ in hypothesis_pairs
        ]
        quality = (
            len(hypothesis_pairs) * 10.0
            - _abs_percentile(residuals, 90)
            - abs(scale - 1.0) * 20.0
        )
        if quality > best_quality:
            best_quality = quality
            best_count = len(hypothesis_pairs)
            best_scale = scale
            best_offset = offset

    if best_count < 3:
        plan.warnings.append(
            "자막과 음성 구간을 충분히 연결하지 못했습니다. "
            "다른 미디어/자막인지, 오디오 트랙이 맞는지 확인하세요."
        )
        return plan

    # A scale that only explains a small part of the file is usually fitting
    # one scene block rather than real drift.  Fall back to 1.0 and let the
    # piecewise repair handle the other block.
    if (
        abs(best_scale - 1.0) >= 0.0015
        and best_count < max(3, int(count * 0.80))
        and len(initial_pairs) >= 3
    ):
        best_scale = 1.0
        best_offset = initial_offset
        best_count = len(initial_pairs)

    # A hypothesis that is indistinguishable from "no drift" should stay at
    # exactly 1.0; this prevents tiny fitting errors from stretching the file.
    if abs(best_scale - 1.0) < 0.0015 or not options.fix_drift:
        best_scale = 1.0

    # 2. Robust affine fit on monotonic matches.  Free slope fitting is only
    #    allowed when a non-trivial scale hypothesis actually won.
    scale, offset, pairs = _refine(
        cue_starts,
        speech_starts,
        best_scale,
        best_offset,
        options.match_tolerance,
        allow_scale=options.fix_drift and abs(best_scale - 1.0) >= 0.0015,
    )
    if len(pairs) < 3:
        plan.warnings.append("신뢰할 수 있는 음성 매칭을 찾지 못했습니다.")
        return plan

    if not options.fix_drift:
        scale = 1.0
        offset = median(
            speech_starts[speech_index] - cue_starts[cue_index]
            for cue_index, speech_index, _ in pairs
        )
        pairs = _match_cues(
            cue_starts, speech_starts, scale, offset, options.match_tolerance
        )

    # 3. Snap a near-common FPS ratio and report it.  Only snap when the fit
    #    is already extremely close, so arbitrary editing drift is preserved.
    fps_pair: tuple[float, float] | None = None
    if options.detect_fps and abs(scale - 1.0) >= 0.0015:
        for sub_fps, video_fps in _FPS_PAIRS:
            ratio = sub_fps / video_fps
            if abs(scale - ratio) / ratio <= 0.004:
                fps_pair = (sub_fps, video_fps)
                scale = ratio
                offset = median(
                    speech_starts[speech_index]
                    - scale * cue_starts[cue_index]
                    for cue_index, speech_index, _ in pairs
                )
                break

    # 4. Re-match and quantify the fit.
    pairs = _match_cues(
        cue_starts, speech_starts, scale, offset, options.match_tolerance
    )
    residuals = [
        speech_starts[speech_index]
        - (scale * cue_starts[cue_index] + offset)
        for cue_index, speech_index, _ in pairs
    ]
    inliers = sum(1 for value in residuals if abs(value) <= 0.40)

    # 5. Apply the affine model to every cue.
    transformed = [
        (
            max(0.0, scale * cue.start + offset),
            max(0.0, scale * cue.end + offset),
        )
        for cue in cues
    ]

    # 6. Repair residual steps (a scene that is offset as a block).
    segment_shifts: list[tuple[int, int, float]] = []
    if options.fix_segments and count >= 6:
        all_residuals = _cue_residuals(
            cue_starts, speech_starts, scale, offset, window=4.0
        )
        before = _abs_percentile(
            [residual for _, residual, _ in all_residuals], 90
        )
        candidate_shifts = (
            _segment_shifts(
                all_residuals,
                count,
                options.segment_threshold,
                options.min_segment_matches,
                options.max_segments,
            )
            if before >= 0.70
            else []
        )
        if candidate_shifts:
            adjusted = [list(pair) for pair in transformed]
            for first, last, shift in candidate_shifts:
                for index in range(first, min(last + 1, count)):
                    adjusted[index][0] = max(0.0, adjusted[index][0] + shift)
                    adjusted[index][1] = max(0.0, adjusted[index][1] + shift)
            after_values = [
                abs(speech_start - adjusted[cue_index][0])
                for cue_index, _, speech_start in all_residuals
            ]
            after = _abs_percentile(after_values, 90)
            if after + 0.08 < before:
                segment_shifts = candidate_shifts
                transformed = [tuple(pair) for pair in adjusted]

    # 7. Snap cue boundaries to nearby speech starts/ends.
    boundary_snaps = 0
    if options.snap_boundaries:
        for index, cue in enumerate(cues):
            start, end = transformed[index]
            changed = False
            segment_index: int | None = None
            near_start = _nearest_starts(
                speech_starts, start, options.snap_window, limit=1
            )
            if near_start:
                segment_index = near_start[0][0]
                new_start = speech_starts[segment_index]
                if new_start < end - MIN_DURATION:
                    start = max(0.0, new_start)
                    changed = True
            else:
                # The start may already be inside a speech segment.  Keep it
                # there, but remember the segment so its end can be snapped.
                insert_at = bisect_left(speech_starts, start)
                for candidate in (insert_at - 1, insert_at):
                    if 0 <= candidate < len(ordered_segments):
                        seg_start, seg_end = ordered_segments[candidate]
                        if seg_start <= start <= seg_end:
                            segment_index = candidate
                            break
            if segment_index is not None:
                target_end = speech_ends[segment_index]
                if (
                    abs(end - target_end) <= options.snap_end_window
                    and target_end > start + MIN_DURATION
                ):
                    end = target_end
                    changed = True
            else:
                near_end = _nearest_starts(
                    speech_ends, end, 0.60, limit=1
                )
                if near_end:
                    target_end = speech_ends[near_end[0][0]]
                    if target_end > start + MIN_DURATION:
                        end = target_end
                        changed = True
            if end < start + MIN_DURATION:
                end = start + MIN_DURATION
            if changed:
                boundary_snaps += 1
            transformed[index] = (start, end)

    # Once nearly every cue has been snapped to the correct speech interval,
    # a small ICP offset is no longer meaningful (it is usually just the
    # average of per-line jitter).  Keep the user-facing model neutral rather
    # than reporting a false global shift.
    if (
        scale == 1.0
        and abs(offset) < 0.8
        and boundary_snaps >= max(3, int(count * 0.8))
    ):
        offset = 0.0

    # 8. Final metrics and confidence.
    changed = 0
    for cue, (start, end) in zip(cues, transformed):
        if abs(cue.start - start) > 0.005 or abs(cue.end - end) > 0.005:
            changed += 1

    matched = len(pairs)
    match_ratio = matched / max(1, count)
    inlier_ratio = inliers / max(1, matched)
    residual_median = median(abs(value) for value in residuals) if residuals else 0.0
    residual_p90 = _abs_percentile(residuals, 90)
    confidence = (
        0.45 * match_ratio
        + 0.35 * inlier_ratio
        + 0.20 * max(0.0, 1.0 - residual_p90 / 0.60)
    )
    confidence = max(0.0, min(1.0, confidence))

    plan.targets = transformed
    plan.scale = scale
    plan.offset = offset
    plan.fps = fps_pair
    plan.segments = segment_shifts
    plan.boundary_snaps = boundary_snaps
    plan.matched = matched
    plan.inliers = inliers
    plan.confidence = confidence
    plan.residual_median = residual_median
    plan.residual_p90 = residual_p90
    plan.changed = changed

    if len(segments) < 3:
        plan.warnings.append("발화 구간이 적어 결과가 불안정할 수 있습니다.")
    if matched < max(3, int(count * 0.25)):
        plan.warnings.append(
            "매칭된 자막이 적습니다. 배경음이나 겹치는 대사를 확인하세요."
        )
    if confidence < options.min_confidence:
        plan.warnings.append(
            "신뢰도가 낮습니다. 적용 전에 타임라인을 확인하는 것을 권장합니다."
        )
    if changed == 0:
        plan.warnings.append("이미 음성과 잘 맞아 수정할 내용이 없습니다.")
    if not fps_pair and abs(scale - 1.0) >= 0.005:
        plan.warnings.append(
            "일반적인 FPS 비율과 다른 드리프트로 판단해 임의 비율로 보정했습니다."
        )
    return plan


def apply_auto_plan(cues: list[Cue], plan: AutoSyncPlan) -> int:
    """Apply a plan in place; returns the number of changed cues."""

    changed = 0
    for cue, (start, end) in zip(cues, plan.targets):
        new_start = max(0.0, start)
        new_end = max(new_start + MIN_DURATION, end)
        if abs(cue.start - new_start) > 1e-9 or abs(cue.end - new_end) > 1e-9:
            changed += 1
        cue.start = new_start
        cue.end = new_end
    return changed
