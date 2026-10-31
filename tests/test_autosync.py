"""Tests for the one-click automatic sync engine."""

from __future__ import annotations

import unittest

from captionsync.autosync import (
    AutoSyncOptions,
    apply_auto_plan,
    build_auto_plan,
)
from captionsync.models import Cue


def speech_segments(count: int = 12, start: float = 2.0, gap: float = 3.0,
                    duration: float = 2.0) -> list[tuple[float, float]]:
    return [
        (start + index * gap, start + index * gap + duration)
        for index in range(count)
    ]


def cues_from(
    segments: list[tuple[float, float]],
    *,
    scale: float = 1.0,
    offset: float = 0.0,
    duration_scale: float = 1.0,
    per_cue: list[float] | None = None,
) -> list[Cue]:
    cues: list[Cue] = []
    for index, (start, end) in enumerate(segments):
        jitter = per_cue[index] if per_cue and index < len(per_cue) else 0.0
        cue_start = (start - offset) / scale + jitter
        cue_end = (end - offset) / scale + jitter * duration_scale
        cues.append(Cue(cue_start, cue_end, f"line {index + 1}"))
    return cues


class AutoSyncTests(unittest.TestCase):
    def test_global_shift(self) -> None:
        segments = speech_segments()
        cues = cues_from(segments, offset=-2.5)  # subtitles are 2.5 s late

        plan = build_auto_plan(cues, segments)

        self.assertAlmostEqual(plan.offset, -2.5, delta=0.05)
        self.assertAlmostEqual(plan.scale, 1.0, delta=0.002)
        self.assertEqual(plan.matched, len(cues))
        self.assertGreater(plan.confidence, 0.9)
        for cue, (expected, _) in zip(plan.targets, segments):
            self.assertAlmostEqual(cue[0], expected, delta=0.06)

    def test_fps_drift_is_snapped(self) -> None:
        segments = speech_segments()
        ratio = 25.0 / 23.976
        cues = cues_from(segments, scale=ratio)

        plan = build_auto_plan(cues, segments)

        self.assertAlmostEqual(plan.scale, ratio, delta=0.001)
        self.assertIsNotNone(plan.fps)
        self.assertEqual(plan.fps, (25.0, 23.976))
        self.assertGreater(plan.confidence, 0.8)
        for cue, (expected, _) in zip(plan.targets, segments):
            self.assertAlmostEqual(cue[0], expected, delta=0.12)

    def test_arbitrary_drift_is_estimated(self) -> None:
        segments = speech_segments(count=16, gap=4.0)
        ratio = 1.012
        cues = cues_from(segments, scale=ratio)

        plan = build_auto_plan(cues, segments)

        self.assertAlmostEqual(plan.scale, ratio, delta=0.003)
        self.assertIsNone(plan.fps)
        self.assertGreater(plan.confidence, 0.6)
        for cue, (expected, _) in zip(plan.targets, segments):
            self.assertAlmostEqual(cue[0], expected, delta=0.35)

    def test_segment_shift_is_repaired(self) -> None:
        segments = speech_segments(count=14)
        cues = cues_from(segments)
        for index in range(7, len(cues)):
            cues[index].start -= 1.4
            cues[index].end -= 1.4

        plan = build_auto_plan(cues, segments)

        self.assertTrue(plan.segments, "a block shift should be detected")
        for cue, (expected, _) in zip(plan.targets, segments):
            self.assertAlmostEqual(cue[0], expected, delta=0.15)

    def test_per_line_jitter_is_snapped(self) -> None:
        segments = speech_segments(count=10)
        jitter = [0.30, -0.28, 0.19, -0.33, 0.22, -0.17, 0.29, -0.24, 0.18, -0.31]
        cues = cues_from(segments, per_cue=jitter)

        plan = build_auto_plan(cues, segments)

        self.assertAlmostEqual(plan.scale, 1.0, delta=0.002)
        self.assertLess(abs(plan.offset), 0.25)
        for cue, (expected, _) in zip(plan.targets, segments):
            self.assertAlmostEqual(cue[0], expected, delta=0.06)
            self.assertGreater(cue[1], cue[0])

    def test_missing_cues_do_not_break_matching(self) -> None:
        segments = speech_segments(count=14)
        cues = cues_from(segments, offset=-1.8)
        removed = [3, 8, 11]
        kept = [index for index in range(len(segments)) if index not in removed]
        cues = [cue for index, cue in enumerate(cues) if index not in removed]

        plan = build_auto_plan(cues, segments)

        self.assertEqual(plan.total, len(cues))
        self.assertGreaterEqual(plan.matched, len(cues) - 1)
        self.assertGreater(plan.confidence, 0.6)
        for cue, index in zip(plan.targets, kept):
            self.assertAlmostEqual(cue[0], segments[index][0], delta=0.2)

    def test_no_speech_is_safe(self) -> None:
        cues = cues_from(speech_segments())
        plan = build_auto_plan(cues, [])

        self.assertEqual(plan.changed, 0)
        self.assertEqual(plan.confidence, 0.0)
        self.assertTrue(plan.warnings)
        self.assertEqual(plan.targets[0], (cues[0].start, cues[0].end))

    def test_already_synced_is_left_alone(self) -> None:
        segments = speech_segments(count=8)
        cues = [Cue(start, end, f"line {index}") for index, (start, end) in enumerate(segments)]

        plan = build_auto_plan(cues, segments)

        self.assertAlmostEqual(plan.offset, 0.0, delta=0.03)
        self.assertAlmostEqual(plan.scale, 1.0, delta=0.002)
        self.assertEqual(plan.segments, [])
        self.assertEqual(plan.changed, 0)

    def test_options_can_disable_repairs(self) -> None:
        segments = speech_segments(count=10)
        jitter = [0.30, -0.28, 0.19, -0.33, 0.22, -0.17, 0.29, -0.24, 0.18, -0.31]
        cues = cues_from(segments, per_cue=jitter)

        plan = build_auto_plan(
            cues,
            segments,
            AutoSyncOptions(snap_boundaries=False, fix_segments=False),
        )

        self.assertEqual(plan.boundary_snaps, 0)
        self.assertEqual(plan.segments, [])

    def test_apply_plan_updates_cues(self) -> None:
        segments = speech_segments(count=6)
        cues = cues_from(segments, offset=-1.2)
        plan = build_auto_plan(cues, segments)

        changed = apply_auto_plan(cues, plan)

        self.assertEqual(changed, len(cues))
        for cue, (expected, end) in zip(cues, segments):
            self.assertAlmostEqual(cue.start, expected, delta=0.06)
            self.assertAlmostEqual(cue.end, end, delta=0.06)


if __name__ == "__main__":
    unittest.main()
