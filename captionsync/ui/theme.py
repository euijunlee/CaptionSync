"""Professional dark editing theme and ttk style configuration."""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

__all__ = [
    "BG",
    "PANEL",
    "PANEL_ALT",
    "PANEL_HOVER",
    "BORDER",
    "BORDER_LIGHT",
    "TEXT",
    "MUTED",
    "FAINT",
    "ACCENT",
    "ACCENT_DIM",
    "ACCENT_SOFT",
    "VIOLET",
    "OK",
    "WARN",
    "DANGER",
    "WAVE",
    "WAVE_SPEECH",
    "FONT",
    "FONT_BOLD",
    "FONT_SMALL",
    "FONT_SMALL_BOLD",
    "FONT_TINY",
    "FONT_TITLE",
    "FONT_BIG",
    "MONO",
    "setup_theme",
]

# Palette ---------------------------------------------------------------
BG = "#18191c"
PANEL = "#24262b"
PANEL_ALT = "#1e2024"
PANEL_HOVER = "#30343b"
BORDER = "#3a3e46"
BORDER_LIGHT = "#555b66"
TEXT = "#edf0f4"
MUTED = "#a5acb7"
FAINT = "#707783"
ACCENT = "#3d9cff"
ACCENT_DIM = "#2366a6"
ACCENT_SOFT = "#1c3d5d"
VIOLET = "#a58cff"
OK = "#4fd1a1"
WARN = "#f1b35b"
DANGER = "#ff6f85"
WAVE = "#4b7486"
WAVE_SPEECH = "#3d9d9b"

# Fonts (filled in by setup_theme) -------------------------------------
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_SMALL_BOLD = ("Segoe UI", 9, "bold")
FONT_TINY = ("Segoe UI", 8)
FONT_TITLE = ("Segoe UI", 15, "bold")
FONT_BIG = ("Segoe UI", 12)
MONO = ("Consolas", 10)


def _pick_font(families: set[str], preferred: list[str], fallback: str) -> str:
    for name in preferred:
        if name in families:
            return name
    return fallback


def _enable_windows_dark_menus() -> None:
    """Ask Windows to draw native menus in dark mode (best effort)."""

    try:
        import ctypes

        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme.SetPreferredAppMode(2)  # ForceDark
        uxtheme.FlushMenuThemes()
    except Exception:
        pass


