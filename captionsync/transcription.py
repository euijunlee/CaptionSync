"""Optional local speech-to-text transcription."""

from __future__ import annotations

from pathlib import Path

from .models import Cue

__all__ = ["TranscriptionError", "transcribe_korean", "available_backend"]


class TranscriptionError(RuntimeError):
    """Readable transcription failure for the GUI."""


def available_backend() -> str | None:
    try:
        import faster_whisper  # noqa: F401
        return "faster-whisper"
    except ImportError:
        try:
            import whisper  # noqa: F401
            return "openai-whisper"
        except ImportError:
            return None


def transcribe_korean(audio_path: str | Path, model_name: str = "small", progress=None) -> list[Cue]:
    """Transcribe a prepared audio file into Korean subtitle cues."""
    path = Path(audio_path)
    if not path.is_file():
        raise TranscriptionError(f"오디오 파일을 찾을 수 없습니다: {path}")
    backend = available_backend()
    if backend is None:
        raise TranscriptionError(
            "한국어 음성인식 엔진이 설치되지 않았습니다.\n\n"
            "권장 설치:\n  pip install faster-whisper\n\n"
            "설치 후 앱을 다시 실행하세요. 모델은 첫 실행 때 자동 다운로드됩니다."
        )
    if progress:
        progress(0.02)
    try:
        if backend == "faster-whisper":
            from faster_whisper import WhisperModel
            model = WhisperModel(model_name, device="auto", compute_type="int8")
            chunks, _info = model.transcribe(str(path), language="ko", task="transcribe", vad_filter=True)
            cues = [
                Cue(float(c.start), max(float(c.start) + 0.04, float(c.end)), c.text.strip())
                for c in chunks if c.text and c.text.strip() and c.end > c.start
            ]
        else:
            import whisper
            model = whisper.load_model(model_name)
            result = model.transcribe(str(path), language="ko", task="transcribe", fp16=False)
            cues = [
                Cue(float(s["start"]), max(float(s["start"]) + 0.04, float(s["end"])), s["text"].strip())
                for s in result.get("segments", []) if s.get("text", "").strip() and s["end"] > s["start"]
            ]
    except Exception as exc:  # noqa: BLE001
        raise TranscriptionError(f"한국어 자막 생성에 실패했습니다: {exc}") from exc
    if progress:
        progress(1.0)
    if not cues:
        raise TranscriptionError("음성에서 자막으로 만들 문장을 찾지 못했습니다.")
    return cues
