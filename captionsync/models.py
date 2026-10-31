"""Document model and undo/redo support."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

__all__ = ["Cue", "Document", "History", "MIN_DURATION", "clone_cues"]

MIN_DURATION = 0.04


@dataclass
class Cue:
    """One subtitle event.

    ``extra`` keeps format-specific data (WebVTT settings/identifier or the
    complete ASS field mapping) so that saving never destroys styling.
    """

    start: float
    end: float
    text: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def copy(self) -> "Cue":
        return Cue(self.start, self.end, self.text, copy.deepcopy(self.extra))

    def display_text(self) -> str:
        """Readable single-line text for the cue table and timeline."""

        text = self.text
        if "\\N" in text or "\\n" in text or "\\h" in text:
            text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        text = text.replace("\n", " ")
        return " ".join(text.split())


@dataclass
class Document:
    kind: str = "srt"
    cues: list[Cue] = field(default_factory=list)
    path: Path | None = None
    encoding: str = "utf-8"
    newline: str = "\r\n"
    meta: dict = field(default_factory=dict)
    dirty: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.path.name if self.path else "제목 없음"

    @property
    def first_start(self) -> float:
        return min((c.start for c in self.cues), default=0.0)

    @property
    def last_end(self) -> float:
        return max((c.end for c in self.cues), default=0.0)

    @property
    def duration(self) -> float:
        return self.last_end

    def cue_at(self, seconds: float) -> list[int]:
        """Indices of cues active at ``seconds``."""

        return [i for i, c in enumerate(self.cues) if c.start <= seconds < c.end]

    def renumber(self) -> None:
        self.cues.sort(key=lambda c: (c.start, c.end))


def clone_cues(cues: Iterable[Cue]) -> list[Cue]:
    return [cue.copy() for cue in cues]


class History:
    """Snapshot based undo/redo stack.

    Snapshot undo keeps all five correction workflows simple and safe; a
    subtitle file is small enough that copying it is effectively free.
    """

    def __init__(self, limit: int = 200) -> None:
        self.limit = limit
        self._undo: list[tuple[str, list[Cue]]] = []
        self._redo: list[tuple[str, list[Cue]]] = []
        self._pending: tuple[str, list[Cue]] | None = None

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._pending = None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def push_snapshot(self, label: str, before: list[Cue]) -> None:
        self._undo.append((label, before))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()

    def begin_interactive(self, label: str, before: list[Cue]) -> None:
        if self._pending is None:
            self._pending = (label, before)

    def commit_interactive(self) -> bool:
        if self._pending is None:
            return False
        label, before = self._pending
        self._pending = None
        self.push_snapshot(label, before)
        return True

    def abort_interactive(self) -> list[Cue] | None:
        if self._pending is None:
            return None
        _, before = self._pending
        self._pending = None
        return before

    def undo(self, current: list[Cue]) -> tuple[str, list[Cue]] | None:
        if not self._undo:
            return None
        label, before = self._undo.pop()
        self._redo.append((label, clone_cues(current)))
        if len(self._redo) > self.limit:
            self._redo.pop(0)
        return label, before

    def redo(self, current: list[Cue]) -> tuple[str, list[Cue]] | None:
        if not self._redo:
            return None
        label, after = self._redo.pop()
        self._undo.append((label, clone_cues(current)))
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        return label, after


def median(values: Iterable[float]) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    mid = len(data) // 2
    if len(data) % 2:
        return data[mid]
    return (data[mid - 1] + data[mid]) / 2.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def finite(value: float) -> float:
    return value if math.isfinite(value) else 0.0


def active_cues(cues: list[Cue], seconds: float) -> Iterator[tuple[int, Cue]]:
    for i, cue in enumerate(cues):
        if cue.start <= seconds < cue.end:
            yield i, cue
