"""Generate a small demo WAV + deliberately mis-synced subtitle files.

Usage:
    python tools/make_demo.py [output_dir]

The demo audio contains ten speech-like bursts.  ``demo_late.srt`` is three
seconds late everywhere, and ``demo_jitter.srt`` has per-line errors.  Open the
WAV in CaptionSync, then try the "음성 기준 오프셋 추천" and auto-alignment.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

SEGMENTS = [
    (1.5, 3.2),
    (4.0, 6.3),
    (7.2, 9.0),
    (10.4, 12.6),
    (13.8, 15.4),
    (16.8, 19.2),
    (20.5, 22.2),
    (23.6, 26.1),
    (27.0, 29.0),
    (30.0, 31.6),
]

LINES = [
    "첫 번째 대사입니다. 전체 자막을 삼 초만큼 이동해 보세요.",
    "두 번째 대사는 조금 더 길게 이어집니다.",
    "세 번째 장면에서는 음성 구간이 시작되는 지점을 확인합니다.",
    "네 번째 대사부터는 자막 표시 시간이 조금씩 어긋납니다.",
    "다섯 번째 대사는 짧습니다.",
    "여섯 번째 대사는 싱크 보정 도구의 사용법을 설명합니다.",
    "일곱 번째 대사는 비율 보정과 구간 보정을 비교합니다.",
    "여덟 번째 대사에서는 파형과 음성 구간을 살펴봅니다.",
    "아홉 번째 대사는 드래그로 미세 조정하는 방법을 알려 줍니다.",
    "마지막 대사입니다. 저장하면 원본 형식이 유지됩니다.",
]


def srt_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, rest = divmod(milliseconds, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def make_audio(path: Path, duration: float = 33.0, rate: int = 16000) -> None:
    samples = bytearray()
    for index in range(int(duration * rate)):
        t = index / rate
        value = 0.0
        for seg_index, (start, end) in enumerate(SEGMENTS):
            if start <= t <= end:
                local = t - start
                length = end - start
                attack = min(1.0, local / 0.06)
                release = min(1.0, (end - t) / 0.10)
                envelope = max(0.0, min(attack, release))
                pitch = 155.0 + seg_index * 7.0
                value = envelope * (
                    0.30 * math.sin(2 * math.pi * pitch * t)
                    + 0.16 * math.sin(2 * math.pi * pitch * 2.02 * t)
                    + 0.08 * math.sin(2 * math.pi * pitch * 3.01 * t)
                    + 0.03 * math.sin(2 * math.pi * 9.0 * t)
                )
                break
        samples.extend(struct.pack("<h", int(max(-1.0, min(1.0, value)) * 32767)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(samples))


def make_subtitles(directory: Path) -> None:
    late = []
    jitter = []
    jitter_offsets = [0.42, -0.35, 0.28, -0.44, 0.18, 0.36, -0.30, 0.24, -0.20, 0.40]
    for index, ((start, end), text) in enumerate(zip(SEGMENTS, LINES), start=1):
        duration = end - start
        late.append(
            f"{index}\n{srt_time(start + 3.0)} --> {srt_time(end + 3.0)}\n{text}\n"
        )
        offset = jitter_offsets[index - 1]
        jitter.append(
            f"{index}\n{srt_time(start + offset)} --> "
            f"{srt_time(start + offset + duration * 1.45)}\n{text}\n"
        )
    (directory / "demo_late.srt").write_text("\n".join(late), encoding="utf-8")
    (directory / "demo_jitter.srt").write_text("\n".join(jitter), encoding="utf-8")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "examples"
    output.mkdir(parents=True, exist_ok=True)
    make_audio(output / "demo.wav")
    make_subtitles(output)
    print(f"Wrote {output / 'demo.wav'}")
    print(f"Wrote {output / 'demo_late.srt'}")
    print(f"Wrote {output / 'demo_jitter.srt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
