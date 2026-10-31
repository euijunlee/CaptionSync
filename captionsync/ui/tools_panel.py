"""The five synchronization tool tabs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..timecode import format_clock, parse_offset, parse_timecode
from ..transforms import linear_parameters, speech_coverage, suggest_offset
from . import theme
from .widgets import (
    ScrollableFrame,
    button,
    check,
    combo,
    divider,
    entry,
    hint,
    pill,
    radio,
)

FPS_VALUES = ["23.976", "24", "25", "29.97", "30", "48", "50", "59.94", "60"]


class ToolsPanel(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=theme.PANEL)
        self.app = app
        self._scrolls: list[ScrollableFrame] = []

        header = tk.Frame(self, bg=theme.PANEL)
        header.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(
            header,
            text="싱크 보정 도구",
            bg=theme.PANEL,
            fg=theme.TEXT,
            font=theme.FONT_BIG,
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            header,
            text="문제 유형에 맞는 탭을 선택하세요.",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
        ).pack(anchor="w", pady=(2, 0))

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._build_global_tab()
        self._build_rate_tab()
        self._build_fps_tab()
        self._build_section_tab()
        self._build_audio_tab()

        for scroll in self._scrolls:
            scroll.bind_wheel()

        self.on_document_changed()

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------
    def _tab(self, title: str) -> tk.Frame:
        scroll = ScrollableFrame(self.notebook)
        self.notebook.add(scroll, text=title)
        self._scrolls.append(scroll)
        content = tk.Frame(scroll.body, bg=theme.PANEL)
        content.pack(fill="both", expand=True, padx=14, pady=13)
        return content

    def _heading(self, parent, title: str, description: str) -> None:
        tk.Label(
            parent,
            text=title,
            bg=theme.PANEL,
            fg=theme.TEXT,
            font=theme.FONT_BIG,
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            parent,
            text=description,
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=320,
        ).pack(anchor="w", pady=(3, 12))

    def _card(self, parent, bg: str = theme.PANEL_ALT):
        outer = tk.Frame(
            parent,
            bg=bg,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
        )
        inner = tk.Frame(outer, bg=bg)
        inner.pack(fill="both", expand=True, padx=10, pady=10)
        return outer, inner

    def _field(self, parent, label: str, variable, width: int = 10, mono: bool = False):
        row = tk.Frame(parent, bg=theme.PANEL)
        tk.Label(
            row,
            text=label,
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
        ).pack(side="left")
        widget = entry(
            row,
            textvariable=variable,
            width=width,
            font=theme.MONO if mono else theme.FONT_SMALL,
            justify="center",
        )
        widget.pack(side="right")
        return row, widget

    def _separator(self, parent, before: int = 12, after: int = 12) -> None:
        spacer = tk.Frame(parent, bg=theme.PANEL, height=1)
        spacer.pack(fill="x", pady=(before, 0))
        tk.Frame(spacer, bg=theme.BORDER, height=1).pack(fill="x")
        tk.Frame(parent, bg=theme.PANEL, height=after).pack(fill="x")

    # ------------------------------------------------------------------
    # 1. Global offset
    # ------------------------------------------------------------------
    def _build_global_tab(self) -> None:
        tab = self._tab("전체 이동")
        self._heading(
            tab,
            "전체 타임코드 이동",
            "자막이 영상보다 전체적으로 빠르거나 느릴 때 모든 타임코드를 같은 값만큼 이동합니다.",
        )

        self.offset_var = tk.StringVar(value="0.000")
        entry_box = tk.Frame(tab, bg=theme.PANEL)
        entry_box.pack(fill="x")
        tk.Label(
            entry_box,
            text="이동 시간 (초)",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
        ).pack(anchor="w")
        offset_entry = tk.Entry(
            entry_box,
            textvariable=self.offset_var,
            justify="center",
            font=(theme.MONO[0], 18, "bold"),
            bg=theme.PANEL_ALT,
            fg=theme.ACCENT,
            insertbackground=theme.ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=1,
            highlightbackground=theme.BORDER,
            highlightcolor=theme.ACCENT,
        )
        offset_entry.pack(fill="x", pady=(4, 8), ipady=7)
        offset_entry.bind("<Return>", lambda _event: self._apply_offset())
        offset_entry.bind("<KP_Enter>", lambda _event: self._apply_offset())

        nudge = tk.Frame(tab, bg=theme.PANEL)
        nudge.pack(fill="x", pady=(0, 8))
        for i, (label, value) in enumerate(
            [("-1초", -1.0), ("-0.1초", -0.1), ("+0.1초", 0.1), ("+1초", 1.0)]
        ):
            nudge.grid_columnconfigure(i, weight=1)
            btn = button(
                nudge,
                label,
                command=lambda v=value: self._nudge_offset(v),
                kind="ghost",
                padx=4,
                pady=5,
            )
            btn.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))

        button(tab, "모든 자막 이동", self._apply_offset, kind="accent", pady=8).pack(
            fill="x", pady=(0, 4)
        )

        self._separator(tab)
        tk.Label(
            tab,
            text="빠른 맞춤",
            bg=theme.PANEL,
            fg=theme.TEXT,
            font=theme.FONT_SMALL_BOLD,
            anchor="w",
        ).pack(anchor="w", pady=(0, 6))
        button(
            tab,
            "첫 자막을 현재 재생 위치에 맞춤",
            self.app.align_first_to_playhead,
            kind="soft",
            pady=7,
        ).pack(fill="x", pady=(0, 5))
        button(
            tab,
            "음성 기준 오프셋 추천 · 적용",
            self.app.apply_suggested_offset,
            kind="soft",
            pady=7,
        ).pack(fill="x")
        self.suggest_label = tk.Label(
            tab,
            text="음성 구간을 분석하면 전체 이동값을 자동으로 추천합니다.",
            bg=theme.PANEL,
            fg=theme.FAINT,
            font=theme.FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=320,
        )
        self.suggest_label.pack(anchor="w", pady=(6, 0))

        self._separator(tab)
        hint(
            tab,
            "예: 전체가 3초 빠르면 +3.000을 입력하고 이동하세요. "
            "이동 후 음성 자동 정렬 탭에서 시작점을 다듬으면 좋습니다.",
        ).pack(anchor="w")

    def _nudge_offset(self, delta: float) -> None:
        try:
            current = parse_offset(self.offset_var.get())
        except ValueError:
            current = 0.0
        self.offset_var.set(f"{current + delta:+.3f}")

    def _apply_offset(self) -> None:
        try:
            delta = parse_offset(self.offset_var.get())
        except ValueError as exc:
            self.app.set_status(str(exc), "error")
            return
        self.app.apply_offset(delta)

    # ------------------------------------------------------------------
    # 2. Linear / drift
    # ------------------------------------------------------------------
    def _build_rate_tab(self) -> None:
        tab = self._tab("비율 보정")
        self._heading(
            tab,
            "시간 비율 · 드리프트",
            "앞부분은 맞는데 뒤로 갈수록 어긋날 때, 두 지점을 기준으로 시간 간격을 비율에 맞춰 늘리거나 줄입니다.",
        )

        self.a_src = tk.StringVar(value="00:00:00.000")
        self.a_dst = tk.StringVar(value="00:00:00.000")
        self.b_src = tk.StringVar(value="00:00:00.000")
        self.b_dst = tk.StringVar(value="00:00:00.000")

        for name, src, dst in (
            ("A 지점", self.a_src, self.a_dst),
            ("B 지점", self.b_src, self.b_dst),
        ):
            card_outer, card = self._card(tab)
            card_outer.pack(fill="x", pady=(0, 7))
            top = tk.Frame(card, bg=theme.PANEL_ALT)
            top.pack(fill="x")
            tk.Label(
                top,
                text=name,
                bg=theme.PANEL_ALT,
                fg=theme.ACCENT,
                font=theme.FONT_SMALL_BOLD,
            ).pack(side="left")
            tk.Label(
                top,
                text="자막 시각  →  실제 영상 시각",
                bg=theme.PANEL_ALT,
                fg=theme.MUTED,
                font=theme.FONT_TINY,
            ).pack(side="left", padx=(8, 0))

            row = tk.Frame(card, bg=theme.PANEL_ALT)
            row.pack(fill="x", pady=(6, 0))
            entry(row, src, width=11, font=theme.MONO, justify="center").pack(
                side="left", fill="x", expand=True, ipady=3
            )
            tk.Label(
                row,
                text="→",
                bg=theme.PANEL_ALT,
                fg=theme.MUTED,
                font=theme.FONT,
            ).pack(side="left", padx=6)
            entry(row, dst, width=11, font=theme.MONO, justify="center").pack(
                side="left", fill="x", expand=True, ipady=3
            )
            button(
                card,
                "현재 재생 위치를 실제 시각으로",
                command=lambda var=dst: self._set_from_playhead(var),
                kind="bare",
                pady=3,
                font=theme.FONT_TINY,
            ).pack(anchor="e", pady=(4, 0))

        pick = tk.Frame(tab, bg=theme.PANEL)
        pick.pack(fill="x", pady=(2, 8))
        button(
            pick,
            "선택 자막을 A로",
            lambda: self._set_from_selection(self.a_src),
            kind="ghost",
            pady=5,
            padx=6,
        ).pack(side="left", fill="x", expand=True)
        button(
            pick,
            "선택 자막을 B로",
            lambda: self._set_from_selection(self.b_src),
            kind="ghost",
            pady=5,
            padx=6,
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        self.ratio_label = tk.Label(
            tab,
            text="배율 100.00% · 오프셋 +0.000초",
            bg=theme.PANEL_ALT,
            fg=theme.MUTED,
            font=theme.MONO,
            anchor="w",
            padx=10,
            pady=7,
        )
        self.ratio_label.pack(fill="x", pady=(0, 8))

        button(
            tab,
            "두 지점 기준으로 보정",
            self._apply_rate,
            kind="accent",
            pady=8,
        ).pack(fill="x")

        for variable in (self.a_src, self.a_dst, self.b_src, self.b_dst):
            variable.trace_add("write", lambda *_args: self._update_ratio())

        self._separator(tab)
        hint(
            tab,
            "A는 앞부분에서 잘 맞는 자막, B는 뒤부분에서 잘 맞는 자막을 고르는 것이 가장 정확합니다. "
            "'현재 재생 위치를 실제 시각으로'는 영상의 실제 위치를 재생하며 지정할 때 사용하세요. "
            "B가 A보다 뒤에 있어야 합니다.",
        ).pack(anchor="w")

    def _set_from_playhead(self, variable) -> None:
        variable.set(format_clock(self.app.playhead))

    def _set_from_selection(self, variable) -> None:
        index = self.app.selected_index
        if index is None or not self.app.doc or index >= len(self.app.doc.cues):
            self.app.set_status("먼저 자막을 하나 선택하세요.", "warn")
            return
        variable.set(format_clock(self.app.doc.cues[index].start))

    def _update_ratio(self) -> None:
        try:
            src_a = parse_timecode(self.a_src.get())
            dst_a = parse_timecode(self.a_dst.get())
            src_b = parse_timecode(self.b_src.get())
            dst_b = parse_timecode(self.b_dst.get())
            factor, offset = linear_parameters(src_a, dst_a, src_b, dst_b)
        except ValueError:
            self.ratio_label.configure(
                text="두 지점을 입력하면 배율이 계산됩니다.", fg=theme.FAINT
            )
            return
        self.ratio_label.configure(
            text=f"배율 {factor * 100:6.3f}%  ·  오프셋 {offset:+.3f}초",
            fg=theme.ACCENT if abs(factor - 1.0) > 1e-6 else theme.MUTED,
        )

    def _apply_rate(self) -> None:
        try:
            src_a = parse_timecode(self.a_src.get())
            dst_a = parse_timecode(self.a_dst.get())
            src_b = parse_timecode(self.b_src.get())
            dst_b = parse_timecode(self.b_dst.get())
        except ValueError as exc:
            self.app.set_status(str(exc), "error")
            return
        self.app.apply_two_point(src_a, dst_a, src_b, dst_b)

    # ------------------------------------------------------------------
    # 3. FPS
    # ------------------------------------------------------------------
    def _build_fps_tab(self) -> None:
        tab = self._tab("FPS")
        self._heading(
            tab,
            "프레임레이트 차이 보정",
            "자막 기준 FPS와 실제 영상 FPS가 다를 때 발생하는 시간 차이를 비율로 보정합니다.",
        )

        self.fps_sub = tk.StringVar(value="23.976")
        self.fps_video = tk.StringVar(value="25")

        row = tk.Frame(tab, bg=theme.PANEL)
        row.pack(fill="x")
        left = tk.Frame(row, bg=theme.PANEL)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(
            left,
            text="자막 기준 FPS",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
        ).pack(anchor="w")
        combo(left, self.fps_sub, FPS_VALUES, width=9).pack(fill="x", pady=(3, 0))

        swap = button(
            row,
            "⇄",
            self._swap_fps,
            kind="ghost",
            padx=7,
            pady=4,
            tooltip="자막/영상 FPS 바꾸기",
        )
        swap.pack(side="left", padx=7, pady=(17, 0))

        right = tk.Frame(row, bg=theme.PANEL)
        right.pack(side="left", fill="x", expand=True)
        tk.Label(
            right,
            text="실제 영상 FPS",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
        ).pack(anchor="w")
        combo(right, self.fps_video, FPS_VALUES, width=9).pack(fill="x", pady=(3, 0))

        self.fps_label = tk.Label(
            tab,
            text="배율 95.904%  ·  2시간 기준 약 -4분 55초",
            bg=theme.PANEL_ALT,
            fg=theme.ACCENT,
            font=theme.MONO,
            anchor="w",
            padx=10,
            pady=8,
            justify="left",
            wraplength=305,
        )
        self.fps_label.pack(fill="x", pady=(10, 8))

        presets = tk.Frame(tab, bg=theme.PANEL)
        presets.pack(fill="x", pady=(0, 8))
        preset_pairs = [
            ("23.976 → 25", "23.976", "25"),
            ("25 → 23.976", "25", "23.976"),
            ("23.976 → 24", "23.976", "24"),
            ("24 → 23.976", "24", "23.976"),
            ("29.97 → 30", "29.97", "30"),
            ("30 → 29.97", "30", "29.97"),
        ]
        for i, (label, sub, video) in enumerate(preset_pairs):
            presets.grid_columnconfigure(i % 2, weight=1)
            button(
                presets,
                label,
                command=lambda s=sub, v=video: self._set_fps_preset(s, v),
                kind="ghost",
                pady=4,
                padx=4,
                font=theme.FONT_TINY,
            ).grid(
                row=i // 2,
                column=i % 2,
                sticky="ew",
                padx=(0 if i % 2 == 0 else 4, 0),
                pady=(0, 4),
            )

        anchor_row = tk.Frame(tab, bg=theme.PANEL)
        anchor_row.pack(fill="x", pady=(2, 2))
        self.fps_anchor_enabled = tk.BooleanVar(value=True)
        check(
            anchor_row,
            "첫 자막 위치 고정",
            self.fps_anchor_enabled,
            command=self._update_fps_label,
        ).pack(side="left")
        self.fps_anchor = tk.StringVar(value="00:00:00.000")
        entry(
            anchor_row,
            self.fps_anchor,
            width=12,
            font=theme.MONO,
            justify="center",
        ).pack(side="right", ipady=2)

        button(
            tab,
            "FPS 차이 보정 적용",
            self._apply_fps,
            kind="accent",
            pady=8,
        ).pack(fill="x", pady=(8, 0))

        for variable in (self.fps_sub, self.fps_video, self.fps_anchor):
            variable.trace_add("write", lambda *_args: self._update_fps_label())

        self._separator(tab)
        hint(
            tab,
            "PAL 속도 변환(23.976 → 25)은 자막 간격이 4.1% 짧아집니다. "
            "'첫 자막 위치 고정'을 켜면 앞부분 정렬을 유지한 채 뒤쪽만 당겨집니다. "
            "FPS 값은 직접 입력할 수도 있습니다.",
        ).pack(anchor="w")

    def _set_fps_preset(self, sub: str, video: str) -> None:
        self.fps_sub.set(sub)
        self.fps_video.set(video)

    def _swap_fps(self) -> None:
        sub = self.fps_sub.get()
        video = self.fps_video.get()
        self.fps_sub.set(video)
        self.fps_video.set(sub)

    def _parse_fps(self, value: str) -> float:
        try:
            fps = float(value)
        except ValueError:
            raise ValueError("FPS는 숫자로 입력하세요.") from None
        if not 1.0 <= fps <= 300.0:
            raise ValueError("FPS는 1 ~ 300 사이여야 합니다.")
        return fps

    def _update_fps_label(self) -> None:
        try:
            sub = self._parse_fps(self.fps_sub.get())
            video = self._parse_fps(self.fps_video.get())
        except ValueError:
            self.fps_label.configure(text="FPS 값을 확인하세요.", fg=theme.FAINT)
            return
        factor = sub / video
        drift = (factor - 1.0) * 7200.0
        sign = "+" if drift >= 0 else "-"
        minutes = int(abs(drift) // 60)
        seconds = int(abs(drift) % 60)
        self.fps_label.configure(
            text=(
                f"배율 {factor * 100:.3f}%  ·  2시간 기준 {sign}{minutes}분 {seconds}초"
            ),
            fg=theme.ACCENT if abs(factor - 1.0) > 1e-6 else theme.MUTED,
        )

    def _apply_fps(self) -> None:
        try:
            sub = self._parse_fps(self.fps_sub.get())
            video = self._parse_fps(self.fps_video.get())
            anchor = (
                parse_timecode(self.fps_anchor.get())
                if self.fps_anchor_enabled.get()
                else None
            )
        except ValueError as exc:
            self.app.set_status(str(exc), "error")
            return
        self.app.apply_fps(sub, video, anchor)

    # ------------------------------------------------------------------
    # 4. Section / range
    # ------------------------------------------------------------------
    def _build_section_tab(self) -> None:
        tab = self._tab("구간 보정")
        self._heading(
            tab,
            "구간별 개별 보정",
            "특정 장면부터 싱크가 틀어질 때 그 지점 이후만, 또는 선택한 자막 구간만 이동합니다.",
        )

        self.from_index = tk.StringVar(value="1")
        self.from_delta = tk.StringVar(value="+0.500")
        self.range_first = tk.StringVar(value="1")
        self.range_last = tk.StringVar(value="1")
        self.range_delta = tk.StringVar(value="+0.500")
        self.time_value = tk.StringVar(value="00:00:00.000")
        self.time_delta = tk.StringVar(value="+0.500")

        # From cue N onward
        card_outer, card = self._card(tab)
        card_outer.pack(fill="x", pady=(0, 8))
        tk.Label(
            card,
            text="N번 자막부터 이후 전체",
            bg=theme.PANEL_ALT,
            fg=theme.TEXT,
            font=theme.FONT_SMALL_BOLD,
            anchor="w",
        ).pack(anchor="w")
        row = tk.Frame(card, bg=theme.PANEL_ALT)
        row.pack(fill="x", pady=(7, 6))
        entry(row, self.from_index, width=6, justify="center").pack(side="left", ipady=3)
        tk.Label(
            row,
            text="번부터",
            bg=theme.PANEL_ALT,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
        ).pack(side="left", padx=(5, 8))
        entry(
            row,
            self.from_delta,
            width=9,
            font=theme.MONO,
            justify="center",
        ).pack(side="left", ipady=3)
        tk.Label(
            row,
            text="초",
            bg=theme.PANEL_ALT,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
        ).pack(side="left", padx=(4, 0))
        button(
            card,
            "선택 자막 번호 넣기",
            self._fill_from_selection,
            kind="bare",
            pady=3,
            font=theme.FONT_TINY,
        ).pack(anchor="e")
        button(
            card,
            "이 지점부터 적용",
            self._apply_from_index,
            kind="soft",
            pady=6,
        ).pack(fill="x", pady=(5, 0))

        # Range
        card_outer, card = self._card(tab)
        card_outer.pack(fill="x", pady=(0, 8))
        tk.Label(
            card,
            text="선택 구간만",
            bg=theme.PANEL_ALT,
            fg=theme.TEXT,
            font=theme.FONT_SMALL_BOLD,
            anchor="w",
        ).pack(anchor="w")
        row = tk.Frame(card, bg=theme.PANEL_ALT)
        row.pack(fill="x", pady=(7, 6))
        entry(row, self.range_first, width=5, justify="center").pack(side="left", ipady=3)
        tk.Label(
            row, text="~", bg=theme.PANEL_ALT, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(side="left", padx=4)
        entry(row, self.range_last, width=5, justify="center").pack(side="left", ipady=3)
        tk.Label(
            row, text="번", bg=theme.PANEL_ALT, fg=theme.MUTED, font=theme.FONT_SMALL
        ).pack(side="left", padx=(4, 7))
        entry(
            row,
            self.range_delta,
            width=8,
            font=theme.MONO,
            justify="center",
        ).pack(side="left", ipady=3)
        button(card, "구간 이동", self._apply_range, kind="soft", pady=6).pack(
            fill="x", pady=(3, 0)
        )

        # From time
        card_outer, card = self._card(tab)
        card_outer.pack(fill="x", pady=(0, 8))
        tk.Label(
            card,
            text="이 시각 이후 전체",
            bg=theme.PANEL_ALT,
            fg=theme.TEXT,
            font=theme.FONT_SMALL_BOLD,
            anchor="w",
        ).pack(anchor="w")
        row = tk.Frame(card, bg=theme.PANEL_ALT)
        row.pack(fill="x", pady=(7, 6))
        entry(
            row,
            self.time_value,
            width=12,
            font=theme.MONO,
            justify="center",
        ).pack(side="left", ipady=3)
        button(
            row,
            "재생 위치",
            lambda: self.time_value.set(format_clock(self.app.playhead)),
            kind="bare",
            pady=2,
            padx=5,
            font=theme.FONT_TINY,
        ).pack(side="left", padx=5)
        entry(
            row,
            self.time_delta,
            width=8,
            font=theme.MONO,
            justify="center",
        ).pack(side="left", ipady=3)
        button(card, "이 시각부터 적용", self._apply_from_time, kind="soft", pady=6).pack(
            fill="x", pady=(3, 0)
        )

        self._separator(tab)
        hint(
            tab,
            "음수 값을 입력하면 앞으로 당깁니다. 'N번부터'는 N번 자막을 포함해 뒤쪽 전체를, "
            "'선택 구간만'은 지정한 번호 사이만 이동합니다. 장면 전환 지점을 찾았다면 "
            "타임라인에서 클릭해 재생 위치를 잡은 뒤 '재생 위치' 버튼을 쓰세요.",
        ).pack(anchor="w")

    def _fill_from_selection(self) -> None:
        index = self.app.selected_index
        if index is None:
            self.app.set_status("먼저 자막을 하나 선택하세요.", "warn")
            return
        self.from_index.set(str(index + 1))

    def _index_value(self, variable: tk.StringVar, name: str, cue_count: int) -> int:
        try:
            value = int(float(variable.get()))
        except ValueError:
            raise ValueError(f"{name}은 숫자로 입력하세요.") from None
        if not 1 <= value <= cue_count:
            raise ValueError(f"{name}은 1 ~ {cue_count} 사이여야 합니다.")
        return value

    def _apply_from_index(self) -> None:
        if not self.app.doc or not self.app.doc.cues:
            self.app.set_status("자막 파일을 먼저 여세요.", "warn")
            return
        try:
            index = self._index_value(self.from_index, "자막 번호", len(self.app.doc.cues))
            delta = parse_offset(self.from_delta.get())
        except ValueError as exc:
            self.app.set_status(str(exc), "error")
            return
        self.app.shift_from_index(index - 1, delta)

    def _apply_range(self) -> None:
        if not self.app.doc or not self.app.doc.cues:
            self.app.set_status("자막 파일을 먼저 여세요.", "warn")
            return
        try:
            count = len(self.app.doc.cues)
            first = self._index_value(self.range_first, "시작 번호", count)
            last = self._index_value(self.range_last, "끝 번호", count)
            if first > last:
                raise ValueError("시작 번호가 끝 번호보다 클 수 없습니다.")
            delta = parse_offset(self.range_delta.get())
        except ValueError as exc:
            self.app.set_status(str(exc), "error")
            return
        self.app.shift_range(first - 1, last - 1, delta)

    def _apply_from_time(self) -> None:
        if not self.app.doc or not self.app.doc.cues:
            self.app.set_status("자막 파일을 먼저 여세요.", "warn")
            return
        try:
            threshold = parse_timecode(self.time_value.get())
            delta = parse_offset(self.time_delta.get())
        except ValueError as exc:
            self.app.set_status(str(exc), "error")
            return
        self.app.shift_from_time(threshold, delta)

    # ------------------------------------------------------------------
    # 5. Audio alignment
    # ------------------------------------------------------------------
    def _build_audio_tab(self) -> None:
        tab = self._tab("음성 정렬")
        self._heading(
            tab,
            "음성 구간 기준 정렬",
            "파형에서 말소리 시작·끝을 찾아 자막의 표시 시간을 실제 음성에 맞춥니다.",
        )

        card_outer, card = self._card(tab)
        card_outer.pack(fill="x", pady=(0, 10))
        tk.Label(
            card,
            text="음성 분석 상태",
            bg=theme.PANEL_ALT,
            fg=theme.MUTED,
            font=theme.FONT_TINY,
            anchor="w",
        ).pack(anchor="w")
        self.audio_status = tk.Label(
            card,
            text="미디어를 열면 자동으로 분석합니다.",
            bg=theme.PANEL_ALT,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=290,
        )
        self.audio_status.pack(anchor="w", pady=(3, 5))
        self.audio_detail = tk.Label(
            card,
            text="",
            bg=theme.PANEL_ALT,
            fg=theme.FAINT,
            font=theme.FONT_TINY,
            anchor="w",
            justify="left",
            wraplength=290,
        )
        self.audio_detail.pack(anchor="w")

        target_row = tk.Frame(tab, bg=theme.PANEL)
        target_row.pack(fill="x", pady=(0, 6))
        tk.Label(
            target_row,
            text="대상",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            width=6,
            anchor="w",
        ).pack(side="left")
        self.align_target = tk.StringVar(value="all")
        radio(target_row, "전체", self.align_target, "all").pack(side="left", padx=(0, 10))
        radio(target_row, "선택 자막", self.align_target, "selected").pack(side="left")

        self.window_var = tk.StringVar(value="1.50")
        row, _widget = self._field(tab, "탐색 범위 (±초)", self.window_var, width=7, mono=True)
        row.pack(fill="x", pady=(4, 6))

        self.snap_end_var = tk.BooleanVar(value=True)
        check(tab, "종료 시간도 음성 끝에 맞춤", self.snap_end_var).pack(anchor="w")
        self.fallback_var = tk.BooleanVar(value=False)
        check(
            tab,
            "매칭 실패 시 이웃 이동값 사용",
            self.fallback_var,
        ).pack(anchor="w", pady=(2, 8))

        sensitivity_header = tk.Frame(tab, bg=theme.PANEL)
        sensitivity_header.pack(fill="x")
        tk.Label(
            sensitivity_header,
            text="감지 민감도",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
        ).pack(side="left")
        self.sens_label = tk.Label(
            sensitivity_header,
            text="50",
            bg=theme.PANEL,
            fg=theme.ACCENT,
            font=theme.FONT_SMALL_BOLD,
        )
        self.sens_label.pack(side="right")
        self.sensitivity_var = tk.DoubleVar(value=50.0)
        self.sensitivity_scale = ttk.Scale(
            tab,
            from_=0,
            to=100,
            orient="horizontal",
            variable=self.sensitivity_var,
            style="Card.Horizontal.TScale",
            command=self._on_sensitivity_preview,
        )
        self.sensitivity_scale.pack(fill="x", pady=(3, 10))
        self.sensitivity_scale.bind("<ButtonRelease-1>", self._on_sensitivity_commit)
        self.sensitivity_scale.bind("<KeyRelease>", self._on_sensitivity_commit)

        button(
            tab,
            "음성 기준 자동 정렬",
            self._apply_auto_align,
            kind="accent",
            pady=8,
        ).pack(fill="x", pady=(0, 5))

        pair = tk.Frame(tab, bg=theme.PANEL)
        pair.pack(fill="x", pady=(0, 8))
        button(
            pair,
            "시작만 맞춤",
            lambda: self.app.snap_selected("start"),
            kind="soft",
            pady=5,
            padx=4,
        ).pack(side="left", fill="x", expand=True)
        button(
            pair,
            "끝만 맞춤",
            lambda: self.app.snap_selected("end"),
            kind="soft",
            pady=5,
            padx=4,
        ).pack(side="left", fill="x", expand=True, padx=(5, 0))

        button(
            tab,
            "전체 오프셋 추천 · 적용",
            self.app.apply_suggested_offset,
            kind="ghost",
            pady=7,
        ).pack(fill="x")
        self.align_result = tk.Label(
            tab,
            text="",
            bg=theme.PANEL,
            fg=theme.MUTED,
            font=theme.FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=320,
        )
        self.align_result.pack(anchor="w", pady=(5, 0))

        self._separator(tab)
        hint(
            tab,
            "MP4/MKV 영상은 FFmpeg가 필요합니다. 감지 민감도를 올리면 작은 목소리도 잡지만 "
            "배경 음악을 말소리로 오인할 수 있습니다. 타임라인의 초록 음영이 감지된 음성 구간입니다. "
            "자동 정렬 후 드래그로 미세 조정하고, 필요하면 Ctrl+Z로 되돌리세요.",
        ).pack(anchor="w")

    def _on_sensitivity_preview(self, value) -> None:
        self.sens_label.configure(text=f"{float(value):.0f}")

    def _on_sensitivity_commit(self, _event=None) -> None:
        if self.app.analysis is None:
            return
        self.app.set_sensitivity(float(self.sensitivity_var.get()))

    def _apply_auto_align(self) -> None:
        if self.app.analysis is None or not self.app.segments:
            self.app.set_status(
                "먼저 미디어를 열고 음성 분석이 끝나야 합니다. FFmpeg 설치도 확인하세요.",
                "warn",
            )
            return
        try:
            window = float(self.window_var.get())
        except ValueError:
            self.app.set_status("탐색 범위는 숫자로 입력하세요.", "error")
            return
        if not 0.2 <= window <= 15.0:
            self.app.set_status("탐색 범위는 0.2 ~ 15초 사이여야 합니다.", "error")
            return
        target = self.align_target.get()
        self.app.auto_align(
            target=target,
            window=window,
            snap_end=self.snap_end_var.get(),
            fallback=self.fallback_var.get(),
        )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def on_document_changed(self) -> None:
        doc = self.app.doc
        if doc and doc.cues:
            first = format_clock(doc.first_start)
            last = format_clock(doc.last_end)
            self.a_src.set(first)
            self.a_dst.set(first)
            self.b_src.set(last)
            self.b_dst.set(last)
            self.fps_anchor.set(first)
            self.time_value.set(first)
        else:
            for variable in (self.a_src, self.a_dst, self.b_src, self.b_dst):
                variable.set("00:00:00.000")
            self.fps_anchor.set("00:00:00.000")
            self.time_value.set("00:00:00.000")
        self.from_index.set("1")
        self.range_first.set("1")
        self.range_last.set("1")
        self._update_ratio()
        self._update_fps_label()
        self.refresh()

    def refresh(self) -> None:
        self._update_ratio()
        self._update_fps_label()
        self.update_audio_state()
        self.update_suggestion()

    def update_audio_state(self) -> None:
        analysis = self.app.analysis
        segments = self.app.segments
        if analysis is None:
            self.audio_status.configure(
                text="미디어가 연결되지 않았습니다.",
                fg=theme.MUTED,
            )
            self.audio_detail.configure(
                text="영상/오디오를 열면 파형과 음성 구간이 표시됩니다.",
                fg=theme.FAINT,
            )
            return

        coverage = speech_coverage(self.app.doc.cues, segments) if self.app.doc else 0.0
        if segments:
            self.audio_status.configure(
                text=f"음성 구간 {len(segments)}개 감지됨",
                fg=theme.OK,
            )
            self.audio_detail.configure(
                text=(
                    f"자막 시작점 일치율 {coverage * 100:.0f}%  ·  "
                    f"분석 길이 {format_clock(analysis.duration)}"
                ),
                fg=theme.MUTED,
            )
        else:
            self.audio_status.configure(text="음성 구간을 찾지 못했습니다.", fg=theme.WARN)
            self.audio_detail.configure(
                text="감지 민감도를 높이거나 다른 미디어를 사용해 보세요.",
                fg=theme.MUTED,
            )

    def update_suggestion(self) -> None:
        if not self.app.doc or not self.app.doc.cues or not self.app.segments:
            self.suggest_label.configure(
                text="음성 구간을 분석하면 전체 이동값을 자동으로 추천합니다.",
                fg=theme.FAINT,
            )
            return
        value, matches = suggest_offset(
            self.app.doc.cues, self.app.segments, window=5.0
        )
        if value is None:
            self.suggest_label.configure(
                text=f"추천값을 계산하기에 일치하는 자막이 부족합니다. (일치 {matches}개)",
                fg=theme.FAINT,
            )
        else:
            self.suggest_label.configure(
                text=f"음성 기준 추천 오프셋 {value:+.3f}초 (일치 {matches}개)",
                fg=theme.OK,
            )

    def show_align_result(self, text: str, ok: bool = True) -> None:
        self.align_result.configure(
            text=text,
            fg=theme.OK if ok else theme.WARN,
        )

    def show_suggestion(self, text: str, ok: bool = True) -> None:
        self.suggest_label.configure(
            text=text,
            fg=theme.OK if ok else theme.WARN,
        )
