"""Reading and writing SRT, WebVTT and ASS/SSA subtitle files."""

from __future__ import annotations

import codecs
import re
from pathlib import Path

from .models import Cue, Document
from .timecode import (
    format_ass_time,
    format_srt_time,
    format_vtt_time,
    parse_timecode,
)

__all__ = [
    "SubtitleError",
    "SUPPORTED_SUFFIXES",
    "load_subtitle",
    "save_subtitle",
    "serialize_document",
    "kind_from_suffix",
    "detect_kind",
    "decode_bytes",
]

SUPPORTED_SUFFIXES = (".srt", ".vtt", ".webvtt", ".ass", ".ssa")
_TIME_TOKEN = re.compile(
    r"\d{1,3}:\d{1,2}:\d{2}(?:[.,]\d{1,3})?|\d{1,3}:\d{2}(?:[.,]\d{1,3})?|\d+(?:[.,]\d+)?"
)
_ASS_TAG = re.compile(r"\{[^}]*\}")

_ASS_DEFAULT_FIELDS = [
    "Layer",
    "Start",
    "End",
    "Style",
    "Name",
    "MarginL",
    "MarginR",
    "MarginV",
    "Effect",
    "Text",
]

_ASS_DEFAULT_HEADER = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,54,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2.5,0,2,30,30,40,1

