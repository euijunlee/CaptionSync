"""CaptionSync main window."""

from __future__ import annotations

import queue
import shutil
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .. import __version__
from ..audio import AudioError, MediaInfo, detect_speech, find_ffmpeg, prepare_media
from ..config import load_config, save_config
from ..formats import (
    SubtitleError,
    kind_from_suffix,
    load_subtitle,
    save_subtitle,
)
from ..models import MIN_DURATION, Document, History, clone_cues
from ..autosync import apply_auto_plan, build_auto_plan
from ..player import PlaybackController
from ..timecode import format_clock, format_duration, parse_timecode
from ..transforms import (
    auto_align,
    map_two_points,
    nearest_end,
    nearest_start,
    scale_fps,
    set_cue_times,
    shift_all,
    shift_from_index,
    shift_from_time,
    shift_index_range,
    suggest_offset,
)
from ..transcription import TranscriptionError, available_backend, transcribe_korean
from . import theme
from .cue_table import CueTable
from .timeline import Timeline
from .tools_panel import ToolsPanel
from .widgets import button, make_icon, pill

SHORTCUTS = [
    ("Space", "재생 / 일시정지"),
    ("← / →", "1초 뒤로 / 앞으로"),
    ("Shift + ← / →", "0.1초 뒤로 / 앞으로"),
    ("Ctrl + ← / →", "10초 뒤로 / 앞으로"),
    ("Alt + ← / →", "선택 자막 전체 ±0.05초"),
    ("Alt+Shift + ← / →", "선택 자막 시작 ±0.05초"),
    ("Alt+Ctrl + ← / →", "선택 자막 끝 ±0.05초"),
    ("Ctrl + O", "자막 열기"),
    ("Ctrl + Shift + O", "미디어 열기"),
    ("Ctrl + S", "저장"),
    ("Ctrl + Z / Ctrl + Y", "되돌리기 / 다시 실행"),
    ("Ctrl + 0", "타임라인 전체 보기"),
    ("Ctrl + +/-", "타임라인 확대 / 축소"),
    ("Delete", "선택 자막 삭제"),
    ("Esc", "일시정지"),
]


class MainWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.config = load_config()
        self.doc = Document()
        self.history = History()
        self.player = PlaybackController()
        self.media: MediaInfo | None = None
        self.analysis = None
        self.segments: list[tuple[float, float]] = []
        self.selected_index: int | None = None
        self.playhead = 0.0
        self.work_dir = Path(tempfile.mkdtemp(prefix="captionsync-"))

        self.ffmpeg_path = self.config.get("ffmpeg_path") or None
        self.sensitivity = float(self.config.get("sensitivity", 50.0))
        self._media_token = 0
        self._media_queue: queue.Queue = queue.Queue()
        self._media_loading = False
        self._drag_before: list | None = None
        self._drag_label = ""
        self._was_playing = False
        self._closing = False
        self._preview_wraplength = 600
        self._auto_sync_key = None

        self._icon = make_icon(root)
        root.iconphoto(True, self._icon)
        root.title("CaptionSync — 자막 싱크 스튜디오")
        root.minsize(1180, 760)
        root.configure(bg=theme.BG)

        self._build_menu()
        self._build_ui()
        self._bind_keys()
        self._apply_initial_state()
        self._restore_layout()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(80, self._poll)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root, tearoff=0)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="자막 열기…", accelerator="Ctrl+O", command=self.open_subtitle_dialog
        )
        file_menu.add_command(
            label="미디어 열기…",
            accelerator="Ctrl+Shift+O",
            command=self.open_media_dialog,
        )
        file_menu.add_separator()
        file_menu.add_command(label="저장", accelerator="Ctrl+S", command=self.save)
        file_menu.add_command(
            label="다른 이름으로 저장…", accelerator="Ctrl+Shift+S", command=self.save_as
        )
        file_menu.add_separator()
        file_menu.add_command(label="종료", command=self.on_close)
        menubar.add_cascade(label="파일", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=0)
        edit_menu.add_command(label="되돌리기", accelerator="Ctrl+Z", command=self.undo)
        edit_menu.add_command(
            label="다시 실행", accelerator="Ctrl+Y", command=self.redo
        )
        edit_menu.add_separator()
        edit_menu.add_command(label="선택 자막 삭제", accelerator="Delete", command=self.delete_selected)
        edit_menu.add_command(label="시간순으로 정렬", command=self.sort_cues)
        menubar.add_cascade(label="편집", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=0)
        view_menu.add_command(
            label="타임라인 전체 보기", accelerator="Ctrl+0", command=self.timeline_fit
        )
        view_menu.add_command(label="확대", accelerator="Ctrl++", command=lambda: self.timeline_zoom(1.5))
        view_menu.add_command(label="축소", accelerator="Ctrl+-", command=lambda: self.timeline_zoom(1 / 1.5))
        view_menu.add_separator()
        self.snap_var = tk.BooleanVar(value=bool(self.config.get("snap", True)))
        view_menu.add_checkbutton(
            label="드래그 시 음성 경계에 스냅",
            variable=self.snap_var,
            command=self._toggle_snap,
        )
        menubar.add_cascade(label="보기", menu=view_menu)

        tool_menu = tk.Menu(menubar, tearoff=0)
        tool_menu.add_command(label="전체 이동", command=lambda: self._focus_tool(0))
        tool_menu.add_command(label="비율 · 드리프트 보정", command=lambda: self._focus_tool(1))
        tool_menu.add_command(label="FPS 보정", command=lambda: self._focus_tool(2))
        tool_menu.add_command(label="구간 보정", command=lambda: self._focus_tool(3))
        tool_menu.add_command(label="음성 자동 정렬", command=lambda: self._focus_tool(4))
        tool_menu.add_separator()
        tool_menu.add_command(label="자동 싱크 실행", accelerator="Ctrl+Shift+A", command=self.run_auto_sync)
        tool_menu.add_command(label="영상에서 한글 자막 생성", command=self.generate_korean_subtitles)
        tool_menu.add_separator()
        tool_menu.add_command(label="FFmpeg 경로 지정…", command=self.choose_ffmpeg)
        tool_menu.add_command(label="FFmpeg 감지 상태", command=self.show_ffmpeg_status)
        menubar.add_cascade(label="도구", menu=tool_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="사용법", accelerator="F1", command=self.show_help)
        help_menu.add_command(label="CaptionSync 정보", command=self.show_about)
        menubar.add_cascade(label="도움말", menu=help_menu)

        self.root.configure(menu=menubar)

    def _build_ui(self) -> None:
        top = tk.Frame(self.root, bg=theme.BG, height=84)
        top.pack(fill="x")
        top.pack_propagate(False)

        brand = tk.Frame(top, bg=theme.BG)
        brand.pack(side="left", padx=(16, 0), pady=10)
        tk.Label(brand, image=self._icon, bg=theme.BG, bd=0).pack(side="left")
        title_box = tk.Frame(brand, bg=theme.BG)
        title_box.pack(side="left", padx=(9, 0))
        title_row = tk.Frame(title_box, bg=theme.BG)
        title_row.pack(anchor="w")
        tk.Label(
            title_row,
            text="CaptionSync",
            bg=theme.BG,
            fg=theme.TEXT,
            font=(theme.FONT[0], 16, "bold"),
        ).pack(side="left")
        pill(title_row, "SYNC STUDIO", theme.VIOLET, "#241f3d").pack(
            side="left", padx=(8, 0), pady=(2, 0)
        )
        tk.Label(
            title_box,
            text="자막 싱크 보정 데스크톱 스튜디오",
            bg=theme.BG,
            fg=theme.FAINT,
            font=theme.FONT_TINY,
        ).pack(anchor="w")

        actions = tk.Frame(top, bg=theme.BG)
        actions.pack(side="left", padx=(24, 0), pady=26)
        self.open_btn = button(
            actions,
            "자막 열기",
            self.open_subtitle_dialog,
            kind="accent",
            pady=6,
            tooltip="SRT · VTT · ASS 파일 열기  (Ctrl+O)",
        )
        self.open_btn.pack(side="left")
        self.media_btn = button(
            actions,
            "미디어 열기",
            self.open_media_dialog,
            kind="ghost",
            pady=6,
            tooltip="영상/오디오 열기 · MP4는 FFmpeg 필요  (Ctrl+Shift+O)",
        )
        self.media_btn.pack(side="left", padx=(6, 0))
        self.auto_btn = button(
            actions,
            "자동 싱크",
            self.run_auto_sync,
            kind="soft",
            pady=6,
            tooltip="영상 음성과 자막을 자동으로 맞춥니다  (Ctrl+Shift+A)",
        )
        self.auto_btn.pack(side="left", padx=(6, 0))
        self.caption_btn = button(
            actions,
            "한글 자막 생성",
            self.generate_korean_subtitles,
            kind="ghost",
            pady=6,
            tooltip="영상 음성을 인식해 한국어 자막을 생성합니다",
        )
        self.caption_btn.pack(side="left", padx=(6, 0))
        self.save_btn = button(
            actions,
            "저장",
            self.save,
            kind="ghost",
            padx=14,
            pady=6,
            tooltip="현재 자막 저장  (Ctrl+S)",
        )
        self.save_btn.pack(side="left", padx=(6, 0))
        tk.Frame(actions, bg=theme.BORDER, width=1, height=24).pack(
            side="left", padx=10
        )
        self.undo_btn = button(
            actions, "↶ 되돌리기", self.undo, kind="bare", padx=8, pady=4,
            font=theme.FONT_TINY, tooltip="되돌리기  (Ctrl+Z)",
        )
        self.undo_btn.pack(side="left")
        self.redo_btn = button(
            actions, "↷ 다시 실행", self.redo, kind="bare", padx=8, pady=4,
            font=theme.FONT_TINY, tooltip="다시 실행  (Ctrl+Y)",
        )
        self.redo_btn.pack(side="left", padx=(2, 0))

        right = tk.Frame(top, bg=theme.BG)
        right.pack(side="right", padx=16, pady=23)
        self.file_label = tk.Label(
            right,
            text="자막 없음",
            bg=theme.BG,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="e",
        )
        self.file_label.pack(anchor="e")
        self.media_label = tk.Label(
            right,
            text="미디어 없음",
            bg=theme.BG,
            fg=theme.FAINT,
            font=theme.FONT_TINY,
            anchor="e",
        )
        self.media_label.pack(anchor="e", pady=(2, 0))

        tk.Frame(self.root, bg=theme.BORDER, height=1).pack(fill="x")

        status_bar = tk.Frame(self.root, bg=theme.PANEL_ALT, height=28)
        status_bar.pack(side="bottom", fill="x")
        status_bar.pack_propagate(False)
        self.status_label = tk.Label(
            status_bar,
            text="●  자막 파일을 열어 시작하세요.",
            bg=theme.PANEL_ALT,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
        )
        self.status_label.pack(side="left", padx=12)
        self.progress_label = tk.Label(
            status_bar,
            text="",
            bg=theme.PANEL_ALT,
            fg=theme.ACCENT,
            font=theme.FONT_SMALL,
            anchor="e",
        )
        self.progress_label.pack(side="right", padx=(0, 12))
        self.info_label = tk.Label(
            status_bar,
            text="0개 자막 · UTF-8 · 미디어 없음",
            bg=theme.PANEL_ALT,
            fg=theme.FAINT,
            font=theme.FONT_TINY,
            anchor="e",
        )
        self.info_label.pack(side="right", padx=(0, 14))

        self.paned = tk.PanedWindow(
            self.root,
            orient="horizontal",
            bg=theme.BORDER,
            sashwidth=5,
            bd=0,
            relief="flat",
            sashrelief="flat",
            showhandle=False,
        )
        self.paned.pack(fill="both", expand=True)

        left = tk.Frame(self.paned, bg=theme.PANEL)
        center = tk.Frame(self.paned, bg=theme.BG)
        right_panel = tk.Frame(self.paned, bg=theme.PANEL)
        self.paned.add(left, minsize=330, width=424)
        self.paned.add(center, minsize=620, width=760)
        self.paned.add(right_panel, minsize=354, width=392)

        self._build_left(left)
        self._build_center(center)
        self.tools = ToolsPanel(right_panel, self)
        self.tools.pack(fill="both", expand=True)

    def _build_left(self, parent) -> None:
        header = tk.Frame(parent, bg=theme.PANEL)
        header.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(
            header,
            text="자막 목록",
            bg=theme.PANEL,
            fg=theme.TEXT,
            font=theme.FONT_BIG,
            anchor="w",
        ).pack(side="left")
        self.count_pill = pill(header, "0개", theme.MUTED, theme.PANEL_HOVER)
        self.count_pill.pack(side="left", padx=(8, 0), pady=(1, 0))

        table_card = tk.Frame(
            parent,
            bg=theme.PANEL,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        table_card.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.table = CueTable(
            table_card,
            on_select=self._on_table_select,
            on_activate=self._on_table_activate,
            on_edit=self.set_cue_field,
        )
        self.table.pack(fill="both", expand=True)

        editor = tk.Frame(
            parent,
            bg=theme.PANEL,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        editor.pack(fill="x", padx=12, pady=(0, 12))
        inner = tk.Frame(editor, bg=theme.PANEL)
        inner.pack(fill="x", padx=12, pady=10)

        top_row = tk.Frame(inner, bg=theme.PANEL)
        top_row.pack(fill="x")
        self.cue_title = tk.Label(
            top_row,
            text="선택 자막 없음",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL_BOLD,
            anchor="w",
        )
        self.cue_title.pack(side="left")
        self.cue_duration = tk.Label(
            top_row,
            text="",
            bg=theme.PANEL,
            fg=theme.FAINT,
            font=theme.FONT_TINY,
            anchor="e",
        )
        self.cue_duration.pack(side="right")

        self.cue_start_var = tk.StringVar()
        self.cue_end_var = tk.StringVar()

        for label, variable, field in (
            ("시작", self.cue_start_var, "start"),
            ("종료", self.cue_end_var, "end"),
        ):
            row = tk.Frame(inner, bg=theme.PANEL)
            row.pack(fill="x", pady=(6, 0))
            tk.Label(
                row,
                text=label,
                bg=theme.PANEL,
                fg=theme.MUTED,
                font=theme.FONT_SMALL,
                width=4,
                anchor="w",
            ).pack(side="left")
            widget = tk.Entry(
                row,
                textvariable=variable,
                font=theme.MONO,
                bg=theme.PANEL_ALT,
                fg=theme.TEXT,
                insertbackground=theme.ACCENT,
                relief="flat",
                bd=0,
                highlightthickness=1,
                highlightbackground=theme.BORDER,
                highlightcolor=theme.ACCENT,
                justify="center",
            )
            widget.pack(side="left", fill="x", expand=True, ipady=2, padx=(0, 5))
            self._cue_entries = getattr(self, "_cue_entries", [])
            self._cue_entries.append(widget)
            button(
                row,
                "현재",
                lambda f=field: self._set_field_from_playhead(f),
                kind="bare",
                padx=7,
                pady=2,
                font=theme.FONT_TINY,
                tooltip="현재 재생 위치를 이 값으로 적용",
            ).pack(side="left")

        action_row = tk.Frame(inner, bg=theme.PANEL)
        action_row.pack(fill="x", pady=(8, 0))
        self.cue_apply_btn = button(
            action_row,
            "적용",
            self._apply_cue_editor,
            kind="accent",
            padx=8,
            pady=4,
            font=theme.FONT_TINY,
        )
        self.cue_apply_btn.pack(side="left", fill="x", expand=True)
        self.cue_nudge_buttons = []
        for label, field, delta in (
            ("시작−", "start", -0.1),
            ("시작+", "start", 0.1),
            ("끝−", "end", -0.1),
            ("끝+", "end", 0.1),
        ):
            btn = button(
                action_row,
                label,
                lambda f=field, d=delta: self.nudge_selected(f, d),
                kind="ghost",
                padx=4,
                pady=4,
                font=theme.FONT_TINY,
            )
            btn.pack(side="left", padx=(4, 0))
            self.cue_nudge_buttons.append(btn)

    def _build_center(self, parent) -> None:
        preview = tk.Frame(
            parent,
            bg="#070a0f",
            height=148,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        preview.pack(fill="x", padx=12, pady=(12, 8))
        preview.pack_propagate(False)
        preview_header = tk.Frame(preview, bg="#070a0f")
        preview_header.pack(fill="x", padx=14, pady=(10, 0))
        tk.Label(
            preview_header,
            text="PREVIEW",
            bg="#070a0f",
            fg=theme.FAINT,
            font=(theme.FONT[0], 8, "bold"),
        ).pack(side="left")
        self.preview_meta = tk.Label(
            preview_header,
            text="대기 중",
            bg="#070a0f",
            fg=theme.FAINT,
            font=theme.FONT_TINY,
        )
        self.preview_meta.pack(side="right")

        self.preview_label = tk.Label(
            preview,
            text="자막 파일을 열고 재생하면 현재 자막이 여기에 표시됩니다.",
            bg="#070a0f",
            fg=theme.FAINT,
            font=theme.FONT_SMALL,
            wraplength=self._preview_wraplength,
            justify="center",
        )
        self.preview_label.pack(expand=True, fill="both", padx=28, pady=(0, 8))
        preview.bind("<Configure>", self._on_preview_configure)

        timeline_card = tk.Frame(
            parent,
            bg="#0b0f16",
            highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        timeline_card.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.timeline = Timeline(
            timeline_card,
            on_seek=self._on_timeline_seek,
            on_select=self._on_timeline_select,
            on_cue_drag=self._on_cue_drag,
            on_view_change=self._on_view_change,
        )
        self.timeline.pack(fill="both", expand=True, padx=1, pady=1)
        self.timeline.set_snap_enabled(self.snap_var.get())

        transport = tk.Frame(
            parent,
            bg=theme.PANEL,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        transport.pack(fill="x", padx=12, pady=(0, 12))
        bar = tk.Frame(transport, bg=theme.PANEL)
        bar.pack(fill="x", padx=9, pady=7)

        left = tk.Frame(bar, bg=theme.PANEL)
        left.pack(side="left")
        self.play_btn = button(
            left,
            "재생",
            self.toggle_play,
            kind="accent",
            padx=18,
            pady=6,
            tooltip="재생 / 일시정지  (Space)",
        )
        self.play_btn.pack(side="left")
        button(
            left, "−5초", lambda: self.seek(self.playhead - 5), kind="ghost", padx=8, pady=6
        ).pack(side="left", padx=(5, 0))
        button(
            left, "+5초", lambda: self.seek(self.playhead + 5), kind="ghost", padx=8, pady=6
        ).pack(side="left", padx=(3, 0))

        self.time_label = tk.Label(
            bar,
            text="00:00:00.000 / 00:00:00.000",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.MONO,
            anchor="w",
        )
        self.time_label.pack(side="left", padx=(14, 0))

        right = tk.Frame(bar, bg=theme.PANEL)
        right.pack(side="right")
        ttk.Checkbutton(
            right,
            text="음성 스냅",
            variable=self.snap_var,
            command=self._toggle_snap,
            style="Card.TCheckbutton",
        ).pack(side="left", padx=(0, 8))
        button(
            right, "−", lambda: self.timeline_zoom(1 / 1.5), kind="bare", padx=7, pady=5
        ).pack(side="left")
        button(
            right, "+", lambda: self.timeline_zoom(1.5), kind="bare", padx=7, pady=5
        ).pack(side="left", padx=(2, 0))
        button(
            right,
            "전체",
            self.timeline_fit,
            kind="ghost",
            padx=9,
            pady=5,
            tooltip="타임라인 전체 보기  (Ctrl+0)",
        ).pack(side="left", padx=(5, 0))

    def _apply_initial_state(self) -> None:
        self.table.set_document(self.doc)
        self.timeline.set_document(self.doc)
        self.timeline.set_media_duration(0.0)
        self.tools.on_document_changed()
        self._fill_cue_editor()
        self._update_history_buttons()
        self._update_document_meta()
        self._update_transport()
        self._update_preview()
        self.set_status("자막 파일을 열어 시작하세요. (Ctrl+O)", "info")

    def _restore_layout(self) -> None:
        geometry = self.config.get("geometry")
        if isinstance(geometry, str) and "x" in geometry:
            try:
                self.root.geometry(geometry)
            except tk.TclError:
                self._default_geometry()
        else:
            self._default_geometry()

        self._sashes_placed = False
        self.paned.bind("<Configure>", self._on_paned_configure, add="+")
        self.root.after(90, self._on_paned_configure)

    def _default_geometry(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(1560, max(1180, screen_w - 80))
        height = min(960, max(760, screen_h - 80))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _on_paned_configure(self, event=None) -> None:
        if getattr(self, "_sashes_placed", False):
            return
        try:
            total = self.paned.winfo_width()
        except tk.TclError:
            return
        if total < 900:
            return
        self._sashes_placed = True
        # Let Tk finish the current layout pass before moving the sashes,
        # otherwise the placement is undone when the panes are first mapped.
        try:
            self.root.after_idle(lambda: self._place_sashes(total))
        except tk.TclError:
            pass

    def _place_sashes(self, total: int) -> None:
        try:
            total = self.paned.winfo_width() or total
            sash = self.config.get("sash")
            saved_total = self.config.get("sash_total")
            scale = 1.0
            if isinstance(saved_total, (int, float)) and saved_total > 600:
                scale = total / float(saved_total)
            if isinstance(sash, list) and len(sash) == 2:
                first = int(sash[0] * scale)
                second = int(sash[1] * scale)
            else:
                first = 424
                second = total - 392
            if not (300 < first < total - 600 and first < second < total - 300):
                first = 424
                second = total - 392
            self.paned.sash_place(0, first, 0)
            self.paned.sash_place(1, second, 0)
        except tk.TclError:
            self._sashes_placed = False

    # ------------------------------------------------------------------
    # Key bindings
    # ------------------------------------------------------------------
    def _bind_keys(self) -> None:
        def always(func):
            def handler(_event=None):
                func()
                return "break"

            return handler

        def when_not_typing(func):
            def handler(_event=None):
                if self._is_typing():
                    return None
                func()
                return "break"

            return handler

        bindings = {
            "<Control-o>": self.open_subtitle_dialog,
            "<Control-O>": self.open_media_dialog,
            "<Control-s>": self.save,
            "<Control-S>": self.save_as,
            "<Control-Shift-A>": self.run_auto_sync,
            "<Control-z>": self.undo,
            "<Control-y>": self.redo,
            "<Control-Z>": self.redo,
            "<Control-Key-0>": self.timeline_fit,
            "<Control-plus>": lambda: self.timeline_zoom(1.5),
            "<Control-equal>": lambda: self.timeline_zoom(1.5),
            "<Control-minus>": lambda: self.timeline_zoom(1 / 1.5),
            "<F1>": self.show_help,
        }
        for sequence, func in bindings.items():
            self.root.bind_all(sequence, always(func))

        self.root.bind_all("<space>", when_not_typing(self.toggle_play))
        self.root.bind_all("<Left>", when_not_typing(lambda: self.seek(self.playhead - 1.0)))
        self.root.bind_all("<Right>", when_not_typing(lambda: self.seek(self.playhead + 1.0)))
        self.root.bind_all(
            "<Shift-Left>", when_not_typing(lambda: self.seek(self.playhead - 0.1))
        )
        self.root.bind_all(
            "<Shift-Right>", when_not_typing(lambda: self.seek(self.playhead + 0.1))
        )
        self.root.bind_all(
            "<Control-Left>", when_not_typing(lambda: self.seek(self.playhead - 10.0))
        )
        self.root.bind_all(
            "<Control-Right>", when_not_typing(lambda: self.seek(self.playhead + 10.0))
        )
        self.root.bind_all(
            "<Alt-Left>", when_not_typing(lambda: self.nudge_selected("both", -0.05))
        )
        self.root.bind_all(
            "<Alt-Right>", when_not_typing(lambda: self.nudge_selected("both", 0.05))
        )
        self.root.bind_all(
            "<Alt-Shift-Left>", when_not_typing(lambda: self.nudge_selected("start", -0.05))
        )
        self.root.bind_all(
            "<Alt-Shift-Right>", when_not_typing(lambda: self.nudge_selected("start", 0.05))
        )
        self.root.bind_all(
            "<Alt-Control-Left>", when_not_typing(lambda: self.nudge_selected("end", -0.05))
        )
        self.root.bind_all(
            "<Alt-Control-Right>", when_not_typing(lambda: self.nudge_selected("end", 0.05))
        )
        self.root.bind_all("<Delete>", when_not_typing(self.delete_selected))
        self.root.bind_all("<Escape>", when_not_typing(self._pause))
        self.root.bind_all("<Home>", when_not_typing(lambda: self.seek(0.0)))

    def _is_typing(self) -> bool:
        widget = self.root.focus_get()
        return isinstance(
            widget,
            (
                tk.Entry,
                ttk.Entry,
                ttk.Combobox,
                ttk.Spinbox,
                tk.Spinbox,
                tk.Text,
                ttk.Checkbutton,
                ttk.Radiobutton,
                ttk.Scale,
            ),
        )

    # ------------------------------------------------------------------
    # Document state
    # ------------------------------------------------------------------
    def _require_doc(self) -> bool:
        if not self.doc.cues:
            self.set_status("자막 파일을 먼저 여세요.", "warn")
            return False
        return True

    def _commit_edit(self, label: str, func):
        before = clone_cues(self.doc.cues)
        try:
            result = func(self.doc.cues)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            self.doc.cues = before
            messagebox.showerror("보정할 수 없습니다", str(exc), parent=self.root)
            self.set_status(str(exc), "error")
            return False, None
        self.history.push_snapshot(label, before)
        self.doc.dirty = True
        if self.selected_index is not None and self.selected_index >= len(self.doc.cues):
            self.selected_index = None
        self._refresh_after_edit()
        return True, result

    def _refresh_after_edit(self) -> None:
        self.table.refresh(self.doc)
        self.timeline.doc = self.doc
        self.timeline.redraw()
        self.timeline.set_selected(self.selected_index)
        self.table.select(self.selected_index, scroll=False)
        self._fill_cue_editor()
        self._sync_playhead()
        self.tools.refresh()
        self._update_history_buttons()
        self._update_document_meta()

    def _after_document_loaded(self, doc: Document, message: str) -> None:
        self.doc = doc
        self.history.clear()
        self.selected_index = None
        self.playhead = 0.0
        self.player.pause()
        self.player.seek(0.0)

        self.table.set_document(doc)
        self.timeline.doc = doc
        self.timeline.set_document(doc)
        self.timeline.set_playhead(0.0)
        self.tools.on_document_changed()
        self._fill_cue_editor()
        self._sync_playhead()
        self._update_history_buttons()
        self._update_document_meta()
        self._update_transport()
        self._update_preview()
        self.set_status(message, "ok")
        self.config["last_dir"] = str(doc.path.parent) if doc.path else ""
        save_config(self.config)

    def _sync_playhead(self) -> None:
        self.timeline.set_playhead(self.playhead)
        self.table.set_active(tuple(self.doc.cue_at(self.playhead)) if self.doc else ())
        self._update_preview()
        self._update_transport()
        self._update_info()

    def _update_history_buttons(self) -> None:
        self.undo_btn.configure(state="normal" if self.history.can_undo else "disabled")
        self.redo_btn.configure(state="normal" if self.history.can_redo else "disabled")

    def _update_document_meta(self) -> None:
        count = len(self.doc.cues)
        dirty = self.doc.dirty
        if self.doc.path:
            name = self.doc.path.name
            self.file_label.configure(
                text=f"{name}{'  ·  수정됨' if dirty else ''}", fg=theme.TEXT if dirty else theme.MUTED
            )
            self.root.title(f"CaptionSync — {name}{' *' if dirty else ''}")
        else:
            self.file_label.configure(text="자막 없음", fg=theme.MUTED)
            self.root.title("CaptionSync — 자막 싱크 스튜디오")
        self.count_pill.configure(text=f"{count}개")
        if self.media:
            media_name = self.media.source.name
            if len(media_name) > 32:
                media_name = media_name[:29] + "…"
            duration = format_duration(self.media.duration) if self.media.duration else "길이 미확인"
            self.media_label.configure(
                text=f"{media_name}  ·  {duration}",
                fg=theme.MUTED,
            )
        else:
            self.media_label.configure(text="미디어 없음  ·  Ctrl+Shift+O", fg=theme.FAINT)
        self._update_info()
        self.save_btn.configure(state="normal" if count else "disabled")

    def _update_info(self) -> None:
        count = len(self.doc.cues)
        encoding = (self.doc.encoding or "utf-8").upper()
        media = "미디어 없음"
        if self.media:
            media = self.media.source.name
            if len(media) > 24:
                media = media[:21] + "…"
        span = self.timeline.view_span if hasattr(self, "timeline") else 120
        self.info_label.configure(
            text=f"{count}개 자막  ·  {encoding}  ·  {media}  ·  보기 {span:.0f}초"
        )

    def _update_transport(self) -> None:
        end = self._play_end()
        self.time_label.configure(
            text=f"{format_clock(self.playhead)} / {format_clock(end)}"
        )
        self.play_btn.configure(text="일시정지" if self.player.playing else "재생")

    def _update_preview(self) -> None:
        if not self.doc.cues:
            self.preview_label.configure(
                text="자막 파일을 열고 재생하면 현재 자막이 여기에 표시됩니다.",
                fg=theme.FAINT,
                font=theme.FONT_SMALL,
            )
            self.preview_meta.configure(text="대기 중")
            return
        active = self.doc.cue_at(self.playhead)
        if not active:
            self.preview_label.configure(
                text="", fg=theme.FAINT, font=theme.FONT_SMALL
            )
            self.preview_meta.configure(text=format_clock(self.playhead))
            return
        lines = [self.doc.cues[index].display_text() for index in active[:2]]
        self.preview_label.configure(
            text="\n".join(lines),
            fg=theme.TEXT,
            font=(theme.FONT[0], 16, "bold"),
        )
        if len(active) > 2:
            self.preview_meta.configure(text=f"자막 {len(active)}개 중 2개 표시")
        else:
            self.preview_meta.configure(text=f"자막 {len(active)}개 표시 중")

    def _on_preview_configure(self, event) -> None:
        self._preview_wraplength = max(240, event.width - 70)
        self.preview_label.configure(wraplength=self._preview_wraplength)

    def _fill_cue_editor(self) -> None:
        valid = (
            self.selected_index is not None
            and 0 <= self.selected_index < len(self.doc.cues)
        )
        state = "normal" if valid else "disabled"
        for widget in getattr(self, "_cue_entries", []):
            widget.configure(state=state)
        self.cue_apply_btn.configure(state=state)
        for widget in getattr(self, "cue_nudge_buttons", []):
            widget.configure(state=state)

        if not valid:
            self.cue_title.configure(text="선택 자막 없음", fg=theme.MUTED)
            self.cue_duration.configure(text="")
            self.cue_start_var.set("")
            self.cue_end_var.set("")
            return

        cue = self.doc.cues[self.selected_index]
        self.cue_title.configure(
            text=f"선택 자막  #{self.selected_index + 1}", fg=theme.TEXT
        )
        self.cue_duration.configure(
            text=f"길이 {cue.duration:.3f}초", fg=theme.MUTED
        )
        self.cue_start_var.set(format_clock(cue.start))
        self.cue_end_var.set(format_clock(cue.end))

    def _apply_cue_editor(self) -> None:
        if self.selected_index is None or not self._require_doc():
            return
        try:
            start = parse_timecode(self.cue_start_var.get())
            end = parse_timecode(self.cue_end_var.get())
        except ValueError as exc:
            self.set_status(str(exc), "error")
            return
        if end <= start:
            self.set_status("종료 시간은 시작 시간보다 뒤여야 합니다.", "error")
            return
        index = self.selected_index
        ok, _ = self._commit_edit(
            "자막 시간 수정",
            lambda cues: set_cue_times(cues[index], start=start, end=end),
        )
        if ok:
            self.set_status(
                f"자막 #{index + 1} 시간을 {format_clock(start)} ~ {format_clock(end)}로 수정했습니다.",
                "ok",
            )

    def _set_field_from_playhead(self, field: str) -> None:
        if self.selected_index is None:
            self.set_status("먼저 자막을 선택하세요.", "warn")
            return
        self.set_cue_field(self.selected_index, field, self.playhead)

    def set_cue_field(self, index: int, field: str, seconds: float) -> None:
        if not (0 <= index < len(self.doc.cues)):
            return
        if field == "start":
            func = lambda cues: set_cue_times(cues[index], start=seconds)  # noqa: E731
            label = "자막 시작 수정"
        else:
            func = lambda cues: set_cue_times(cues[index], end=seconds)  # noqa: E731
            label = "자막 종료 수정"
        ok, _ = self._commit_edit(label, func)
        if ok:
            name = "시작" if field == "start" else "종료"
            self.set_status(
                f"자막 #{index + 1} {name} 시간을 {format_clock(seconds)}로 수정했습니다.",
                "ok",
            )

    def nudge_selected(self, field: str, delta: float) -> None:
        if self.selected_index is None:
            self.set_status("먼저 자막을 선택하세요.", "warn")
            return
        if not self._require_doc():
            return
        index = self.selected_index
        cue = self.doc.cues[index]

        def apply(cues):
            target = cues[index]
            if field == "both":
                duration = target.duration
                start = max(0.0, target.start + delta)
                set_cue_times(target, start=start, end=start + duration)
            elif field == "start":
                set_cue_times(target, start=max(0.0, target.start + delta))
            else:
                set_cue_times(target, end=max(target.start + MIN_DURATION, target.end + delta))

        ok, _ = self._commit_edit("자막 미세 조정", apply)
        if ok:
            name = {"both": "전체", "start": "시작", "end": "끝"}[field]
            self.set_status(
                f"자막 #{index + 1} {name}을 {delta:+.2f}초 조정했습니다.", "ok"
            )

    def sort_cues(self) -> None:
        if not self._require_doc():
            return
        selected = self.selected_index
        cue = self.doc.cues[selected] if selected is not None and selected < len(self.doc.cues) else None
        ok, _ = self._commit_edit(
            "시간순 정렬", lambda cues: cues.sort(key=lambda c: (c.start, c.end))
        )
        if ok:
            if cue is not None:
                try:
                    self.selected_index = self.doc.cues.index(cue)
                except ValueError:
                    self.selected_index = None
            self.table.select(self.selected_index)
            self.timeline.set_selected(self.selected_index)
            self._fill_cue_editor()
            self.set_status("자막을 시작 시간순으로 정렬했습니다.", "ok")

    def delete_selected(self) -> None:
        if self.selected_index is None or not self._require_doc():
            self.set_status("삭제할 자막을 선택하세요.", "warn")
            return
        index = self.selected_index
        answer = messagebox.askyesno(
            "자막 삭제",
            f"자막 #{index + 1}을(를) 삭제할까요?\n{self.doc.cues[index].display_text()[:80]}",
            parent=self.root,
        )
        if not answer:
            return
        ok, _ = self._commit_edit(
            "자막 삭제", lambda cues: cues.pop(index) if 0 <= index < len(cues) else None
        )
        if ok:
            self.selected_index = min(index, len(self.doc.cues) - 1) if self.doc.cues else None
            self.table.select(self.selected_index)
            self.timeline.set_selected(self.selected_index)
            self._fill_cue_editor()
            self._sync_playhead()
            self.set_status(f"자막 #{index + 1}을(를) 삭제했습니다.", "ok")

    # ------------------------------------------------------------------
    # Undo / redo
    # ------------------------------------------------------------------
    def undo(self) -> None:
        if self._drag_before is not None:
            return
        result = self.history.undo(clone_cues(self.doc.cues))
        if not result:
            self.set_status("되돌릴 작업이 없습니다.", "info")
            return
        label, cues = result
        self.doc.cues = cues
        self.doc.dirty = True
        self._restore_cues_after_history()
        self.set_status(f"'{label}' 작업을 되돌렸습니다.", "info")

    def redo(self) -> None:
        if self._drag_before is not None:
            return
        result = self.history.redo(clone_cues(self.doc.cues))
        if not result:
            self.set_status("다시 실행할 작업이 없습니다.", "info")
            return
        label, cues = result
        self.doc.cues = cues
        self.doc.dirty = True
        self._restore_cues_after_history()
        self.set_status(f"'{label}' 작업을 다시 실행했습니다.", "info")

    def _restore_cues_after_history(self) -> None:
        if self.selected_index is not None and self.selected_index >= len(self.doc.cues):
            self.selected_index = None
        self.timeline.doc = self.doc
        self.timeline.redraw()
        self.table.set_document(self.doc, keep_view=True)
        self.table.select(self.selected_index, scroll=False)
        self.timeline.set_selected(self.selected_index)
        self._fill_cue_editor()
        self._sync_playhead()
        self.tools.refresh()
        self._update_history_buttons()
        self._update_document_meta()

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------
    def _guard_discard(self) -> bool:
        if not self.doc.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "저장 확인",
            f"'{self.doc.title}' 파일의 변경 사항을 저장할까요?",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save()
        return True

    def open_subtitle_dialog(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="자막 파일 열기",
            initialdir=self.config.get("last_dir") or None,
            filetypes=[
                ("자막 파일", "*.srt *.vtt *.webvtt *.ass *.ssa"),
                ("SubRip (*.srt)", "*.srt"),
                ("WebVTT (*.vtt)", "*.vtt"),
                ("ASS/SSA (*.ass *.ssa)", "*.ass *.ssa"),
                ("모든 파일", "*.*"),
            ],
        )
        if not path:
            return
        if not self._guard_discard():
            return
        self.open_subtitle(path)

    def open_subtitle(self, path: str | Path) -> None:
        try:
            doc = load_subtitle(path)
        except SubtitleError as exc:
            messagebox.showerror("자막 열기 실패", str(exc), parent=self.root)
            self.set_status(str(exc), "error")
            return
        message = (
            f"{len(doc.cues)}개 자막을 불러왔습니다 · {doc.encoding.upper()}"
        )
        if self.segments:
            message += " · 음성 자동 정렬 사용 가능"
        self._after_document_loaded(doc, message)
        self._maybe_auto_sync()

    def save(self) -> bool:
        if not self.doc.cues:
            self.set_status("저장할 자막이 없습니다.", "warn")
            return False
        if not self.doc.path:
            return self.save_as()
        try:
            encoding, warning = save_subtitle(
                self.doc, self.doc.path, kind=self.doc.kind
            )
        except OSError as exc:
            messagebox.showerror("저장 실패", f"파일을 저장할 수 없습니다.\n{exc}", parent=self.root)
            self.set_status(f"저장 실패: {exc}", "error")
            return False
        self._after_save(encoding, warning)
        return True

    def save_as(self) -> bool:
        if not self.doc.cues:
            self.set_status("저장할 자막이 없습니다.", "warn")
            return False
        extension = {"srt": ".srt", "vtt": ".vtt", "ass": ".ass"}.get(self.doc.kind, ".srt")
        initial = self.doc.path.name if self.doc.path else f"subtitles{extension}"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="다른 이름으로 저장",
            initialdir=self.config.get("last_dir") or None,
            initialfile=initial,
            defaultextension=extension,
            filetypes=[
                ("SubRip 자막 (*.srt)", "*.srt"),
                ("WebVTT (*.vtt)", "*.vtt"),
                ("ASS 자막 (*.ass)", "*.ass"),
                ("모든 파일", "*.*"),
            ],
        )
        if not path:
            return False
        kind = kind_from_suffix(path) or self.doc.kind
        try:
            encoding, warning = save_subtitle(self.doc, path, kind=kind)
        except OSError as exc:
            messagebox.showerror("저장 실패", f"파일을 저장할 수 없습니다.\n{exc}", parent=self.root)
            self.set_status(f"저장 실패: {exc}", "error")
            return False
        self.config["last_dir"] = str(Path(path).parent)
        self._after_save(encoding, warning)
        return True

    def _after_save(self, encoding: str, warning: str | None) -> None:
        self.doc.dirty = False
        self._update_document_meta()
        self._update_history_buttons()
        self.set_status(
            f"저장했습니다 · {encoding.upper()}" + (f" · {warning}" if warning else ""),
            "warn" if warning else "ok",
        )
        if warning:
            messagebox.showwarning("인코딩 변경", warning, parent=self.root)

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------
    def open_media_dialog(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="영상 또는 오디오 열기",
            initialdir=self.config.get("last_dir") or None,
            filetypes=[
                (
                    "미디어",
                    "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.mp3 *.wav *.m4a *.aac *.flac *.wma *.ogg *.opus",
                ),
                ("모든 파일", "*.*"),
            ],
        )
        if path:
            self.load_media(path)

    def load_media(self, path: str | Path) -> None:
        if self._media_loading:
            self.set_status("이미 미디어를 분석하고 있습니다.", "warn")
            return
        self._media_token += 1
        token = self._media_token
        self._media_loading = True
        self.media_btn.configure(state="disabled", text="분석 중…")
        self.progress_label.configure(text="오디오 분석 준비 중…")
        self.set_status(f"미디어를 분석하고 있습니다: {Path(path).name}", "info")
        self.config["last_dir"] = str(Path(path).parent)
        save_config(self.config)

        def worker() -> None:
            def progress(value: float) -> None:
                self._media_queue.put(("progress", token, value))

            try:
                info = prepare_media(
                    path,
                    self.work_dir,
                    ffmpeg_path=self.ffmpeg_path,
                    progress=progress,
                )
            except Exception as exc:  # noqa: BLE001
                self._media_queue.put(("error", token, str(exc)))
            else:
                self._media_queue.put(("ready", token, info))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_media_queue(self) -> None:
        while True:
            try:
                kind, token, payload = self._media_queue.get_nowait()
            except queue.Empty:
                break
            if token != self._media_token:
                continue
            if kind == "progress":
                self.progress_label.configure(text=f"오디오 분석 {float(payload) * 100:.0f}%")
            elif kind == "error":
                self._media_failed(str(payload))
            elif kind == "ready":
                self._media_ready(payload)

    def _media_ready(self, info: MediaInfo) -> None:
        self._media_loading = False
        self.media_btn.configure(state="normal", text="미디어 열기")
        self.progress_label.configure(text="")
        self.media = info
        self.analysis = info.analysis
        if info.analysis:
            sensitivity = float(self.tools.sensitivity_var.get()) / 100.0
            self.segments = detect_speech(
                info.analysis.envelope, info.analysis.frame_hz, sensitivity
            )
            info.analysis.speech = self.segments
        else:
            self.segments = []
        self.playhead = 0.0
        self.player.close()
        mci_ok = self.player.load(info.playback_path, info.duration)
        self.timeline.set_analysis(info.analysis)
        self.timeline.set_segments(self.segments)
        self.timeline.set_media_duration(info.duration)
        self.timeline.set_playhead(0.0)
        if not self.doc.cues:
            self.timeline.fit()
        self._update_document_meta()
        self._update_transport()
        self.tools.update_audio_state()
        self.tools.update_suggestion()
        self._update_preview()

        parts = []
        if info.analysis:
            parts.append(
                f"오디오 분석 완료 · 음성 구간 {len(self.segments)}개 · "
                f"{format_duration(info.duration)}"
            )
        if info.note:
            parts.append(info.note)
        if not mci_ok:
            parts.append("재생 엔진이 파일을 열지 못해 타임라인 탐색 모드로 전환했습니다.")
        self.set_status("  ·  ".join(parts) or "미디어를 열었습니다.", "ok" if mci_ok else "warn")
        if info.analysis:
            self.config["last_dir"] = str(info.source.parent)
            save_config(self.config)
        if not self.doc.cues and self.config.get("auto_caption_on_media", True):
            if available_backend():
                self.root.after(250, self.generate_korean_subtitles)
            else:
                self.set_status(
                    "미디어가 준비되었습니다 · ‘한글 자막 생성’을 누르거나 faster-whisper를 설치하세요.",
                    "warn",
                )
        self._maybe_auto_sync()

    def _maybe_auto_sync(self) -> None:
        """Run once when a subtitle and analyzed media are both available."""

        if not self.doc.cues or not self.segments or self._media_loading:
            return
        key = (
            str(self.doc.path) if self.doc.path else id(self.doc),
            str(self.media.source) if self.media else None,
        )
        if key == self._auto_sync_key:
            return
        self._auto_sync_key = key
        self.root.after(120, self.run_auto_sync)

    def run_auto_sync(self) -> None:
        """Build and apply the automatic plan, preserving one-step undo."""

        if not self._require_doc():
            return
        if not self.segments:
            self.set_status("자동 싱크에는 먼저 영상/오디오 분석이 필요합니다.", "warn")
            return
        self.auto_btn.configure(state="disabled", text="싱크 계산 중…")
        self.progress_label.configure(text="자동 싱크 계산 중…")
        self.set_status("음성 구간과 자막을 자동으로 매칭하고 있습니다…", "info")

        def worker() -> None:
            try:
                plan = build_auto_plan(self.doc.cues, self.segments)
            except Exception as exc:  # noqa: BLE001
                self.root.after(0, lambda: self._auto_sync_finished(None, str(exc)))
                return
            self.root.after(0, lambda: self._auto_sync_finished(plan, None))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_sync_finished(self, plan, error: str | None) -> None:
        self.auto_btn.configure(state="normal", text="자동 싱크")
        self.progress_label.configure(text="")
        if error:
            self.set_status(f"자동 싱크 실패: {error}", "error")
            return
        if plan is None or plan.matched < 3:
            message = "\n".join(plan.warnings if plan else ["계산 결과가 없습니다."])
            messagebox.showwarning("자동 싱크", message, parent=self.root)
            self.set_status("자동 싱크를 적용하지 못했습니다.", "warn")
            return

        report = "\n".join(plan.describe())
        if plan.confidence < 0.85:
            answer = messagebox.askyesno(
                "자동 싱크 결과 확인",
                report + "\n\n신뢰도가 낮습니다. 그래도 적용할까요?",
                parent=self.root,
            )
            if not answer:
                self.set_status("자동 싱크 적용을 취소했습니다.", "warn")
                return
        ok, _ = self._commit_edit("자동 싱크", lambda cues: apply_auto_plan(cues, plan))
        if ok:
            self.set_status(
                f"자동 싱크 적용 완료 · {plan.changed}개 수정 · Ctrl+Z로 되돌릴 수 있습니다.",
                "ok",
            )

    def _media_failed(self, message: str) -> None:
        self._media_loading = False
        self.media_btn.configure(state="normal", text="미디어 열기")
        self.progress_label.configure(text="")
        self.set_status(message, "error")
        answer = messagebox.askyesno(
            "미디어 열기 실패",
            f"{message}\n\nFFmpeg 경로를 지금 지정할까요?",
            parent=self.root,
        )
        if answer:
            self.choose_ffmpeg()

    def generate_korean_subtitles(self) -> None:
        """Create a Korean subtitle document from the loaded media."""

        if not self.media or not self.media.analysis:
            self.set_status("먼저 영상 또는 오디오를 열어 주세요.", "warn")
            return
        if self.doc.cues and not self._guard_discard():
            return
        self.caption_btn.configure(state="disabled", text="자막 생성 중…")
        self.progress_label.configure(text="한국어 음성인식 준비 중…")
        self.set_status("영상 음성을 분석해 한국어 자막을 만들고 있습니다…", "info")
        audio_path = self.media.playback_path

        def worker() -> None:
            try:
                cues = transcribe_korean(
                    audio_path,
                    progress=lambda value: self.root.after(
                        0,
                        lambda: self.progress_label.configure(
                            text=f"한국어 음성인식 {float(value) * 100:.0f}%"
                        ),
                    ),
                )
            except TranscriptionError as exc:
                self.root.after(0, lambda: self._caption_generation_finished(None, str(exc)))
                return
            self.root.after(0, lambda: self._caption_generation_finished(cues, None))

        threading.Thread(target=worker, daemon=True).start()

    def _caption_generation_finished(self, cues, error: str | None) -> None:
        self.caption_btn.configure(state="normal", text="한글 자막 생성")
        self.progress_label.configure(text="")
        if error:
            messagebox.showerror("한글 자막 생성", error, parent=self.root)
            self.set_status(error.splitlines()[0], "error")
            return
        doc = Document(
            kind="srt",
            cues=cues or [],
            encoding="utf-8",
            newline="\r\n",
            dirty=True,
        )
        self._after_document_loaded(doc, f"한국어 자막 {len(doc.cues)}개를 생성했습니다.")
        self.set_status("한국어 자막 생성 완료 · 다른 이름으로 저장해 파일을 보관하세요.", "ok")

    def choose_ffmpeg(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="ffmpeg.exe 선택",
            filetypes=[("FFmpeg 실행 파일", "ffmpeg.exe"), ("실행 파일", "*.exe"), ("모든 파일", "*.*")],
        )
        if not path:
            return
        self.ffmpeg_path = path
        self.config["ffmpeg_path"] = path
        save_config(self.config)
        self.set_status(f"FFmpeg 경로를 저장했습니다: {path}", "ok")

    def show_ffmpeg_status(self) -> None:
        found = find_ffmpeg(self.ffmpeg_path)
        if found:
            messagebox.showinfo("FFmpeg", f"사용 가능한 FFmpeg:\n{found}", parent=self.root)
        else:
            messagebox.showwarning(
                "FFmpeg",
                "FFmpeg를 찾지 못했습니다.\n\n"
                "MP4/MKV 같은 영상의 파형과 음성 자동 정렬에는 FFmpeg가 필요합니다.\n"
                "ffmpeg.exe를 PATH 또는 앱 폴더에 두거나, "
                "도구 메뉴에서 경로를 지정하세요.",
                parent=self.root,
            )

    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def _play_end(self) -> float:
        if self.media and self.media.duration > 0:
            return self.media.duration
        if self.doc.cues:
            return max(self.doc.last_end + 1.0, 10.0)
        return 30.0

    def toggle_play(self) -> None:
        if not self.doc.cues and not self.media:
            self.set_status("재생할 자막이나 미디어가 없습니다.", "warn")
            return
        if self.player.playing:
            self._pause()
            return
        end = self._play_end()
        if self.playhead >= end - 0.05:
            self.seek(0.0)
        if not self.player.using_mci:
            self.player.duration = max(self.player.duration, end)
        self.player.play(self.playhead)
        self._was_playing = True
        self._update_transport()
        if self.player.has_media and not self.player.playing and not self.player.using_mci:
            self.set_status(
                "미디어를 재생할 수 없어 타임라인 탐색 모드로 재생합니다.", "warn"
            )

    def _pause(self) -> None:
        if self.player.playing:
            self.player.pause()
        self._was_playing = False
        self._update_transport()

    def seek(self, seconds: float) -> None:
        end = max(1.0, self._play_end())
        seconds = max(0.0, min(seconds, end))
        self.playhead = seconds
        self.player.seek(seconds)
        self.timeline.set_playhead(seconds)
        self._sync_playhead()

    def _on_timeline_seek(self, seconds: float) -> None:
        self.seek(seconds)

    def _on_timeline_select(self, index: int) -> None:
        self.select_cue(index)

    def _on_timeline_view_change(self, _span: float) -> None:
        self._update_info()

    _on_view_change = _on_timeline_view_change

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            self._handle_media_queue()
            playing = self.player.playing
            if playing:
                position = self.player.position()
                end = self._play_end()
                if position >= end - 0.02:
                    self.player.pause()
                    self.playhead = end
                    self._was_playing = False
                    self.timeline.set_playhead(end)
                    self.table.set_active(())
                    self._update_preview()
                    self._update_transport()
                elif abs(position - self.playhead) > 0.003:
                    self.playhead = position
                    self.timeline.set_playhead(position, follow=True)
                    self.table.set_active(tuple(self.doc.cue_at(position)))
                    self._update_preview()
                    self._update_transport()
            elif self._was_playing:
                self._was_playing = False
                self.playhead = self.player.position()
                self._sync_playhead()
            if playing != self._was_playing:
                self._update_transport()
        except Exception:
            pass
        if not self._closing:
            self.root.after(70, self._poll)

    # ------------------------------------------------------------------
    # Selection / editing
    # ------------------------------------------------------------------
    def _on_table_select(self, index: int) -> None:
        self.select_cue(index)

    def _on_table_activate(self, index: int) -> None:
        self.select_cue(index, seek=True)

    def select_cue(self, index: int | None, seek: bool = False) -> None:
        if index is None or not (0 <= index < len(self.doc.cues)):
            self.selected_index = None
            self.timeline.set_selected(None)
            self.table.select(None)
            self._fill_cue_editor()
            return
        self.selected_index = index
        self.timeline.set_selected(index)
        if self.table.selected != index:
            self.table.select(index)
        self._fill_cue_editor()
        if seek:
            self.seek(self.doc.cues[index].start)

    def _on_cue_drag(self, index: int, start: float, end: float, final: bool) -> None:
        if not (0 <= index < len(self.doc.cues)):
            return
        if not final and self._drag_before is None:
            self._drag_before = clone_cues(self.doc.cues)
            self._drag_label = "타임라인에서 자막 조정"
        cue = self.doc.cues[index]
        set_cue_times(cue, start=start, end=end)
        self.table.update_row(index)
        self._fill_cue_editor()
        self._update_preview()
        if final:
            if self._drag_before is not None:
                self.history.push_snapshot(self._drag_label, self._drag_before)
                self._drag_before = None
                self.doc.dirty = True
                self.table.refresh(self.doc)
                self.tools.refresh()
                self._update_history_buttons()
                self._update_document_meta()
            self.set_status(
                f"자막 #{index + 1}: {format_clock(cue.start)} ~ {format_clock(cue.end)}",
                "ok",
            )

    # ------------------------------------------------------------------
    # Correction actions
    # ------------------------------------------------------------------
    def apply_offset(self, delta: float) -> None:
        if not self._require_doc():
            return
        ok, _ = self._commit_edit("전체 타임코드 이동", lambda cues: shift_all(cues, delta))
        if ok:
            self.set_status(
                f"모든 자막을 {delta:+.3f}초 이동했습니다.", "ok"
            )
            self.tools.update_suggestion()

    def align_first_to_playhead(self) -> None:
        if not self._require_doc():
            return
        first_index = min(
            range(len(self.doc.cues)), key=lambda i: self.doc.cues[i].start
        )
        delta = self.playhead - self.doc.cues[first_index].start
        if abs(delta) < 1e-6:
            self.set_status("첫 자막이 이미 현재 재생 위치에 있습니다.", "info")
            return
        ok, _ = self._commit_edit("첫 자막 맞춤", lambda cues: shift_all(cues, delta))
        if ok:
            self.set_status(
                f"첫 자막을 재생 위치에 맞추기 위해 전체를 {delta:+.3f}초 이동했습니다.",
                "ok",
            )

    def apply_suggested_offset(self) -> None:
        if not self._require_doc():
            return
        if not self.segments:
            self.set_status(
                "음성 구간이 없습니다. 미디어를 열고 분석이 끝난 뒤 다시 시도하세요.",
                "warn",
            )
            return
        value, matches = suggest_offset(self.doc.cues, self.segments, window=5.0)
        if value is None:
            self.set_status(
                f"오프셋을 추천하기에 일치하는 자막이 부족합니다. (일치 {matches}개)",
                "warn",
            )
            return
        ok, _ = self._commit_edit(
            "음성 기준 전체 이동", lambda cues: shift_all(cues, value)
        )
        if ok:
            self.set_status(
                f"음성 기준 추천 오프셋 {value:+.3f}초를 적용했습니다. (일치 {matches}개)",
                "ok",
            )
            self.tools.show_align_result(
                f"전체 오프셋 {value:+.3f}초 적용 · 일치 {matches}개", True
            )

    def apply_two_point(
        self,
        src_a: float,
        dst_a: float,
        src_b: float,
        dst_b: float,
    ) -> None:
        if not self._require_doc():
            return
        ok, factor = self._commit_edit(
            "두 지점 비율 보정",
            lambda cues: map_two_points(cues, src_a, dst_a, src_b, dst_b),
        )
        if ok and factor:
            self.set_status(
                f"두 지점 기준으로 보정했습니다. (배율 {factor * 100:.3f}%)", "ok"
            )

    def apply_fps(self, subtitle_fps: float, video_fps: float, anchor: float | None) -> None:
        if not self._require_doc():
            return
        ok, factor = self._commit_edit(
            "FPS 보정",
            lambda cues: scale_fps(cues, subtitle_fps, video_fps, anchor),
        )
        if ok and factor:
            self.set_status(
                f"FPS 차이를 보정했습니다. (배율 {factor * 100:.3f}%)", "ok"
            )

    def shift_from_index(self, index: int, delta: float) -> None:
        if not self._require_doc():
            return
        ok, count = self._commit_edit(
            "구간부터 이동",
            lambda cues: shift_from_index(cues, index, delta),
        )
        if ok:
            self.set_status(
                f"자막 #{index + 1}부터 {count}개를 {delta:+.3f}초 이동했습니다.", "ok"
            )

    def shift_range(self, first: int, last: int, delta: float) -> None:
        if not self._require_doc():
            return
        ok, count = self._commit_edit(
            "선택 구간 이동",
            lambda cues: shift_index_range(cues, first, last, delta),
        )
        if ok:
            self.set_status(
                f"자막 #{first + 1} ~ #{last + 1} ({count}개)를 {delta:+.3f}초 이동했습니다.",
                "ok",
            )

    def shift_from_time(self, threshold: float, delta: float) -> None:
        if not self._require_doc():
            return
        ok, count = self._commit_edit(
            "시각 이후 이동",
            lambda cues: shift_from_time(cues, threshold, delta),
        )
        if ok:
            self.set_status(
                f"{format_clock(threshold)} 이후 {count}개를 {delta:+.3f}초 이동했습니다.",
                "ok",
            )

    def auto_align(
        self, target: str, window: float, snap_end: bool, fallback: bool
    ) -> None:
        if not self._require_doc():
            return
        if not self.segments:
            self.set_status(
                "음성 구간이 없습니다. 미디어를 열고 분석이 끝난 뒤 다시 시도하세요.",
                "warn",
            )
            return
        if target == "selected":
            if self.selected_index is None:
                self.set_status("선택 자막 모드에서는 자막을 먼저 선택하세요.", "warn")
                return
            indices = [self.selected_index]
        else:
            indices = None

        before = clone_cues(self.doc.cues)
        try:
            stats = auto_align(
                self.doc.cues,
                self.segments,
                indices=indices,
                window=window,
                snap_end=snap_end,
                reuse_neighbour_offset=fallback,
            )
        except Exception as exc:  # noqa: BLE001
            self.doc.cues = before
            messagebox.showerror("자동 정렬 실패", str(exc), parent=self.root)
            return
        if not stats["aligned"]:
            self.doc.cues = before
            self.set_status(
                f"탐색 범위 안에서 맞는 음성 구간을 찾지 못했습니다. (범위 {window:g}초를 늘려 보세요)",
                "warn",
            )
            return
        self.history.push_snapshot("음성 기준 자동 정렬", before)
        self.doc.dirty = True
        self._refresh_after_edit()
        text = (
            f"{stats['aligned']}개 자막 정렬 · {stats['skipped']}개 유지 · "
            f"중앙 이동 {stats['median_shift']:+.3f}초"
        )
        self.set_status(text, "ok")
        self.tools.show_align_result(text, True)

    def snap_selected(self, edge: str) -> None:
        if self.selected_index is None or not self._require_doc():
            self.set_status("먼저 자막을 선택하세요.", "warn")
            return
        if not self.segments:
            self.set_status("음성 구간이 없습니다. 미디어를 먼저 여세요.", "warn")
            return
        cue = self.doc.cues[self.selected_index]
        target = (
            nearest_start(self.segments, cue.start)
            if edge == "start"
            else nearest_end(self.segments, cue.end)
        )
        if target is None:
            self.set_status("가까운 음성 경계를 찾지 못했습니다.", "warn")
            return
        distance = abs(target - (cue.start if edge == "start" else cue.end))
        if distance > 3.0:
            self.set_status(
                f"가장 가까운 음성 경계가 {distance:.2f}초 떨어져 있어 맞추지 않았습니다.",
                "warn",
            )
            return
        index = self.selected_index
        if edge == "start":
            func = lambda cues: set_cue_times(cues[index], start=target)  # noqa: E731
            label = "시작을 음성에 맞춤"
        else:
            func = lambda cues: set_cue_times(cues[index], end=target)  # noqa: E731
            label = "끝을 음성에 맞춤"
        ok, _ = self._commit_edit(label, func)
        if ok:
            name = "시작" if edge == "start" else "끝"
            self.set_status(
                f"자막 #{index + 1} {name}을 음성 경계 {format_clock(target)}에 맞췄습니다.",
                "ok",
            )

    def set_sensitivity(self, value: float) -> None:
        if not self.analysis:
            return
        self.sensitivity = max(0.0, min(100.0, value))
        self.segments = detect_speech(
            self.analysis.envelope, self.analysis.frame_hz, self.sensitivity / 100.0
        )
        self.analysis.speech = self.segments
        self.timeline.set_segments(self.segments)
        self.tools.update_audio_state()
        self.tools.update_suggestion()
        self.set_status(
            f"감지 민감도 {self.sensitivity:.0f} · 음성 구간 {len(self.segments)}개",
            "info",
        )

    # ------------------------------------------------------------------
    # View helpers
    # ------------------------------------------------------------------
    def timeline_fit(self) -> None:
        self.timeline.fit()

    def timeline_zoom(self, factor: float) -> None:
        self.timeline.zoom(factor, anchor=self.playhead)

    def _toggle_snap(self) -> None:
        self.timeline.set_snap_enabled(self.snap_var.get())

    def _focus_tool(self, index: int) -> None:
        try:
            self.tools.notebook.select(index)
        except tk.TclError:
            pass

    # ------------------------------------------------------------------
    # Status, help
    # ------------------------------------------------------------------
    def set_status(self, message: str, kind: str = "info") -> None:
        colors = {
            "info": theme.MUTED,
            "ok": theme.OK,
            "warn": theme.WARN,
            "error": theme.DANGER,
        }
        self.status_label.configure(text=f"●  {message}", fg=colors.get(kind, theme.MUTED))

    def show_help(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("CaptionSync 사용법")
        window.configure(bg=theme.BG)
        window.geometry("840x740")
        window.minsize(680, 520)
        window.transient(self.root)
        window.iconphoto(False, self._icon)

        header = tk.Frame(window, bg=theme.BG)
        header.pack(fill="x", padx=22, pady=(18, 10))
        tk.Label(
            header,
            text="CaptionSync 사용법",
            bg=theme.BG,
            fg=theme.TEXT,
            font=(theme.FONT[0], 18, "bold"),
        ).pack(side="left")
        button(header, "닫기", window.destroy, kind="ghost", padx=16).pack(side="right")

        body = tk.Frame(window, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        text = tk.Text(
            body,
            bg=theme.PANEL,
            fg=theme.TEXT,
            font=theme.FONT,
            relief="flat",
            bd=0,
            padx=22,
            pady=18,
            wrap="word",
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            cursor="arrow",
        )
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        text.tag_configure(
            "h1",
            font=(theme.FONT[0], 16, "bold"),
            foreground=theme.ACCENT,
            spacing1=14,
            spacing3=6,
        )
        text.tag_configure(
            "h2",
            font=(theme.FONT[0], 12, "bold"),
            foreground=theme.TEXT,
            spacing1=12,
            spacing3=4,
        )
        text.tag_configure("body", font=theme.FONT, spacing1=2, spacing3=2)
        text.tag_configure("muted", font=theme.FONT_SMALL, foreground=theme.MUTED)
        text.tag_configure("code", font=theme.MONO, foreground=theme.OK)

        def h1(value: str) -> None:
            text.insert("end", value + "\n", "h1")

        def h2(value: str) -> None:
            text.insert("end", value + "\n", "h2")

        def body_text(value: str, tag: str = "body") -> None:
            text.insert("end", value + "\n", tag)

        h1("문제 유형별 보정 방법")
        table = [
            (
                "전체 자막이 3초 빠르거나 느림",
                "전체 타임코드 일괄 이동",
                "전체 이동 탭",
            ),
            (
                "초반은 맞지만 후반으로 갈수록 어긋남",
                "시간 비율 및 구간별 보정",
                "비율 보정 탭",
            ),
            (
                "특정 장면부터 싱크가 틀어짐",
                "해당 구간부터 개별 보정",
                "구간 보정 탭",
            ),
            (
                "자막마다 표시 시간이 부정확함",
                "음성 구간 기준 시작·종료 조정",
                "음성 정렬 탭",
            ),
            (
                "영상과 자막의 프레임레이트 차이",
                "FPS 차이에 따른 타임코드 보정",
                "FPS 탭",
            ),
        ]
        for problem, method, tab in table:
            text.insert("end", f"• {problem}\n", "body")
            text.insert("end", f"   → {method}  ", "muted")
            text.insert("end", f"[{tab}]\n", "code")
        body_text("")

        h1("빠른 시작")
        steps = [
            "1. [자막 열기]로 SRT / VTT / ASS 파일을 엽니다.",
            "2. [미디어 열기]로 영상 또는 오디오를 엽니다. MP4/MKV는 FFmpeg가 필요합니다.",
            "3. 타임라인에서 클릭해 재생 위치를 옮기고 Space로 재생/일시정지합니다.",
            "4. 전체가 밀려 있으면 [전체 이동] 탭에서 값을 입력하거나 음성 기준 오프셋 추천을 사용합니다.",
            "5. 자막마다 시간이 어긋나면 [음성 정렬] 탭에서 자동 정렬합니다.",
            "6. 자막 막대의 양 끝을 드래그해 미세 조정하고, Ctrl+Z로 언제든 되돌립니다.",
            "7. [저장]으로 원본 형식을 유지한 채 저장합니다.",
        ]
        for step in steps:
            body_text(step)
        body_text("")

        h1("타임라인 조작")
        for line in [
            "• 자막 막대 몸통 드래그: 자막 전체 이동",
            "• 자막 막대 양 끝 드래그: 시작 또는 종료 시간 조정",
            "• 빈 곳 클릭: 재생 위치 이동, 더블클릭한 자막으로 이동",
            "• 마우스 휠: 확대/축소, Shift+휠: 좌우 이동",
            "• 음성 스냅이 켜져 있으면 드래그 시 감지된 음성 경계에 붙습니다.",
        ]:
            body_text(line)
        body_text("")

        h1("단축키")
        for key, action in SHORTCUTS:
            text.insert("end", f"{key:<24}", "code")
            text.insert("end", action + "\n", "body")
        body_text("")

        h1("파일과 인코딩")
        for line in [
            "• SRT, WebVTT, ASS/SSA를 읽고 씁니다. ASS의 스타일/필드는 그대로 보존됩니다.",
            "• 저장 시 원본 인코딩(UTF-8, CP949 등)을 유지합니다. 표현할 수 없는 문자는 UTF-8로 자동 전환됩니다.",
            "• 다른 이름으로 저장할 때 확장자를 바꾸면 형식이 변환됩니다. (ASS 태그는 일반 텍스트로 정리)",
        ]:
            body_text(line)
        body_text("")

        h1("FFmpeg (선택)")
        for line in [
            "• WAV는 FFmpeg 없이 파형과 음성 구간을 바로 분석합니다.",
            "• MP3/WMA는 FFmpeg 없이도 재생할 수 있지만 파형 분석은 되지 않습니다.",
            "• MP4/MKV/MOV 등 영상은 FFmpeg로 오디오를 추출해 분석합니다.",
            "• ffmpeg.exe를 PATH, 앱 폴더, 또는 [도구 → FFmpeg 경로 지정]으로 연결하세요.",
        ]:
            body_text(line)
        body_text("")

        h1("문제 해결")
        for line in [
            "• 자동 정렬 결과가 이상하면 감지 민감도를 조정하고 다시 실행하세요.",
            "• 음성 구간이 너무 많으면 민감도를 낮추고, 너무 적으면 높이세요.",
            "• 모든 작업은 되돌리기 기록에 남습니다. 저장 전에 Ctrl+Z로 비교해 보세요.",
            "• 재생 엔진이 파일을 열지 못해도 타임라인 탐색과 편집은 계속 사용할 수 있습니다.",
        ]:
            body_text(line)

        text.configure(state="disabled")
        window.bind("<Escape>", lambda _event: window.destroy())
        window.after(50, lambda: (window.grab_set(), window.focus_set()))

    def show_about(self) -> None:
        messagebox.showinfo(
            "CaptionSync 정보",
            (
                f"CaptionSync {__version__}\n"
                "자막 싱크 보정 데스크톱 스튜디오\n\n"
                "지원: 전체 이동 · 비율/드리프트 · 구간 보정 · "
                "음성 구간 자동 정렬 · FPS 변환\n"
                "형식: SRT / WebVTT / ASS(SSA)\n"
                "엔진: Python + Tkinter (표준 라이브러리)\n\n"
                "MIT License"
            ),
            parent=self.root,
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def on_close(self) -> None:
        if self._closing:
            return
        if not self._guard_discard():
            return
        self._closing = True
        self.config["geometry"] = self.root.geometry()
        self.config["ffmpeg_path"] = self.ffmpeg_path
        self.config["snap"] = bool(self.snap_var.get())
        self.config["sensitivity"] = float(self.tools.sensitivity_var.get())
        try:
            self.config["sash"] = [
                self.paned.sash_coord(0)[0],
                self.paned.sash_coord(1)[0],
            ]
            self.config["sash_total"] = self.paned.winfo_width()
        except tk.TclError:
            pass
        save_config(self.config)
        self.player.close()
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.root.destroy()
