"""Unit tests for the CaptionSync timing engine (no GUI required)."""

from __future__ import annotations

import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from captionsync.audio import analyze_wav, detect_speech, prepare_media
from captionsync.formats import SubtitleError, load_subtitle, save_subtitle, serialize_document
from captionsync.models import Cue, Document, History, clone_cues
from captionsync.timecode import (
    format_ass_time,
    format_clock,
    format_srt_time,
    format_vtt_time,
    parse_offset,
    parse_timecode,
)
from captionsync.transforms import (
    auto_align,
    map_two_points,
    nearest_end,
    nearest_start,
    scale_fps,
    set_cue_times,
    shift_all,
    shift_from_index,
    shift_from_time,
    shift_index_range,
    suggest_offset,
)


class TimecodeTests(unittest.TestCase):
    def test_parse_variants(self):
        cases = {
            "00:01:02,500": 62.5,
            "00:01:02.500": 62.5,
            "1:02.5": 62.5,
            "62.5": 62.5,
            "00:00:00,001": 0.001,
            "250ms": 0.25,
            "1.5s": 1.5,
            "+250ms": 0.25,
            "-3": -3.0,
            "-00:00:02,250": -2.25,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertAlmostEqual(parse_timecode(raw), expected, places=4)

    def test_parse_invalid(self):
        for raw in ("", "abc", "1:2:3:4"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_timecode(raw)

    def test_formats(self):
        self.assertEqual(format_srt_time(3725.456), "01:02:05,456")
        self.assertEqual(format_vtt_time(3725.456), "01:02:05.456")
        self.assertEqual(format_ass_time(3725.456), "1:02:05.46")
        self.assertEqual(format_clock(3725.456), "01:02:05.456")
        self.assertEqual(format_ass_time(-1), "0:00:00.00")

    def test_parse_offset(self):
        self.assertAlmostEqual(parse_offset("-3"), -3.0)
        self.assertAlmostEqual(parse_offset("+0:00:01.500"), 1.5)


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_srt_roundtrip(self):
        path = self.dir / "sample.srt"
        path.write_bytes(
            "\ufeff1\r\n00:00:01,000 --> 00:00:03,500\r\n첫 번째 자막\r\n둘째 줄\r\n\r\n"
            "2\r\n00:00:04,000 --> 00:00:06,000\r\nSecond line\r\n".encode("utf-8")
        )
        doc = load_subtitle(path)
        self.assertEqual(doc.kind, "srt")
        self.assertEqual(len(doc.cues), 2)
        self.assertEqual(doc.cues[0].start, 1.0)
        self.assertEqual(doc.cues[0].end, 3.5)
        self.assertEqual(doc.cues[0].text, "첫 번째 자막\n둘째 줄")
        self.assertEqual(doc.encoding, "utf-8-sig")

        out = self.dir / "out.srt"
        save_subtitle(doc, out)
        raw = out.read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        again = load_subtitle(out)
        self.assertEqual(
            [(c.start, c.end, c.text) for c in again.cues],
            [(c.start, c.end, c.text) for c in doc.cues],
        )

    def test_vtt_identifiers_and_settings(self):
        path = self.dir / "sample.vtt"
        path.write_text(
            "WEBVTT - Demo\nKind: captions\n\n"
            "NOTE this is a note\n\n"
            "intro\n00:00:01.000 --> 00:00:02.500 align:start position:10%\n"
            "Hello <i>world</i>\n\n"
            "00:00:03.000 --> 00:00:04.000\nSecond\n",
            encoding="utf-8",
        )
        doc = load_subtitle(path)
        self.assertEqual(doc.kind, "vtt")
        self.assertEqual(len(doc.cues), 2)
        self.assertEqual(doc.cues[0].extra["vtt_identifier"], "intro")
        self.assertEqual(doc.cues[0].extra["vtt_settings"], "align:start position:10%")
        self.assertEqual(doc.meta["vtt_header"][0], "WEBVTT - Demo")
        self.assertEqual(len(doc.meta["vtt_blocks"]), 1)

        text = serialize_document(doc)
        self.assertIn("WEBVTT - Demo", text)
        self.assertIn("NOTE this is a note", text)
        self.assertIn("align:start position:10%", text)
        self.assertIn("Hello <i>world</i>", text)

    def test_ass_preserves_fields_and_extra_lines(self):
        path = self.dir / "sample.ass"
        path.write_text(
            "[Script Info]\nScriptType: v4.00+\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Comment: 0,0:00:00.00,0:00:00.00,Default,,0,0,0,,note\n"
            "Dialogue: 0,0:00:01.00,0:00:03.50,Default,Narrator,0,0,0,,Hello, {\\b1}world{\\b0}\\NSecond line\n"
            "Dialogue: 1,0:00:04.00,0:00:05.00,Default,,0,0,0,,Second\n",
            encoding="utf-8",
        )
        doc = load_subtitle(path)
        self.assertEqual(len(doc.cues), 2)
        cue = doc.cues[0]
        self.assertAlmostEqual(cue.start, 1.0)
        self.assertEqual(cue.text, "Hello, {\\b1}world{\\b0}\\NSecond line")
        self.assertEqual(cue.extra["ass"]["Name"], "Narrator")
        self.assertIn("Comment:", "\n".join(doc.meta["ass_extra"]))

        cue.start = 2.0
        cue.end = 4.25
        text = serialize_document(doc)
        self.assertIn("Dialogue: 0,0:00:02.00,0:00:04.25,Default,Narrator,0,0,0,,", text)
        self.assertIn("Comment:", text)
        self.assertIn("Hello, {\\b1}world{\\b0}\\NSecond line", text)

        out = self.dir / "out.ass"
        save_subtitle(doc, out)
        again = load_subtitle(out)
        self.assertEqual(len(again.cues), 2)
        self.assertEqual(again.cues[0].extra["ass"]["Style"], "Default")
        self.assertEqual(again.cues[0].text, cue.text)

    def test_cp949_fallback(self):
        path = self.dir / "korean.srt"
        path.write_bytes(
            "1\n00:00:01,000 --> 00:00:02,000\n한글 자막\n".encode("cp949")
        )
        doc = load_subtitle(path)
        self.assertEqual(doc.encoding, "cp949")
        self.assertEqual(doc.cues[0].text, "한글 자막")

        doc.cues[0].text = "한글 자막 😀"
        enc, warning = save_subtitle(doc, self.dir / "out.srt")
        self.assertEqual(enc, "utf-8")
        self.assertIsNotNone(warning)
        self.assertEqual(load_subtitle(self.dir / "out.srt").cues[0].text, "한글 자막 😀")

    def test_empty_file_raises(self):
        path = self.dir / "empty.srt"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(SubtitleError):
            load_subtitle(path)


class TransformTests(unittest.TestCase):
    def _cues(self):
        return [
            Cue(10.0, 12.0, "a"),
            Cue(20.0, 22.0, "b"),
            Cue(30.0, 32.0, "c"),
            Cue(40.0, 42.0, "d"),
        ]

    def test_shift_all_preserves_duration_and_clamps(self):
        cues = self._cues()
        shift_all(cues, -15.0)
        self.assertEqual(cues[0].start, 0.0)
        self.assertAlmostEqual(cues[0].duration, 2.0)
        self.assertAlmostEqual(cues[1].start, 5.0)
        self.assertAlmostEqual(cues[3].start, 25.0)

    def test_two_point_mapping(self):
        cues = self._cues()
        factor = map_two_points(cues, 0.0, 0.0, 100.0, 125.0)
        self.assertAlmostEqual(factor, 1.25)
        self.assertAlmostEqual(cues[0].start, 12.5)
        self.assertAlmostEqual(cues[2].start, 37.5)
        self.assertAlmostEqual(cues[2].end, 40.0)

    def test_two_point_requires_span(self):
        with self.assertRaises(ValueError):
            map_two_points(self._cues(), 10.0, 0.0, 10.01, 1.0)

    def test_fps_conversion(self):
        cues = self._cues()
        factor = scale_fps(cues, 23.976, 25.0)
        self.assertAlmostEqual(factor, 23.976 / 25.0, places=6)
        self.assertAlmostEqual(cues[0].start, 10.0 * factor, places=5)
        self.assertAlmostEqual(cues[0].end, 12.0 * factor, places=5)

    def test_fps_anchor(self):
        cues = [Cue(10.0, 12.0)]
        scale_fps(cues, 25.0, 23.976, anchor=10.0)
        self.assertAlmostEqual(cues[0].start, 10.0)
        self.assertAlmostEqual(cues[0].end, 10.0 + 2.0 * (25.0 / 23.976))

    def test_shift_from_index(self):
        cues = self._cues()
        count = shift_from_index(cues, 2, 1.5)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(cues[1].start, 20.0)
        self.assertAlmostEqual(cues[2].start, 31.5)
        self.assertAlmostEqual(cues[3].start, 41.5)

    def test_shift_range_inclusive(self):
        cues = self._cues()
        count = shift_index_range(cues, 1, 2, -2.0)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(cues[0].start, 10.0)
        self.assertAlmostEqual(cues[1].start, 18.0)
        self.assertAlmostEqual(cues[2].start, 28.0)
        self.assertAlmostEqual(cues[3].start, 40.0)

    def test_shift_from_time(self):
        cues = self._cues()
        count = shift_from_time(cues, 30.0, 3.0)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(cues[2].start, 33.0)
        self.assertAlmostEqual(cues[3].start, 43.0)

    def test_set_cue_times(self):
        cue = Cue(10.0, 12.0)
        set_cue_times(cue, start=11.99)
        self.assertAlmostEqual(cue.start, 11.99)
        self.assertGreaterEqual(cue.end, cue.start + 0.04)
        set_cue_times(cue, end=5.0)
        self.assertGreaterEqual(cue.end, cue.start + 0.04)

    def test_auto_align(self):
        cues = [Cue(9.7, 13.0), Cue(19.6, 23.5), Cue(30.4, 31.0)]
        segments = [(10.0, 12.0), (20.0, 22.0), (30.0, 32.0)]
        stats = auto_align(cues, segments, window=1.0, snap_end=True)
        self.assertEqual(stats["aligned"], 3)
        self.assertEqual(stats["skipped"], 0)
        self.assertAlmostEqual(cues[0].start, 10.0)
        self.assertAlmostEqual(cues[0].end, 12.0)
        self.assertAlmostEqual(cues[1].start, 20.0)
        self.assertAlmostEqual(cues[1].end, 22.0)
        self.assertAlmostEqual(cues[2].start, 30.0)
        self.assertAlmostEqual(cues[2].end, 32.0)

    def test_auto_align_skips_far_cues(self):
        cues = [Cue(50.0, 52.0)]
        stats = auto_align(cues, [(10.0, 12.0)], window=1.0)
        self.assertEqual(stats["aligned"], 0)
        self.assertEqual(cues[0].start, 50.0)

    def test_suggest_offset(self):
        cues = [Cue(13.0, 15.0), Cue(23.0, 25.0), Cue(33.0, 35.0)]
        segments = [(10.0, 12.0), (20.0, 22.0), (30.0, 32.0)]
        value, matches = suggest_offset(cues, segments)
        self.assertAlmostEqual(value, -3.0)
        self.assertEqual(matches, 3)
        self.assertEqual(nearest_start(segments, 21.0), 20.0)
        self.assertEqual(nearest_end(segments, 31.5), 32.0)

    def test_history_undo_redo(self):
        doc = Document(cues=[Cue(1.0, 2.0, "a")])
        history = History()
        before = clone_cues(doc.cues)
        doc.cues[0].start = 5.0
        history.push_snapshot("test", before)
        self.assertTrue(history.can_undo)

        current = clone_cues(doc.cues)
        label, restored = history.undo(current)
        self.assertEqual(label, "test")
        self.assertEqual(restored[0].start, 1.0)
        self.assertTrue(history.can_redo)
        _label, redone = history.redo(restored)
        self.assertEqual(redone[0].start, 5.0)


class AudioTests(unittest.TestCase):
    def _make_wav(self, path: Path, duration: float = 6.0) -> None:
        rate = 16000
        frames = bytearray()
        speech = [(0.5, 1.5), (2.5, 3.6), (4.5, 5.4)]
        for index in range(int(duration * rate)):
            t = index / rate
            value = 0.0
            for start, end in speech:
                if start <= t <= end:
                    envelope = min(1.0, (t - start) * 20, (end - t) * 20)
                    value = (
                        0.45 * math.sin(2 * math.pi * 220 * t)
                        + 0.20 * math.sin(2 * math.pi * 440 * t)
                    ) * max(0.0, envelope)
                    break
            frames.extend(struct.pack("<h", int(value * 32767)))
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(bytes(frames))

    def test_analyze_and_detect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.wav"
            self._make_wav(path)
            analysis = analyze_wav(path)
            self.assertAlmostEqual(analysis.duration, 6.0, places=1)
            self.assertGreater(max(analysis.envelope), 0.2)
            self.assertEqual(len(analysis.speech), 3)
            expected = [(0.5, 1.5), (2.5, 3.6), (4.5, 5.4)]
            for found, want in zip(analysis.speech, expected):
                self.assertAlmostEqual(found[0], want[0], delta=0.15)
                self.assertAlmostEqual(found[1], want[1], delta=0.15)

    def test_prepare_media_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.wav"
            self._make_wav(path, 3.0)
            info = prepare_media(path, Path(tmp) / "work")
            self.assertIsNotNone(info.analysis)
            self.assertEqual(info.playback_path, path)
            self.assertGreater(info.duration, 2.5)

    def test_detect_speech_on_silence(self):
        self.assertEqual(detect_speech([]), [])


if __name__ == "__main__":
    unittest.main()
