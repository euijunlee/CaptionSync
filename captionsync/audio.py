"""Audio analysis: waveform envelope, speech detection and media decoding.

Only the standard library is required.  WAV files are analysed directly;
other containers (MP3, MP4, MKV, ...) are converted to 16 kHz mono WAV with
FFmpeg when it is available.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import wave
from array import array
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "AudioError",
    "AudioAnalysis",
    "MediaInfo",
    "FRAME_HZ",
    "analyze_wav",
    "detect_speech",
    "prepare_media",
    "find_ffmpeg",
    "extract_audio",
]

FRAME_HZ = 100.0  # analysis resolution: 10 ms
WAV_SUFFIXES = {".wav", ".wave"}


class AudioError(Exception):
    pass


@dataclass
class AudioAnalysis:
    path: Path
    duration: float
    envelope: array
    sample_rate: int
    frame_hz: float = FRAME_HZ
    speech: list[tuple[float, float]] = field(default_factory=list)

    @property
    def frame_count(self) -> int:
        return len(self.envelope)


@dataclass
class MediaInfo:
    source: Path
    playback_path: Path
    duration: float
    analysis: AudioAnalysis | None
    note: str = ""
    converted: bool = False


def _percentile(sample: list[float], percent: float) -> float:
    if not sample:
        return 0.0
    ordered = sorted(sample)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (percent / 100.0)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _moving_average(values: array, radius: int = 2) -> list[float]:
    if not values:
        return []
    window = radius * 2 + 1
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    result: list[float] = []
    count = len(values)
    for i in range(count):
        lo = max(0, i - radius)
        hi = min(count, i + radius + 1)
        result.append((prefix[hi] - prefix[lo]) / (hi - lo))
    return result


def detect_speech(
    envelope: array,
    frame_hz: float = FRAME_HZ,
    sensitivity: float = 0.5,
    min_speech: float = 0.10,
    min_gap: float = 0.18,
) -> list[tuple[float, float]]:
    """Energy based voice activity detection with hysteresis.

    The threshold is derived from the noise floor and the loud percentile of
    the file, so it works for both quiet and loud sources.  ``sensitivity``
    runs from 0 (strict, only clear speech) to 1 (lenient, also quiet lines).
    """

    count = len(envelope)
    if count == 0:
        return []

    step = max(1, count // 20000)
    sample = [float(v) for v in envelope[::step]]
    noise = _percentile(sample, 20)
    peak = _percentile(sample, 97)
    if peak < 0.0035:
        return []
    span = peak - noise
    if span < 0.004:
        return []

    sensitivity = max(0.0, min(1.0, sensitivity))
    ratio = 0.34 - 0.26 * sensitivity
    on_threshold = noise + span * ratio
    off_threshold = noise + span * ratio * 0.55

    smooth = _moving_average(envelope, radius=2)
    gap_frames = max(2, int(round(min_gap * frame_hz)))
    min_frames = max(1, int(round(min_speech * frame_hz)))

    segments: list[tuple[int, int]] = []
    in_speech = False
    start_index = 0
    last_above = 0

    for index, value in enumerate(smooth):
        if not in_speech:
            if value >= on_threshold:
                in_speech = True
                start_index = index
                last_above = index
        else:
            if value >= off_threshold:
                last_above = index
            elif index - last_above >= gap_frames:
                segments.append((start_index, last_above + 1))
                in_speech = False

    if in_speech:
        segments.append((start_index, count))

    # Bridge short pauses (breaths, plosives) before filtering.
    merged: list[tuple[int, int]] = []
    bridge = max(6, int(round(0.22 * frame_hz)))
    for start, end in segments:
        if merged and start - merged[-1][1] <= bridge:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    result: list[tuple[float, float]] = []
    for start, end in merged:
        if end - start < min_frames:
            continue
        result.append((start / frame_hz, end / frame_hz))
    return result


def _peak_from_array(segment: array, full_scale: float, unsigned: bool = False) -> float:
    if not segment:
        return 0.0
    high = max(segment)
    low = min(segment)
    if unsigned:
        high = abs(high - 128)
        low = abs(low - 128)
        return max(high, low) / full_scale
    return max(abs(low), abs(high)) / full_scale


def analyze_wav(path: str | Path, progress=None) -> AudioAnalysis:
    """Build a 100 Hz peak envelope from a PCM WAV file."""

    path = Path(path)
    try:
        wav = wave.open(str(path), "rb")
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioError(f"WAV 파일을 읽을 수 없습니다: {exc}") from exc

    with wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frame_count = wav.getnframes()
        if sample_rate <= 0 or channels <= 0:
            raise AudioError("오디오 형식 정보가 올바르지 않습니다.")
        if width not in (1, 2, 3, 4):
            raise AudioError(f"지원하지 않는 샘플 크기입니다: {width * 8}bit")

        window_frames = max(1, int(round(sample_rate / FRAME_HZ)))
        stride = max(1, window_frames // 32)
        frame_bytes = channels * width
        window_bytes = window_frames * frame_bytes
        chunk_frames = max(window_frames, int(sample_rate * 2))  # ~2 seconds
        chunk_bytes = chunk_frames * frame_bytes
        step_bytes = stride * frame_bytes

        envelope = array("f")
        buffer = b""
        frames_read = 0
        total_frames = max(1, frame_count)
        next_report = 0.0

        while True:
            data = wav.readframes(chunk_frames)
            if not data:
                break
            frames_read += len(data) // frame_bytes
            buffer += data

            complete = len(buffer) // window_bytes
            if complete:
                usable = complete * window_bytes
                block = buffer[:usable]
                buffer = buffer[usable:]

                if width == 2:
                    values = array("h")
                    values.frombytes(block)
                    if sys.byteorder == "big":
                        values.byteswap()
                    full_scale = 32768.0
                    for w in range(complete):
                        start = w * window_frames * channels
                        end = (w + 1) * window_frames * channels
                        envelope.append(
                            _peak_from_array(values[start:end:step_bytes // width], full_scale)
                        )
                elif width == 1:
                    values = array("B")
                    values.frombytes(block)
                    for w in range(complete):
                        start = w * window_frames * channels
                        end = (w + 1) * window_frames * channels
                        envelope.append(
                            _peak_from_array(
                                values[start:end:step_bytes], 128.0, unsigned=True
                            )
                        )
                elif width == 4:
                    values = array("i")
                    values.frombytes(block)
                    if sys.byteorder == "big":
                        values.byteswap()
                    for w in range(complete):
                        start = w * window_frames * channels
                        end = (w + 1) * window_frames * channels
                        envelope.append(
                            _peak_from_array(values[start:end:step_bytes // width], 2147483648.0)
                        )
                else:  # 24-bit PCM
                    for w in range(complete):
                        base = w * window_frames * frame_bytes
                        peak = 0
                        for k in range(0, window_frames, stride):
                            offset = base + k * frame_bytes
                            value = int.from_bytes(
                                block[offset : offset + 3], "little", signed=True
                            )
                            if abs(value) > peak:
                                peak = abs(value)
                        envelope.append(peak / 8388608.0)

            if progress and frames_read / total_frames >= next_report:
                progress(min(1.0, frames_read / total_frames))
                next_report += 0.05

        # Handle a final partial window (very short files or odd frame counts).
        if buffer:
            peak = 0
            limit = len(buffer) - frame_bytes
            for offset in range(0, max(0, limit) + 1, step_bytes):
                if width == 1:
                    value = abs(buffer[offset] - 128) * (32768.0 / 128.0)
                else:
                    value = int.from_bytes(buffer[offset : offset + width], "little", signed=True)
                    value = abs(value)
                peak = max(peak, value)
            full_scale = {1: 128.0, 2: 32768.0, 3: 8388608.0, 4: 2147483648.0}[width]
            if width == 1:
                peak /= 32768.0 / 128.0
            envelope.append(min(1.0, peak / full_scale))

    frame_hz = sample_rate / window_frames
    duration = frame_count / sample_rate if frame_count else len(envelope) / frame_hz
    analysis = AudioAnalysis(
        path=path,
        duration=duration,
        envelope=envelope,
        sample_rate=sample_rate,
        frame_hz=frame_hz,
    )
    analysis.speech = detect_speech(envelope, frame_hz)
    if progress:
        progress(1.0)
    return analysis


def find_ffmpeg(configured: str | None = None) -> str | None:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates.extend(
        [
            str(Path(sys.executable).parent / "ffmpeg.exe"),
            str(Path.cwd() / "ffmpeg.exe"),
            str(Path(__file__).resolve().parent.parent / "ffmpeg.exe"),
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "ffmpeg" / "bin" / "ffmpeg.exe"),
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def extract_audio(
    media_path: str | Path,
    output_path: str | Path,
    ffmpeg_path: str,
    progress=None,
) -> None:
    """Decode the first audio stream to 16 kHz mono PCM WAV."""

    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output_path),
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if progress:
        progress(0.02)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=creationflags,
            timeout=60 * 60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AudioError(f"FFmpeg 실행에 실패했습니다: {exc}") from exc
    if result.returncode != 0:
        message = (result.stderr or "").strip().splitlines()
        detail = message[-1] if message else "알 수 없는 오류"
        raise AudioError(f"오디오를 추출할 수 없습니다: {detail}")
    if progress:
        progress(0.35)


def prepare_media(
    media_path: str | Path,
    work_dir: str | Path,
    ffmpeg_path: str | None = None,
    progress=None,
) -> MediaInfo:
    """Analyse a media file, converting it when necessary."""

    source = Path(media_path)
    if not source.is_file():
        raise AudioError(f"미디어 파일을 찾을 수 없습니다: {source}")

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    note = ""

    if suffix in WAV_SUFFIXES:
        try:
            analysis = analyze_wav(source, progress=progress)
            return MediaInfo(
                source=source,
                playback_path=source,
                duration=analysis.duration,
                analysis=analysis,
            )
        except AudioError as exc:
            note = f"WAV를 직접 분석하지 못했습니다 ({exc}). FFmpeg로 다시 시도합니다."

    ffmpeg = find_ffmpeg(ffmpeg_path)
    if not ffmpeg:
        if suffix in {".mp3", ".wma", ".m4a", ".aac"}:
            return MediaInfo(
                source=source,
                playback_path=source,
                duration=0.0,
                analysis=None,
                note=(
                    "FFmpeg가 없어 파형과 음성 자동 정렬을 사용할 수 없습니다. "
                    "재생은 가능합니다. FFmpeg를 설치한 뒤 다시 열어 주세요."
                ),
            )
        raise AudioError(
            "이 형식을 분석하려면 FFmpeg가 필요합니다.\n"
            "ffmpeg.exe를 PATH 또는 앱 폴더에 두거나, 메뉴의 "
            "'FFmpeg 경로 지정'을 사용하세요."
        )

    converted = work_dir / (source.stem.replace(" ", "_") + ".captionsync.wav")
    if progress:
        progress(0.01)
    extract_audio(source, converted, ffmpeg, progress=progress)
    analysis = analyze_wav(converted, progress=progress)
    analysis.path = source
    if note:
        note += " "
    note += "FFmpeg로 오디오를 추출해 분석했습니다."
    return MediaInfo(
        source=source,
        playback_path=converted,
        duration=analysis.duration,
        analysis=analysis,
        note=note,
        converted=True,
    )
