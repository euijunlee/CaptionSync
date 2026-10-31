"""Application entry point."""

from __future__ import annotations

import sys
import traceback
import tkinter as tk
from tkinter import messagebox

from . import __app_name__, __version__
from .ui.main_window import MainWindow
from .ui.theme import setup_theme


def _show_unhandled(exc_type, exc_value, exc_traceback) -> None:
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    try:
        messagebox.showerror(
            f"{__app_name__} 오류",
            "예기치 않은 오류가 발생했습니다.\n\n"
            f"{exc_value}\n\n"
            "작업 내용은 저장한 뒤 앱을 다시 시작해 주세요.",
        )
    except tk.TclError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = tk.Tk()
    root.option_add("*tearOff", False)
    setup_theme(root)
    root.report_callback_exception = _show_unhandled

    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    default_width = min(1560, max(1180, screen_width - 80))
    default_height = min(960, max(760, screen_height - 80))
    root.geometry(
        f"{default_width}x{default_height}"
        f"+{max(0, (screen_width - default_width) // 2)}"
        f"+{max(0, (screen_height - default_height) // 2)}"
    )

    window = MainWindow(root)
    if argv:
        first = argv[0]
        if first.lower().endswith((".srt", ".vtt", ".webvtt", ".ass", ".ssa")):
            window.open_subtitle(first)
        else:
            window.load_media(first)
        if len(argv) > 1:
            window.load_media(argv[1])

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
