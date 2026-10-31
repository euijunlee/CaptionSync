"""Timing correction algorithms used by the five workflows.

Every function mutates the cue list in place (except the read-only helpers),
so the UI can wrap a call in a single undo transaction.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from .models import MIN_DURATION, Cue, median

__all__ = [
    "shift_all",
    "map_two_points",
    "linear_parameters",
    "scale_fps",
    "shift_from_index",
    "shift_index_range",
    "shift_from_time",
    "set_cue_times",
    "nearest_start",
    "nearest_end",
    "auto_align",
    "suggest_offset",
    "speech_coverage",
]


def _duration(cue: Cue) -> float:
    return max(MIN_DURATION, cue.end - cue.start)


def _apply(cue: Cue, start: float, end: float) -> None:
    if not math.isfinite(start) or not math.isfinite(end):
        return
    start = max(0.0, start)
    if end < start + MIN_DURATION:
        end = start + MIN_DURATION
    cue.start = start
    cue.end = end


def shift_all(cues: list[Cue], delta: float) -> None:
    """Move every cue by the same number of seconds (전체 일괄 이동)."""

    if not math.isfinite(delta):
        raise ValueError("이동 시간이 올바르지 않습니다.")
    for cue in cues:
        duration = _duration(cue)
        start = max(0.0, cue.start + delta)
        _apply(cue, start, start + duration)


def map_two_points(
    cues: list[Cue],
    src_a: float,
    dst_a: float,
    src_b: float,
    dst_b: float,
) -> float:
    """Map cue times through two anchor pairs (시간 비율 / 드리프트 보정).

    ``src`` values are the current subtitle times and ``dst`` values are the
    times where those lines should actually appear.  Returns the scale factor.
    """

    span = src_b - src_a
    if abs(span) < 0.05:
        raise ValueError("두 기준 시각이 너무 가깝습니다. 간격을 0.05초 이상 두세요.")
    factor = (dst_b - dst_a) / span
    if not math.isfinite(factor) or factor <= 0.01 or factor > 20.0:
        raise ValueError("보정 비율이 허용 범위를 벗어났습니다.")

    for cue in cues:
        duration = _duration(cue)
        start = max(0.0, dst_a + (cue.start - src_a) * factor)
        end = max(0.0, dst_a + (cue.end - src_a) * factor)
        if end <= start + MIN_DURATION:
            end = start + max(MIN_DURATION, duration * factor)
        _apply(cue, start, end)
    return factor


def linear_parameters(
    src_a: float, dst_a: float, src_b: float, dst_b: float
) -> tuple[float, float]:
    """Return ``(factor, offset)`` for ``dst = src * factor + offset``."""

    span = src_b - src_a
    if abs(span) < 0.05:
        raise ValueError("두 기준 시각이 너무 가깝습니다.")
    factor = (dst_b - dst_a) / span
    offset = dst_a - src_a * factor
    return factor, offset


def scale_fps(
    cues: list[Cue],
    subtitle_fps: float,
    video_fps: float,
    anchor: float | None = None,
) -> float:
    """Correct FPS mismatches (e.g. 23.976 -> 25).

    ``subtitle_fps`` is the frame rate the subtitles were authored for and
    ``video_fps`` is the actual video rate.  A PAL speed-up (23.976 -> 25)
    therefore shrinks the timings by 23.976 / 25.
    """

    if not (0.1 <= subtitle_fps <= 1000.0) or not (0.1 <= video_fps <= 1000.0):
        raise ValueError("FPS 값이 올바르지 않습니다.")
    factor = subtitle_fps / video_fps
    if anchor is None:
        anchor = 0.0
    anchor = max(0.0, anchor)
    for cue in cues:
        duration = _duration(cue) * factor
        start = max(0.0, anchor + (cue.start - anchor) * factor)
        end = max(0.0, anchor + (cue.end - anchor) * factor)
        if end <= start + MIN_DURATION:
            end = start + max(MIN_DURATION, duration)
        _apply(cue, start, end)
    return factor


def shift_from_index(cues: list[Cue], index: int, delta: float) -> int:
    """Shift cue ``index`` and all later cues (특정 장면부터 개별 보정)."""

    if not cues:
        return 0
    index = max(0, min(index, len(cues) - 1))
    count = 0
    for cue in cues[index:]:
        duration = _duration(cue)
        start = max(0.0, cue.start + delta)
        _apply(cue, start, start + duration)
        count += 1
    return count


def shift_index_range(
    cues: list[Cue], first: int, last: int, delta: float
) -> int:
    """Shift an inclusive 0-based cue range."""

    if not cues:
        return 0
    first = max(0, min(first, len(cues) - 1))
    last = max(first, min(last, len(cues) - 1))
    count = 0
    for cue in cues[first : last + 1]:
        duration = _duration(cue)
        start = max(0.0, cue.start + delta)
        _apply(cue, start, start + duration)
        count += 1
    return count


def shift_from_time(cues: list[Cue], threshold: float, delta: float) -> int:
    """Shift every cue whose start is at or after ``threshold``."""

    count = 0
    for cue in cues:
        if cue.start + 1e-9 >= threshold:
            duration = _duration(cue)
            start = max(0.0, cue.start + delta)
            _apply(cue, start, start + duration)
            count += 1
    return count


def set_cue_times(
    cue: Cue, start: float | None = None, end: float | None = None
) -> None:
    new_start = cue.start if start is None else start
    new_end = cue.end if end is None else end
    if end is not None and start is None and new_end <= cue.start + MIN_DURATION:
        new_end = cue.start + MIN_DURATION
    _apply(cue, new_start, new_end)


def nearest_start(segments: list[tuple[float, float]], seconds: float) -> float | None:
    if not segments:
        return None
    return min((start for start, _ in segments), key=lambda value: abs(value - seconds))


def nearest_end(segments: list[tuple[float, float]], seconds: float) -> float | None:
    if not segments:
        return None
    return min((end for _, end in segments), key=lambda value: abs(value - seconds))


def _nearest_segment(
    segments: list[tuple[float, float]], seconds: float, window: float
) -> int | None:
    best_index = None
    best_distance = window + 1e-9
    for index, (start, end) in enumerate(segments):
        distance = abs(start - seconds)
        if distance <= best_distance and end > start:
            best_distance = distance
            best_index = index
    return best_index


def auto_align(
    cues: list[Cue],
    segments: list[tuple[float, float]],
    indices: list[int] | None = None,
    window: float = 1.5,
    snap_end: bool = True,
    reuse_neighbour_offset: bool = False,
) -> dict:
    """Snap cue boundaries onto detected speech segments (음성 구간 정렬).

    A cue start is matched to the closest speech onset within ``window``
    seconds.  The end is matched to the nearest speech offset (or to the end of
    the matched segment when that is close), which fixes display durations
    that are too long, too short or simply wrong.
    """

    if not segments:
        return {"aligned": 0, "skipped": 0, "median_shift": 0.0, "matched": []}

    targets = list(range(len(cues))) if indices is None else indices
    aligned = 0
    skipped = 0
    shifts: list[float] = []
    matched: list[int] = []

    for index in targets:
        if index < 0 or index >= len(cues):
            continue
        cue = cues[index]
        segment_index = _nearest_segment(segments, cue.start, window)
        if segment_index is None:
            skipped += 1
            continue

        seg_start, seg_end = segments[segment_index]
        new_start = seg_start
        new_end = cue.end
        shift = new_start - cue.start
        duration = max(0.2, _duration(cue))

        if snap_end:
            if abs(seg_end - cue.end) <= window * 1.8 and seg_end > new_start + MIN_DURATION:
                new_end = seg_end
            else:
                candidates = [
                    (abs(end - cue.end), end)
                    for _, end in segments
                    if abs(end - cue.end) <= window and end > new_start + MIN_DURATION
                ]
                if candidates:
                    new_end = min(candidates)[1]
                else:
                    new_end = new_start + duration
        else:
            new_end = new_start + duration

        if new_end <= new_start + MIN_DURATION:
            new_end = new_start + max(0.15, min(duration, 12.0))

        _apply(cue, new_start, new_end)
        shifts.append(shift)
        matched.append(index)
        aligned += 1

    median_shift = median(shifts) if shifts else 0.0

    if reuse_neighbour_offset and median_shift and skipped:
        for index in targets:
            if index in matched or index < 0 or index >= len(cues):
                continue
            cue = cues[index]
            duration = _duration(cue)
            start = max(0.0, cue.start + median_shift)
            _apply(cue, start, start + duration)
        skipped = 0

    return {
        "aligned": aligned,
        "skipped": skipped,
        "median_shift": median_shift,
        "matched": matched,
    }


def suggest_offset(
    cues: list[Cue], segments: list[tuple[float, float]], window: float = 5.0
) -> tuple[float | None, int]:
    """Estimate a global offset from subtitle starts vs. speech onsets.

    Every cue votes for every candidate offset inside ``window``; the offset
    with the most consistent votes wins.  This is much more reliable than
    matching each cue to only its nearest onset, because a uniformly late file
    would otherwise lock onto the *next* line's speech.
    """

    if not cues or not segments:
        return None, 0
    starts = sorted(start for start, _ in segments)
    if not starts:
        return None, 0

    buckets: dict[int, list[float]] = {}
    for cue in cues:
        lo = bisect_left(starts, cue.start - window)
        hi = bisect_right(starts, cue.start + window)
        for start in starts[lo:hi]:
            delta = start - cue.start
            key = int(round(delta / 0.1))
            buckets.setdefault(key, []).append(delta)

    if not buckets:
        return None, 0

    best_key, best = max(
        buckets.items(),
        key=lambda item: (len(item[1]), -abs(item[0])),
    )
    votes = len(best)
    if votes < max(3, int(len(cues) * 0.2)):
        return None, votes

    nearby: list[float] = []
    for key in (best_key - 1, best_key, best_key + 1):
        nearby.extend(buckets.get(key, []))
    return median(nearby), votes


def speech_coverage(
    cues: list[Cue], segments: list[tuple[float, float]], window: float = 1.5
) -> float:
    """Fraction of cues with a speech onset close to their start."""

    if not cues or not segments:
        return 0.0
    hits = 0
    for cue in cues:
        if _nearest_segment(segments, cue.start, window) is not None:
            hits += 1
    return hits / len(cues)
