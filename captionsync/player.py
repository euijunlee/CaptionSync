"""Audio playback.

Windows Media Control Interface (MCI) is used through ``winmm.dll`` so the
app can seek and report the playhead without bundling a media framework.  When
MCI cannot open a file the controller falls back to a virtual clock, which
still lets the user scrub the timeline and check cue placement.
"""

from __future__ import annotations

import ctypes
import sys
import time
import uuid
from pathlib import Path

__all__ = ["PlaybackController", "MciPlayer", "PlayerError"]


class PlayerError(Exception):
    pass


def _mci_send(command: str) -> str:
    if sys.platform != "win32":
        raise PlayerError("MCI 재생은 Windows에서만 지원됩니다.")
    buffer = ctypes.create_unicode_buffer(2048)
    result = ctypes.windll.winmm.mciSendStringW(  # type: ignore[attr-defined]
        command, buffer, len(buffer), None
    )
    if result:
        error_buffer = ctypes.create_unicode_buffer(512)
        ctypes.windll.winmm.mciGetErrorStringW(  # type: ignore[attr-defined]
            result, error_buffer, len(error_buffer)
        )
        raise PlayerError(error_buffer.value or f"MCI 오류 {result}")
    return buffer.value


class MciPlayer:
    """Thin MCI wrapper with an internal alias."""

    def __init__(self) -> None:
        self.alias: str | None = None
        self.path: Path | None = None

    def open(self, path: str | Path) -> bool:
        self.close()
        path = Path(path)
        alias = "csync" + uuid.uuid4().hex[:10]
        quoted = f'"{path}"'
        attempts = [
            f"open {quoted} alias {alias}",
            f'open {quoted} type mpegvideo alias {alias}',
            f'open {quoted} type waveaudio alias {alias}',
        ]
        for command in attempts:
            try:
                _mci_send(command)
            except PlayerError:
                continue
            self.alias = alias
            self.path = path
            try:
                _mci_send(f"set {alias} time format milliseconds")
            except PlayerError:
                pass
            return True
        return False

    def close(self) -> None:
        if self.alias:
            try:
                _mci_send(f"close {self.alias}")
            except PlayerError:
                pass
        self.alias = None
        self.path = None

    def _require(self) -> str:
        if not self.alias:
            raise PlayerError("열린 미디어가 없습니다.")
        return self.alias

    def length(self) -> float:
        alias = self._require()
        try:
            value = _mci_send(f"status {alias} length")
            return max(0.0, float(value) / 1000.0)
        except (PlayerError, ValueError):
            return 0.0

    def mode(self) -> str:
        alias = self._require()
        try:
            return _mci_send(f"status {alias} mode").strip().lower()
        except PlayerError:
            return "stopped"

    def position(self) -> float:
        alias = self._require()
        try:
            value = _mci_send(f"status {alias} position")
            return max(0.0, float(value) / 1000.0)
        except (PlayerError, ValueError):
            return 0.0

    def seek(self, seconds: float) -> None:
        alias = self._require()
        milliseconds = max(0, int(round(seconds * 1000.0)))
        _mci_send(f"seek {alias} to {milliseconds}")

    def play(self) -> None:
        alias = self._require()
        _mci_send(f"play {alias}")

    def pause(self) -> None:
        alias = self._require()
        _mci_send(f"pause {alias}")

    def stop(self) -> None:
        alias = self._require()
        try:
            _mci_send(f"stop {alias}")
        finally:
            self.seek(0.0)


class PlaybackController:
    """Play/pause/seek either through MCI or a virtual clock."""

    def __init__(self) -> None:
        self.mci = MciPlayer()
        self.duration = 0.0
        self._virtual = False
        self._playing = False
        self._position = 0.0
        self._anchor = 0.0
        self._started = 0.0
        self._media_path: Path | None = None

    @property
    def has_media(self) -> bool:
        return self._media_path is not None

    @property
    def using_mci(self) -> bool:
        return not self._virtual and self.mci.alias is not None

    def load(self, path: str | Path | None, duration: float = 0.0) -> bool:
        self.close()
        self._media_path = Path(path) if path else None
        self.duration = max(0.0, duration)
        self._position = 0.0
        self._virtual = True
        if self._media_path:
            if self.mci.open(self._media_path):
                length = self.mci.length()
                if length > 0:
                    self.duration = max(self.duration, length)
                self._virtual = False
        return not self._virtual

    def close(self) -> None:
        self.mci.close()
        self._playing = False
        self._position = 0.0
        self._media_path = None
        self._virtual = True

    @property
    def playing(self) -> bool:
        if self._virtual:
            return self._playing
        if self.mci.alias:
            try:
                return self.mci.mode() == "playing"
            except PlayerError:
                return False
        return False

    def position(self) -> float:
        if self._virtual:
            if self._playing:
                value = self._anchor + (time.monotonic() - self._started)
                if self.duration and value >= self.duration:
                    self._position = self.duration
                    self._playing = False
                else:
                    self._position = value
            return self._position
        if not self.mci.alias:
            return self._position
        try:
            value = self.mci.position()
            if value > 0 or self.mci.mode() != "stopped":
                self._position = value
        except PlayerError:
            pass
        return self._position

    def play(self, position: float | None = None) -> None:
        if position is not None:
            self.seek(position)
        if not self.has_media:
            self._playing = True
            self._anchor = self._position
            self._started = time.monotonic()
            return
        if self._virtual:
            self._playing = True
            self._anchor = self._position
            self._started = time.monotonic()
            return
        try:
            if self.duration and self._position >= self.duration - 0.05:
                self.mci.seek(0.0)
                self._position = 0.0
            self.mci.play()
        except PlayerError:
            self._virtual = True
            self._playing = True
            self._anchor = self._position
            self._started = time.monotonic()

    def pause(self) -> None:
        self._position = self.position()
        self._playing = False
        if not self._virtual and self.mci.alias:
            try:
                self.mci.pause()
            except PlayerError:
                pass

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def seek(self, position: float) -> None:
        position = max(0.0, position)
        if self.duration:
            position = min(position, self.duration)
        self._position = position
        self._anchor = position
        self._started = time.monotonic()
        if not self._virtual and self.mci.alias:
            try:
                self.mci.seek(position)
            except PlayerError:
                pass

    def stop(self) -> None:
        self._playing = False
        self._position = 0.0
        if not self._virtual and self.mci.alias:
            try:
                self.mci.stop()
            except PlayerError:
                pass
