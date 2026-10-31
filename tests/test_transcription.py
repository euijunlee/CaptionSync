from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from captionsync.transcription import TranscriptionError, transcribe_korean


class _Chunk:
    def __init__(self, start: float, end: float, text: str) -> None:
        self.start = start
        self.end = end
        self.text = text


class TranscriptionTests(unittest.TestCase):
    def test_missing_backend_has_install_hint(self) -> None:
        with TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            audio.write_bytes(b"not used")
            with patch("captionsync.transcription.available_backend", return_value=None):
                with self.assertRaisesRegex(TranscriptionError, "faster-whisper"):
                    transcribe_korean(audio)

    def test_faster_whisper_segments_become_cues(self) -> None:
        class FakeModel:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def transcribe(self, *args, **kwargs):
                return iter([_Chunk(1.2, 2.4, " 안녕하세요. ")]), object()

        fake_module = types.SimpleNamespace(WhisperModel=FakeModel)
        with TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            audio.write_bytes(b"audio")
            with patch("captionsync.transcription.available_backend", return_value="faster-whisper"), patch.dict(
                sys.modules, {"faster_whisper": fake_module}
            ):
                cues = transcribe_korean(audio)

        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, "안녕하세요.")
        self.assertEqual((cues[0].start, cues[0].end), (1.2, 2.4))

    def test_empty_transcript_is_rejected(self) -> None:
        class FakeModel:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def transcribe(self, *args, **kwargs):
                return iter([]), object()

        fake_module = types.SimpleNamespace(WhisperModel=FakeModel)
        with TemporaryDirectory() as folder:
            audio = Path(folder) / "audio.wav"
            audio.write_bytes(b"audio")
            with patch("captionsync.transcription.available_backend", return_value="faster-whisper"), patch.dict(
                sys.modules, {"faster_whisper": fake_module}
            ):
                with self.assertRaisesRegex(TranscriptionError, "찾지 못했습니다"):
                    transcribe_korean(audio)


if __name__ == "__main__":
    unittest.main()
