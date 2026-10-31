"""Small Tk widget helpers shared by the UI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme

__all__ = [
    "button",
    "entry",
    "combo",
    "check",
    "radio",
    "card",
    "section",
    "hint",
    "divider",
    "pill",
    "ScrollableFrame",
    "Tooltip",
    "make_icon",
    "make_menu_icon",
]


def button(
    parent,
    text: str,
    command=None,
    kind: str = "ghost",
    font=None,
    padx: int = 12,
    pady: int = 6,
    width: int | None = None,
    tooltip: str | None = None,
) -> tk.Button:
    palette = {
        "accent": (theme.ACCENT, "#071421", "#65b5ff", "#071421"),
        "apply": (theme.ACCENT, theme.COMPLEMENT, "#65b5ff", "#fff0ad"),
        "soft": (theme.ACCENT_SOFT, "#b9dcff", "#2b5b86", "#ffffff"),
        "ghost": (theme.PANEL_HOVER, theme.TEXT, theme.BORDER_LIGHT, theme.TEXT),
        "bare": (theme.PANEL, theme.MUTED, theme.PANEL_HOVER, theme.TEXT),
        "danger": ("#3a2028", theme.DANGER, "#60303d", "#ffd7de"),
        "ok": ("#193a34", theme.OK, "#28564b", "#d9fff3"),
    }
    bg, fg, active_bg, active_fg = palette.get(kind, palette["ghost"])
    disabled_fg = (
        "#ffe9a3" if kind == "apply"
        else "#d7edff" if kind == "accent"
        else "#c6def6" if kind == "soft"
        else "#aeb7c4"
    )
    widget = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active_bg,
        activeforeground=active_fg,
        disabledforeground=disabled_fg,
        relief="flat",
        bd=0,
        highlightthickness=0,
        font=font or theme.FONT_SMALL,
        cursor="hand2",
        padx=padx,
        pady=pady,
        width=width,
        takefocus=0,
    )
    if tooltip:
        Tooltip(widget, tooltip)
    return widget


def make_menu_icon(root, kind: str, color: str | None = None) -> tk.PhotoImage:
    """Create a tiny crisp menu glyph without requiring an icon package."""

    color = color or theme.ACCENT
    image = tk.PhotoImage(master=root, width=16, height=16)
    image.put(theme.PANEL_ALT, to=(0, 0, 15, 15))
    ink = color
    light = theme.BORDER_LIGHT
    if kind in {"file", "media", "save"}:
        image.put(ink, to=(3, 2, 12, 13))
        image.put(theme.PANEL_ALT, to=(5, 4, 10, 5))
        image.put(light, to=(5, 8, 10, 8))
        image.put(light, to=(5, 10, 10, 10))
    elif kind in {"undo", "redo"}:
        image.put(ink, to=(3, 7, 12, 8))
        if kind == "undo":
            image.put(ink, to=(3, 7, 6, 4))
            image.put(ink, to=(3, 8, 6, 11))
        else:
            image.put(ink, to=(9, 7, 12, 4))
            image.put(ink, to=(9, 8, 12, 11))
    elif kind in {"auto", "caption"}:
        image.put(ink, to=(3, 4, 12, 11))
        image.put(theme.PANEL_ALT, to=(5, 6, 10, 9))
        image.put(light, to=(6, 7, 9, 7))
    elif kind == "view":
        image.put(light, to=(3, 3, 12, 3))
        image.put(ink, to=(3, 6, 12, 6))
        image.put(light, to=(3, 9, 12, 9))
        image.put(ink, to=(3, 12, 8, 12))
    elif kind == "tool":
        image.put(ink, to=(4, 3, 11, 5))
        image.put(ink, to=(7, 5, 8, 12))
        image.put(light, to=(4, 12, 11, 13))
    else:
        image.put(ink, to=(4, 2, 11, 13))
        image.put(theme.PANEL_ALT, to=(7, 4, 8, 9))
        image.put(theme.PANEL_ALT, to=(7, 11, 8, 11))
    return image


def entry(
    parent,
    textvariable=None,
    width: int = 10,
    justify: str = "left",
    font=None,
    show: str | None = None,
) -> tk.Entry:
    widget = tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        justify=justify,
        font=font or theme.FONT,
        bg=theme.PANEL_ALT,
        fg=theme.TEXT,
        insertbackground=theme.ACCENT,
        disabledbackground=theme.PANEL,
        disabledforeground=theme.FAINT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=theme.BORDER,
        highlightcolor=theme.ACCENT,
        show=show,
    )
    return widget


def combo(
    parent,
    textvariable,
    values: list[str] | tuple[str, ...],
    width: int = 10,
    readonly: bool = True,
) -> ttk.Combobox:
    widget = ttk.Combobox(
        parent,
        textvariable=textvariable,
        values=list(values),
        width=width,
        state="readonly" if readonly else "normal",
        font=theme.FONT_SMALL,
        justify="center",
    )
    return widget


def check(parent, text: str, variable, command=None) -> ttk.Checkbutton:
    return ttk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        style="Card.TCheckbutton",
    )


def radio(parent, text: str, variable, value, command=None) -> ttk.Radiobutton:
    return ttk.Radiobutton(
        parent,
        text=text,
        variable=variable,
        value=value,
        command=command,
        style="Card.TRadiobutton",
    )


def card(parent, bg: str = theme.PANEL, padding: int = 0) -> tk.Frame:
    frame = tk.Frame(
        parent,
        bg=bg,
        highlightthickness=1,
        highlightbackground=theme.BORDER,
        bd=0,
    )
    return frame


def section(parent, title: str, subtitle: str | None = None, bg: str = theme.PANEL):
    frame = tk.Frame(parent, bg=bg)
    tk.Label(
        frame,
        text=title,
        bg=bg,
        fg=theme.TEXT,
        font=theme.FONT_SMALL_BOLD,
        anchor="w",
    ).pack(anchor="w")
    if subtitle:
        tk.Label(
            frame,
            text=subtitle,
            bg=bg,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=316,
        ).pack(anchor="w", pady=(3, 0))
    return frame


def hint(parent, text: str, bg: str = theme.PANEL, wraplength: int = 316):
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=theme.FAINT,
        font=theme.FONT_SMALL,
        anchor="w",
        justify="left",
        wraplength=wraplength,
    )


def divider(parent, bg: str = theme.PANEL) -> tk.Frame:
    frame = tk.Frame(parent, bg=bg, height=1)
    frame.pack_propagate(False)
    tk.Frame(frame, bg=theme.BORDER, height=1).pack(fill="x")
    return frame


def pill(parent, text: str, color: str = theme.ACCENT, bg: str = theme.ACCENT_SOFT) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=bg,
        fg=color,
        font=theme.FONT_TINY,
        padx=7,
        pady=2,
        bd=0,
    )


class ScrollableFrame(tk.Frame):
    """Vertically scrollable tab body with per-widget wheel bindings."""

    def __init__(self, parent, bg: str = theme.PANEL):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)

    def _on_body_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event) -> str:
        if self.body.winfo_height() <= self.canvas.winfo_height() + 2:
            return "break"
        steps = int(-event.delta / 120) if event.delta else 0
        if steps:
            self.canvas.yview_scroll(steps * 2, "units")
        return "break"

    def bind_wheel(self) -> None:
        def walk(widget):
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
            for child in widget.winfo_children():
                walk(child)

        walk(self.body)
        self.canvas.bind("<MouseWheel>", self._on_wheel, add="+")


class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay: int = 420):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _show(self) -> None:
        if self._tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.configure(bg=theme.BORDER)
        label = tk.Label(
            tip,
            text=self.text,
            bg=theme.PANEL_HOVER,
            fg=theme.TEXT,
            font=theme.FONT_SMALL,
            padx=8,
            pady=5,
            justify="left",
            wraplength=280,
        )
        label.pack(padx=1, pady=1)
        tip.wm_geometry(f"+{x}+{y}")
        tip.attributes("-topmost", True)
        self._tip = tip

    def _cancel(self) -> None:
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


def make_icon(root: tk.Tk) -> tk.PhotoImage:
    width = height = 64
    image = tk.PhotoImage(master=root, width=width, height=height)
    image.put(theme.BG, to=(0, 0, width, height))
    image.put("#5c9dff", to=(0, 0, width, 4))
    image.put("#5c9dff", to=(0, height - 4, width, height))
    bars = [(10, 24), (19, 40), (28, 30), (37, 48), (46, 34), (55, 20)]
    for x, bar_height in bars:
        top = (height - bar_height) // 2
        image.put("#5c9dff", to=(x, top, x + 5, top + bar_height))
    image.put("#9b8cff", to=(28, 24, 33, 40))
    return image
