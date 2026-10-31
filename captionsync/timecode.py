"""Timecode parsing and formatting helpers.

All internal timestamps are stored as float seconds.  Subtitle formats use
several different textual layouts, so parsing is deliberately forgiving:

    00:01:02,500      SRT
    00:01:02.500      WebVTT / ASS
    01:02.500         short form (minutes:seconds)
    62.5              plain seconds
    -3.25 / +350ms    relative offsets (for the editing tools)
"""

from __future__ import annotations

import math

__all__ = [
    "parse_timecode",
    "parse_offset",
    "format_srt_time",
    "format_vtt_time",
    "format_ass_time",
    "format_clock",
    "format_duration",
    "format_short_time",
]


def _strip_sign(raw: str) -> tuple[float, str]:
    raw = raw.strip()
    if not raw:
        raise ValueError("시간 값이 비어 있습니다.")
    sign = 1.0
    if raw[0] in "+-":
        if raw[0] == "-":
            sign = -1.0
        raw = raw[1:].strip()
    if not raw:
        raise ValueError("시간 값이 올바르지 않습니다.")
    return sign, raw


def parse_timecode(value: str | float | int) -> float:
    """Parse a human/subtitle timecode into seconds.

    Accepts ``HH:MM:SS,mmm``, ``MM:SS.mmm``, plain seconds and the suffixes
    ``ms`` and ``s``.  Raises ``ValueError`` with a Korean message when the
    input cannot be understood.
    """

    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError("시간 값이 올바르지 않습니다.")
        return float(value)

    sign, raw = _strip_sign(str(value))
    lowered = raw.lower()

    try:
        if lowered.endswith("ms"):
            result = float(lowered[:-2].strip()) / 1000.0
        elif lowered.endswith("s") and not lowered.endswith("ms"):
            result = float(lowered[:-1].strip())
        else:
            raw = raw.replace(",", ".")
            parts = raw.split(":")
            if len(parts) > 3:
                raise ValueError("시간 형식은 HH:MM:SS.mmm 까지만 지원합니다.")
            seconds = float(parts[-1])
            if not math.isfinite(seconds):
                raise ValueError
            if len(parts) >= 2:
                seconds += int(parts[-2]) * 60
            if len(parts) == 3:
                seconds += int(parts[-3]) * 3600
            result = seconds
    except (TypeError, ValueError):
        raise ValueError(f"시간 형식을 해석할 수 없습니다: {value!r}") from None

    if result < 0:
        raise ValueError("시간은 0 이상이어야 합니다.")
    return sign * result


def parse_offset(value: str | float | int) -> float:
    """Parse a signed offset such as ``-3``, ``+250ms`` or ``1:02.5``."""

    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        raise ValueError("이동 시간을 입력하세요.")
    return parse_timecode(raw)


def _split_ms(seconds: float) -> tuple[int, int, int, int]:
    if not math.isfinite(seconds):
        raise ValueError("시간 값이 올바르지 않습니다.")
    total_ms = int(round(max(0.0, seconds) * 1000.0))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return hours, minutes, secs, millis


def format_srt_time(seconds: float) -> str:
    h, m, s, ms = _split_ms(seconds)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_vtt_time(seconds: float) -> str:
    h, m, s, ms = _split_ms(seconds)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_ass_time(seconds: float) -> str:
    h, m, s, ms = _split_ms(seconds)
    # ASS stores centiseconds.  Round rather than truncate so that repeated
    # save/load cycles stay stable.
    centis = int(round(ms / 10.0))
    if centis >= 100:
        centis = 99
    return f"{h:d}:{m:02d}:{s:02d}.{centis:02d}"


def format_clock(seconds: float) -> str:
    """Format a media clock: ``HH:MM:SS.mmm``."""

    h, m, s, ms = _split_ms(seconds)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_short_time(seconds: float) -> str:
    """Compact label for timeline ticks and cue rows."""

    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    if h:
        return f"{h:d}:{m:02d}:{s:04.1f}"
    return f"{m:02d}:{s:04.1f}"


def format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.2f}초"
    m = int(seconds // 60)
    s = seconds % 60
    return f"{m}분 {s:04.1f}초"