def setup_theme(root: tk.Tk) -> None:
    global FONT, FONT_BOLD, FONT_SMALL, FONT_SMALL_BOLD, FONT_TINY
    global FONT_TITLE, FONT_BIG, MONO

    _enable_windows_dark_menus()

    families = set(tkfont.families(root))
    family = _pick_font(
        families, ["Malgun Gothic", "Noto Sans KR", "Segoe UI", "Arial"], "Segoe UI"
    )
    mono_family = _pick_font(
        families, ["Consolas", "D2Coding", "Cascadia Mono", "Courier New"], "Courier New"
    )

    FONT = (family, 10)
    FONT_BOLD = (family, 10, "bold")
    FONT_SMALL = (family, 9)
    FONT_SMALL_BOLD = (family, 9, "bold")
    FONT_TINY = (family, 8)
    FONT_TITLE = (family, 15, "bold")
    FONT_BIG = (family, 12, "bold")
    MONO = (mono_family, 10)

    root.configure(bg=BG)
    root.option_add("*Font", FONT)
    root.option_add("*Menu.background", PANEL_ALT)
    root.option_add("*Menu.foreground", TEXT)
    root.option_add("*Menu.activeBackground", ACCENT_DIM)
    root.option_add("*Menu.activeForeground", "#ffffff")
    root.option_add("*Menu.borderWidth", "0")
    root.option_add("*Menu.relief", "flat")
    root.option_add("*TCombobox*Listbox.background", PANEL_ALT)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", FONT_SMALL)
    root.option_add("*Tooltip.background", PANEL_HOVER)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, font=FONT)
    style.configure("TFrame", background=PANEL, borderwidth=0)
    style.configure("Card.TFrame", background=PANEL, borderwidth=0)
    style.configure("Bg.TFrame", background=BG, borderwidth=0)
    style.configure("TLabel", background=PANEL, foreground=TEXT, font=FONT)
    style.configure("Bg.TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=FONT_SMALL)
    style.configure("Hint.TLabel", background=PANEL, foreground=MUTED, font=FONT_SMALL)

    # Buttons are mostly tk.Button via widgets.button(); keep ttk usable too.
    style.configure(
        "TButton",
        background=PANEL_HOVER,
        foreground=TEXT,
        borderwidth=0,
        focusthickness=0,
        padding=(12, 7),
        font=FONT_SMALL,
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("pressed", ACCENT_DIM), ("active", BORDER_LIGHT)],
        foreground=[("disabled", FAINT)],
    )

    # Entries / combos / spinboxes
    for name in ("TEntry", "TSpinbox"):
        style.configure(
            name,
            fieldbackground=PANEL_ALT,
            background=PANEL_ALT,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=(7, 5),
            arrowcolor=MUTED,
            relief="flat",
        )
        style.map(
            name,
            bordercolor=[("focus", ACCENT)],
            lightcolor=[("focus", ACCENT)],
            darkcolor=[("focus", ACCENT)],
            fieldbackground=[("disabled", PANEL)],
        )

    style.configure(
        "TCombobox",
        fieldbackground=PANEL_ALT,
        background=PANEL_ALT,
        foreground=TEXT,
        arrowcolor=MUTED,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=(7, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL_ALT), ("disabled", PANEL)],
        foreground=[("readonly", TEXT)],
        bordercolor=[("focus", ACCENT)],
        arrowcolor=[("active", TEXT)],
    )

    # Notebook
    style.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=(4, 4, 4, 0))
    style.configure(
        "TNotebook.Tab",
        background=PANEL,
        foreground=MUTED,
        padding=(10, 7),
        font=FONT_SMALL,
        borderwidth=0,
        focusthickness=0,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PANEL_ALT), ("active", PANEL_HOVER)],
        foreground=[("selected", TEXT), ("active", TEXT)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    # Treeview
    style.configure(
        "Cues.Treeview",
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        borderwidth=0,
        relief="flat",
        rowheight=27,
        font=FONT_SMALL,
    )
    style.configure(
        "Cues.Treeview.Heading",
        background=PANEL_ALT,
        foreground=MUTED,
        borderwidth=0,
        relief="flat",
        font=FONT_SMALL,
        padding=(6, 7),
    )
    style.map(
        "Cues.Treeview",
        background=[("selected", ACCENT_DIM)],
        foreground=[("selected", "#ffffff")],
    )
    style.map(
        "Cues.Treeview.Heading",
        background=[("active", PANEL_HOVER)],
        foreground=[("active", TEXT)],
    )

    # Scrollbars
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=BORDER,
            troughcolor=PANEL_ALT,
            bordercolor=PANEL_ALT,
            arrowcolor=MUTED,
            lightcolor=BORDER,
            darkcolor=BORDER,
            gripcount=0,
            relief="flat",
            arrowsize=12,
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("active", BORDER_LIGHT), ("pressed", ACCENT_DIM)],
        )

    # Check / radio
    style.configure(
        "Card.TCheckbutton",
        background=PANEL,
        foreground=TEXT,
        focuscolor=PANEL,
        font=FONT_SMALL,
        padding=(0, 2),
        indicatorcolor=PANEL_ALT,
    )
    style.map(
        "Card.TCheckbutton",
        background=[("active", PANEL)],
        foreground=[("disabled", FAINT)],
        indicatorcolor=[("selected", ACCENT), ("active", BORDER_LIGHT)],
    )
    style.configure(
        "Card.TRadiobutton",
        background=PANEL,
        foreground=TEXT,
        focuscolor=PANEL,
        font=FONT_SMALL,
        padding=(0, 2),
        indicatorcolor=PANEL_ALT,
    )
    style.map(
        "Card.TRadiobutton",
        background=[("active", PANEL)],
        foreground=[("disabled", FAINT)],
        indicatorcolor=[("selected", ACCENT), ("active", BORDER_LIGHT)],
    )

    # Scale
    style.configure(
        "Card.Horizontal.TScale",
        background=PANEL,
        troughcolor=PANEL_ALT,
        bordercolor=BORDER,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        sliderrelief="flat",
        sliderlength=16,
        gripcount=0,
    )
    style.map(
        "Card.Horizontal.TScale",
        background=[("active", PANEL)],
        lightcolor=[("active", ACCENT)],
    )

    # Separator
    style.configure("Card.TSeparator", background=BORDER)
