"""Tiny JSON configuration store (window layout, ffmpeg path, last folder)."""

from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["config_path", "load_config", "save_config"]

APP_FOLDER = "CaptionSync"


def config_path() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    return root / APP_FOLDER / "config.json"


def load_config() -> dict:
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def save_config(data: dict) -> None:
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