[Events]""".split("\n")


class SubtitleError(Exception):
    """Raised when a subtitle file cannot be read."""


def kind_from_suffix(path: str | Path) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in (".ass", ".ssa"):
        return "ass"
    if suffix in (".vtt", ".webvtt"):
        return "vtt"
    if suffix == ".srt":
        return "srt"
    return None


def detect_kind(path: str | Path, text: str) -> str:
    by_suffix = kind_from_suffix(path)
    if by_suffix:
        return by_suffix
    head = text.lstrip("\ufeff \t\r\n")[:500].lower()
    if head.startswith("webvtt"):
        return "vtt"
    if "[script info]" in head or "[events]" in head:
        return "ass"
    return "srt"


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Decode subtitle bytes, preferring UTF-8 and Korean CP949."""

    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16"), "utf-16"
    for encoding in ("utf-8", "cp949"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("cp949", errors="replace"), "cp949"


def _detect_newline(raw: bytes) -> str:
    crlf = raw.find(b"\r\n")
    lf = raw.find(b"\n")
    cr = raw.find(b"\r")
    if crlf != -1 and (lf == -1 or crlf <= lf):
        return "\r\n"
    if cr != -1 and lf == -1:
        return "\r"
    return "\n"


def _split_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _time_token(text: str) -> str:
    match = _TIME_TOKEN.search(text)
    return match.group(0) if match else ""


def _parse_timing_line(line: str) -> tuple[float, float, str] | None:
    if "-->" not in line:
        return None
    left, right = line.split("-->", 1)
    try:
        start = parse_timecode(_time_token(left))
        right = right.strip()
        match = _TIME_TOKEN.match(right)
        if not match:
            return None
        end = parse_timecode(match.group(0))
    except ValueError:
        return None
    tail = right[match.end() :].strip()
    return max(0.0, start), max(0.0, end), tail


def _load_srt(text: str) -> Document:
    doc = Document(kind="srt")
    for block in _split_blocks(text):
        timing = None
        timing_index = -1
        for i, line in enumerate(block):
            timing = _parse_timing_line(line)
            if timing:
                timing_index = i
                break
        if not timing:
            continue
        start, end, _ = timing
        body = "\n".join(block[timing_index + 1 :]).strip("\n")
        if end <= start:
            end = start + 0.5
        doc.cues.append(Cue(start=start, end=end, text=body))
    return doc


def _load_vtt(text: str) -> Document:
    doc = Document(kind="vtt")
    header: list[str] = []
    extras: list[str] = []
    blocks = _split_blocks(text)
    if blocks and blocks[0][0].lstrip("\ufeff").strip().upper().startswith("WEBVTT"):
        header = blocks.pop(0)
    for block in blocks:
        first = block[0].strip()
        if first.upper().startswith(("NOTE", "STYLE", "REGION")):
            extras.append("\n".join(block))
            continue
        timing_index = -1
        timing = None
        for i, line in enumerate(block):
            timing = _parse_timing_line(line)
            if timing:
                timing_index = i
                break
        if not timing:
            continue
        start, end, settings = timing
        identifier = "\n".join(block[:timing_index]).strip()
        body = "\n".join(block[timing_index + 1 :]).strip("\n")
        if end <= start:
            end = start + 0.5
        doc.cues.append(
            Cue(
                start=start,
                end=end,
                text=body,
                extra={
                    "vtt_identifier": identifier,
                    "vtt_settings": settings,
                },
            )
        )
    doc.meta = {"vtt_header": header, "vtt_blocks": extras}
    return doc


def _load_ass(text: str) -> Document:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    header: list[str] = []
    fields: list[str] | None = None
    extra_event_lines: list[str] = []
    tail: list[str] = []
    cues: list[Cue] = []
    section = ""
    format_seen = False
    in_tail = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            if format_seen and section != "events":
                in_tail = True
            if section == "events":
                in_tail = False
            if in_tail:
                tail.append(line)
            else:
                header.append(line)
            continue

        if in_tail:
            tail.append(line)
            continue

        if section == "events":
            if stripped.lower().startswith("format:"):
                payload = stripped.split(":", 1)[1]
                fields = [part.strip() for part in payload.split(",")]
                format_seen = True
                continue
            if stripped.lower().startswith("dialogue:"):
                if not fields:
                    continue
                payload = line.split(":", 1)[1]
                values = payload.split(",", len(fields) - 1)
                if len(values) < len(fields):
                    values.extend([""] * (len(fields) - len(values)))
                data: dict[str, str] = {}
                for key, value in zip(fields, values):
                    # The Text field is last; keep its leading spaces intact.
                    data[key] = value if key.strip().lower() == "text" else value.strip()
                lookup = {key.strip().lower(): key for key in data}
                try:
                    start = parse_timecode(data[lookup["start"]])
                    end = parse_timecode(data[lookup["end"]])
                except (KeyError, ValueError):
                    continue
                if end <= start:
                    end = start + 0.5
                body = data.get(lookup.get("text", ""), "")
                cues.append(Cue(start=start, end=end, text=body, extra={"ass": data}))
                continue
            if stripped:
                extra_event_lines.append(line)
            continue

        header.append(line)

    if fields is None:
        fields = list(_ASS_DEFAULT_FIELDS)

    doc = Document(kind="ass", cues=cues)
    doc.meta = {
        "ass_header": header,
        "ass_fields": fields,
        "ass_extra": extra_event_lines,
        "ass_tail": tail,
    }
    return doc


def load_subtitle(path: str | Path, encoding: str | None = None) -> Document:
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise SubtitleError(f"파일을 열 수 없습니다: {exc}") from exc

    if encoding:
        try:
            text = raw.decode(encoding)
            used_encoding = encoding
        except UnicodeDecodeError as exc:
            raise SubtitleError(f"{encoding} 인코딩으로 읽을 수 없습니다: {exc}") from exc
    else:
        text, used_encoding = decode_bytes(raw)

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    kind = detect_kind(p, text)
    if kind == "ass":
        doc = _load_ass(text)
    elif kind == "vtt":
        doc = _load_vtt(text)
    else:
        doc = _load_srt(text)

    doc.kind = kind
    doc.path = p
    doc.encoding = used_encoding
    doc.newline = _detect_newline(raw)
    doc.dirty = False
    if not doc.cues:
        raise SubtitleError("자막 항목을 찾지 못했습니다. 파일 형식을 확인해 주세요.")
    return doc


def _plain_text(text: str) -> str:
    """Convert ASS markup into plain text for SRT/VTT export."""

    text = _ASS_TAG.sub("", text)
    return (
        text.replace("\\N", "\n")
        .replace("\\n", "\n")
        .replace("\\h", " ")
    )


def _cue_text_for(doc: Document, cue: Cue, kind: str) -> str:
    text = cue.text
    if doc.kind == "ass" and kind != "ass":
        text = _plain_text(text)
    if kind == "ass":
        text = text.replace("\r", "").replace("\n", "\\N")
    return text


def _serialize_srt(doc: Document) -> str:
    parts: list[str] = []
    for number, cue in enumerate(doc.cues, start=1):
        text = _cue_text_for(doc, cue, "srt")
        parts.append(
            f"{number}\n{format_srt_time(cue.start)} --> {format_srt_time(cue.end)}\n{text}"
        )
    return "\n\n".join(parts)


def _serialize_vtt(doc: Document) -> str:
    header = doc.meta.get("vtt_header") or ["WEBVTT"]
    parts: list[str] = ["\n".join(header)]
    for block in doc.meta.get("vtt_blocks", []):
        parts.append(block)
    for cue in doc.cues:
        identifier = str(cue.extra.get("vtt_identifier", "") or "").strip()
        settings = str(cue.extra.get("vtt_settings", "") or "").strip()
        timing = f"{format_vtt_time(cue.start)} --> {format_vtt_time(cue.end)}"
        if settings:
            timing += f" {settings}"
        block = []
        if identifier:
            block.append(identifier)
        block.append(timing)
        block.append(_cue_text_for(doc, cue, "vtt"))
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def _serialize_ass(doc: Document) -> str:
    header = doc.meta.get("ass_header") or list(_ASS_DEFAULT_HEADER)
    fields = doc.meta.get("ass_fields") or list(_ASS_DEFAULT_FIELDS)
    lines = list(header)
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append("Format: " + ", ".join(fields))
    for extra in doc.meta.get("ass_extra", []):
        lines.append(extra)

    for cue in doc.cues:
        data = dict(cue.extra.get("ass") or {})
        for field in fields:
            data.setdefault(field, "")
        lookup = {key.strip().lower(): key for key in data}
        start_key = lookup.get("start", "Start")
        end_key = lookup.get("end", "End")
        text_key = lookup.get("text", "Text")
        data[start_key] = format_ass_time(cue.start)
        data[end_key] = format_ass_time(cue.end)
        data[text_key] = _cue_text_for(doc, cue, "ass")
        values = []
        for field in fields:
            value = str(data.get(field, ""))
            values.append(value)
        lines.append("Dialogue: " + ",".join(values))

    for line in doc.meta.get("ass_tail", []):
        lines.append(line)
    return "\n".join(lines)


def serialize_document(doc: Document, kind: str | None = None) -> str:
    kind = kind or doc.kind
    if kind == "ass":
        body = _serialize_ass(doc)
    elif kind == "vtt":
        body = _serialize_vtt(doc)
    else:
        body = _serialize_srt(doc)
    return body.rstrip("\n") + "\n"


def save_subtitle(
    doc: Document,
    path: str | Path,
    kind: str | None = None,
    encoding: str | None = None,
) -> tuple[str, str | None]:
    """Write ``doc`` to ``path``.

    Returns ``(encoding_used, warning)``.  When the original encoding cannot
    represent the text (for example CP949 with emoji) the file falls back to
    UTF-8 instead of failing.
    """

    target = Path(path)
    kind = kind or kind_from_suffix(target) or doc.kind
    text = serialize_document(doc, kind)
    used_encoding = encoding or doc.encoding or "utf-8"
    warning = None
    try:
        raw = text.encode(used_encoding)
    except (UnicodeEncodeError, LookupError):
        warning = (
            f"{used_encoding} 인코딩으로 표현할 수 없는 문자가 있어 "
            "UTF-8로 저장했습니다."
        )
        used_encoding = "utf-8"
        raw = text.encode(used_encoding)

    target.write_bytes(raw)
    doc.path = target
    doc.kind = kind
    doc.encoding = used_encoding
    doc.newline = "\r\n" if "\r\n" in text else "\n"
    doc.dirty = False
    return used_encoding, warning
