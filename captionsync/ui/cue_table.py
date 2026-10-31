"""Subtitle cue table with inline start/end editing."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..models import Cue, Document
from ..timecode import format_clock, format_srt_time, parse_timecode
from . import theme
from .widgets import entry


class CueTable(tk.Frame):
    def __init__(self, master, on_select=None, on_activate=None, on_edit=None):
        super().__init__(master, bg=theme.PANEL)
        self.on_select = on_select
        self.on_activate = on_activate
        self.on_edit = on_edit

        self.doc: Document | None = None
        self.selected: int | None = None
        self._active: tuple[int, ...] = ()
        self._suppress = False
        self._editor: tk.Entry | None = None
        self._editor_index = -1
        self._editor_field = ""

        style = ttk.Style(self)
        style.configure("Cues.Treeview", font=theme.FONT_SMALL, rowheight=27)
        style.configure("Cues.Treeview.Heading", font=theme.FONT_SMALL)

        self.tree = ttk.Treeview(
            self,
            columns=("no", "start", "end", "text"),
            show="headings",
            style="Cues.Treeview",
            selectmode="browse",
        )
        self.tree.heading("no", text="#")
        self.tree.heading("start", text="시작")
        self.tree.heading("end", text="종료")
        self.tree.heading("text", text="자막 텍스트")
        self.tree.column("no", width=36, minwidth=32, anchor="center", stretch=False)
        self.tree.column("start", width=88, minwidth=84, anchor="center", stretch=False)
        self.tree.column("end", width=88, minwidth=84, anchor="center", stretch=False)
        self.tree.column("text", width=150, minwidth=100, anchor="w", stretch=True)

        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.vscroll.set)
        self.hscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=self.hscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        self.vscroll.grid(row=0, column=1, sticky="ns")
        self.hscroll.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.tree.tag_configure("odd", background=theme.PANEL, foreground=theme.TEXT)
        self.tree.tag_configure("even", background=theme.PANEL_ALT, foreground=theme.TEXT)
        self.tree.tag_configure(
            "active", background="#193a34", foreground="#d9fff3"
        )
        self.tree.tag_configure(
            "selected", background=theme.ACCENT_DIM, foreground="#ffffff"
        )

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Escape>", lambda _event: self.tree.selection_remove(self.tree.selection()))

    # ------------------------------------------------------------------
    def set_document(self, doc: Document | None, keep_view: bool = False) -> None:
        yview = self.tree.yview()[0] if keep_view and self.tree.get_children() else 0.0
        self._close_editor()
        self.doc = doc
        self.selected = None
        self._active = ()
        self.tree.delete(*self.tree.get_children())
        if doc:
            for index, cue in enumerate(doc.cues):
                tag = "even" if index % 2 else "odd"
                self.tree.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=self._values(index, cue),
                    tags=(tag,),
                )
        if keep_view and yview:
            self.tree.yview_moveto(yview)

    def _values(self, index: int, cue: Cue):
        text = cue.display_text()
        if len(text) > 140:
            text = text[:137] + "…"
        return (index + 1, format_clock(cue.start), format_clock(cue.end), text)

    def refresh(self, doc: Document | None = None) -> None:
        if doc is not None:
            self.doc = doc
        if not self.doc:
            self.tree.delete(*self.tree.get_children())
            return
        if len(self.tree.get_children()) != len(self.doc.cues):
            self.set_document(self.doc, keep_view=True)
            return
        for index, cue in enumerate(self.doc.cues):
            iid = str(index)
            if not self.tree.exists(iid):
                self.set_document(self.doc, keep_view=True)
                return
            self.tree.item(iid, values=self._values(index, cue))

    def update_row(self, index: int) -> None:
        if not self.doc or index < 0 or index >= len(self.doc.cues):
            return
        iid = str(index)
        if self.tree.exists(iid):
            self.tree.item(iid, values=self._values(index, self.doc.cues[index]))

    def select(self, index: int | None, scroll: bool = True) -> None:
        self._close_editor()
        self.selected = index
        self._suppress = True
        try:
            selection = self.tree.selection()
            if selection:
                self.tree.selection_remove(*selection)
            if index is not None and 0 <= index < len(self.tree.get_children()):
                iid = str(index)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                if scroll:
                    self.tree.see(iid)
        finally:
            # <<TreeviewSelect>> is delivered after this method returns, so
            # keep the guard until the next idle cycle.
            self.after_idle(self._release_suppress)
        self._apply_active_tags()

    def _release_suppress(self) -> None:
        self._suppress = False

    def set_active(self, indices: tuple[int, ...]) -> None:
        if indices == self._active:
            return
        previous = set(self._active)
        current = set(indices)
        self._active = tuple(indices)
        for index in previous | current:
            iid = str(index)
            if not self.tree.exists(iid):
                continue
            base = "even" if index % 2 else "odd"
            tags = (base, "active") if index in current else (base,)
            if index == self.selected:
                tags = tags + ("selected",)
            self.tree.item(iid, tags=tags)

    def _apply_active_tags(self) -> None:
        for index in range(len(self.tree.get_children())):
            iid = str(index)
            if not self.tree.exists(iid):
                continue
            base = "even" if index % 2 else "odd"
            tags = (base,)
            if index in self._active:
                tags = tags + ("active",)
            if index == self.selected:
                tags = tags + ("selected",)
            self.tree.item(iid, tags=tags)

    # ------------------------------------------------------------------
    def _on_tree_select(self, _event=None) -> None:
        if self._suppress:
            return
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        self.selected = index
        if self.on_select:
            self.on_select(index)

    def _on_double(self, event) -> None:
        if not self.doc:
            return
        row = self.tree.identify_row(event.y)
        if not row:
            return
        index = int(row)
        column = self.tree.identify_column(event.x)
        field = {"#2": "start", "#3": "end"}.get(column)
        if field:
            self._open_editor(index, field)
        elif self.on_activate:
            self.on_activate(index)

    def _on_right_click(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        index = int(row)
        cue = self.doc.cues[index] if self.doc else None
        if not cue:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(
            label="이 자막으로 재생 위치 이동",
            command=lambda: self.on_activate and self.on_activate(index),
        )
        menu.add_command(
            label="시작 시간 복사",
            command=lambda: self._copy_value(cue.start),
        )
        menu.add_command(
            label="종료 시간 복사",
            command=lambda: self._copy_value(cue.end),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_value(self, seconds: float) -> None:
        self.clipboard_clear()
        self.clipboard_append(format_clock(seconds))

    def paste_into_selected(self, field: str) -> None:
        if not self.doc or self.selected is None:
            return
        try:
            value = parse_timecode(self.clipboard_get())
        except (tk.TclError, ValueError):
            return
        if self.on_edit:
            self.on_edit(self.selected, field, value)

    # ------------------------------------------------------------------
    def _open_editor(self, index: int, field: str) -> None:
        if not self.doc or index >= len(self.doc.cues):
            return
        self._close_editor()
        column = "#2" if field == "start" else "#3"
        bbox = self.tree.bbox(str(index), column)
        if not bbox:
            return
        x, y, width, height = bbox
        cue = self.doc.cues[index]
        value = format_srt_time(cue.start if field == "start" else cue.end)
        variable = tk.StringVar(value=value)
        editor = tk.Entry(
            self.tree,
            textvariable=variable,
            bg=theme.ACCENT_SOFT,
            fg="#ffffff",
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.ACCENT,
            highlightcolor=theme.ACCENT,
            font=(theme.MONO[0], 9),
            justify="center",
        )
        editor.place(x=x, y=y, width=width, height=height)
        editor.focus_set()
        editor.select_range(0, "end")
        editor.icursor("end")
        self._editor = editor
        self._editor_index = index
        self._editor_field = field

        def commit(_event=None):
            self._commit_editor()

        def cancel(_event=None):
            self._close_editor()

        editor.bind("<Return>", commit)
        editor.bind("<KP_Enter>", commit)
        editor.bind("<Escape>", cancel)
        editor.bind("<FocusOut>", commit)

    def _commit_editor(self) -> None:
        editor = self._editor
        if not editor:
            return
        value = editor.get()
        index = self._editor_index
        field = self._editor_field
        self._editor = None
        self._editor_index = -1
        self._editor_field = ""
        editor.destroy()
        try:
            seconds = parse_timecode(value)
        except ValueError:
            return
        if self.on_edit and index >= 0:
            self.on_edit(index, field, seconds)

    def _close_editor(self) -> None:
        if self._editor:
            editor = self._editor
            self._editor = None
            self._editor_index = -1
            self._editor_field = ""
            editor.destroy()
