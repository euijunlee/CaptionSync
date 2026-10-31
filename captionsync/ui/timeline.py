"""Interactive timeline: waveform, speech regions, cue bars and playhead."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont

from ..models import MIN_DURATION, Cue, Document
from ..timecode import format_clock, format_short_time
from . import theme

RULER_H = 27
CUE_TOP = 34
LANE_H = 33
CUE_INSET = 4
MAX_LANES = 3


class Timeline(tk.Canvas):
    def __init__(
        self,
        master,
        on_seek=None,
        on_select=None,
        on_cue_drag=None,
        on_view_change=None,
    ):
        super().__init__(
            master,
            bg=theme.PANEL_ALT,
            highlightthickness=0,
            bd=0,
            cursor="xterm",
        )
        self.on_seek = on_seek
        self.on_select = on_select
        self.on_cue_drag = on_cue_drag
        self.on_view_change = on_view_change

        self.doc: Document | None = None
        self.analysis = None
        self.segments: list[tuple[float, float]] = []
        self.media_duration = 0.0

        self.view_start = 0.0
        self.view_span = 120.0
        self.playhead = 0.0
        self.selected: int | None = None
        self.active: tuple[int, ...] = ()
        self.snap_enabled = True

        self._width = 800
        self._height = 420
        self._label_font = tkfont.Font(font=theme.FONT_TINY)
        self._env_scale = 1.0
        self._lanes: dict[int, int] = {}
        self._lane_count = 1
        self._playhead_item = None
        self._drag = None
        self._drag_cue = None
        self._drag_snap: float | None = None
        self._redraw_job = None
        self._hover_cursor = "xterm"

        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _event: self.configure(cursor="xterm"))
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", lambda event: self._wheel_step(event, 1))
        self.bind("<Button-5>", lambda event: self._wheel_step(event, -1))

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    def set_document(self, doc: Document | None) -> None:
        self.doc = doc
        self.selected = None
        self.active = ()
        self.playhead = 0.0
        if doc and doc.cues:
            start = max(0.0, doc.first_start - 1.0)
            end = max(doc.last_end, start + 20.0)
            self.view_start = start
            self.view_span = min(160.0, max(20.0, end - start + 2.0))
        else:
            self.view_start = 0.0
            self.view_span = 120.0
        self.redraw()

    def set_analysis(self, analysis) -> None:
        self.analysis = analysis
        if analysis and analysis.envelope:
            count = len(analysis.envelope)
            sample = [float(v) for v in analysis.envelope[:: max(1, count // 40000)]]
            sample.sort()
            if sample:
                index = min(len(sample) - 1, int(len(sample) * 0.995))
                self._env_scale = max(0.02, sample[index])
            else:
                self._env_scale = 1.0
        else:
            self._env_scale = 1.0
        self.redraw()

    def set_segments(self, segments: list[tuple[float, float]]) -> None:
        self.segments = list(segments)
        self.redraw()

    def set_media_duration(self, seconds: float) -> None:
        self.media_duration = max(0.0, seconds)
        self.redraw()

    def set_selected(self, index: int | None) -> None:
        if self.selected == index:
            return
        self.selected = index
        self.redraw()

    def set_playhead(self, seconds: float, follow: bool = False) -> None:
        self.playhead = max(0.0, seconds)
        if self.doc:
            active = tuple(self.doc.cue_at(self.playhead))
        else:
            active = ()
        if active != self.active:
            self.active = active
            self.redraw()
            return

        view_end = self.view_start + self.view_span
        if follow and (self.playhead > view_end - self.view_span * 0.08 or self.playhead < self.view_start):
            self.view_start = max(0.0, self.playhead - self.view_span * 0.15)
            self._notify_view()
            self.redraw()
            return

        self._position_playhead()

    def set_snap_enabled(self, enabled: bool) -> None:
        self.snap_enabled = bool(enabled)

    # ------------------------------------------------------------------
    # View helpers
    # ------------------------------------------------------------------
    @property
    def max_time(self) -> float:
        doc_end = self.doc.last_end if self.doc else 0.0
        return max(doc_end + 5.0, self.media_duration + 2.0, 30.0)

    def fit(self) -> None:
        self.view_start = 0.0
        self.view_span = max(5.0, self.max_time)
        self._notify_view()
        self.redraw()

    def zoom(self, factor: float, anchor: float | None = None) -> None:
        if factor <= 0:
            return
        if anchor is None:
            anchor = self.playhead if self.playhead else self.view_start + self.view_span / 2
        anchor = max(self.view_start, min(self.view_start + self.view_span, anchor))
        ratio = (anchor - self.view_start) / self.view_span
        new_span = max(1.0, min(self.max_time * 1.5, self.view_span / factor))
        new_start = anchor - ratio * new_span
        new_start = max(0.0, min(new_start, max(0.0, self.max_time - new_span)))
        self.view_start = new_start
        self.view_span = new_span
        self._notify_view()
        self.redraw()

    def pan(self, seconds: float) -> None:
        new_start = self.view_start + seconds
        new_start = max(0.0, min(new_start, max(0.0, self.max_time - self.view_span)))
        if new_start == self.view_start:
            return
        self.view_start = new_start
        self._notify_view()
        self.redraw()

    def _notify_view(self) -> None:
        if self.on_view_change:
            self.on_view_change(self.view_span)

    def _x(self, seconds: float) -> float:
        return (seconds - self.view_start) / self.view_span * self._width

    def _time(self, x: float) -> float:
        return self.view_start + (x / max(1, self._width)) * self.view_span

    def _on_configure(self, event) -> None:
        self._width = max(100, event.width)
        self._height = max(120, event.height)
        self.redraw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def redraw(self) -> None:
        if not self.winfo_exists():
            return
        self._width = max(100, self.winfo_width())
        self._height = max(120, self.winfo_height())
        self.delete("all")
        self._playhead_item = None

        if self.doc and self.doc.cues:
            self._assign_lanes()
        else:
            self._lanes = {}
            self._lane_count = 1

        cue_bottom = CUE_TOP + max(1, self._lane_count) * LANE_H
        wave_top = min(self._height - 60, cue_bottom + 8)
        wave_height = max(30, self._height - wave_top - 10)

        self._draw_ruler()
        self._draw_waveform(wave_top, wave_height)
        self._draw_speech_bands(wave_top, wave_height)
        self._draw_cues()

        if self.doc and self.doc.cues:
            if self._drag_snap is not None:
                x = self._x(self._drag_snap)
                self.create_line(
                    x,
                    RULER_H,
                    x,
                    self._height - 4,
                    fill=theme.OK,
                    dash=(3, 3),
                    width=1,
                )
        else:
            center = (RULER_H + self._height) / 2
            for line_number, text in enumerate(
                ("자막 파일을 열어 시작하세요", "Ctrl+O  ·  SRT / VTT / ASS")
            ):
                self.create_text(
                    self._width / 2,
                    center + (line_number - 0.5) * 22,
                    text=text,
                    fill=theme.FAINT,
                    font=(theme.FONT[0], 13),
                    anchor="center",
                )

        self._draw_playhead()

    def _assign_lanes(self) -> None:
        cues = self.doc.cues
        order = sorted(range(len(cues)), key=lambda i: (cues[i].start, cues[i].end))
        lane_ends: list[float] = []
        lanes: dict[int, int] = {}
        max_lanes = MAX_LANES if self._height > 320 else 2
        for index in order:
            cue = cues[index]
            placed = None
            for lane, end in enumerate(lane_ends):
                if cue.start >= end + 0.002:
                    lane_ends[lane] = max(end, cue.end)
                    placed = lane
                    break
            if placed is None:
                if len(lane_ends) < max_lanes:
                    lane_ends.append(cue.end)
                    placed = len(lane_ends) - 1
                else:
                    placed = min(range(len(lane_ends)), key=lambda i: lane_ends[i])
                    lane_ends[placed] = max(lane_ends[placed], cue.end)
            lanes[index] = placed
        self._lanes = lanes
        self._lane_count = max(1, len(lane_ends))

    def _draw_ruler(self) -> None:
        self.create_rectangle(0, 0, self._width, RULER_H, fill=theme.PANEL, outline="")
        self.create_line(0, RULER_H, self._width, RULER_H, fill=theme.BORDER, width=1)

        span = self.view_span
        candidates = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]
        step = candidates[-1]
        for candidate in candidates:
            if candidate / span * self._width >= 82:
                step = candidate
                break

        first = math.floor(self.view_start / step) * step
        value = first
        while value <= self.view_start + span + step:
            x = self._x(value)
            if -30 <= x <= self._width + 30:
                self.create_line(x, RULER_H - 6, x, RULER_H, fill=theme.BORDER_LIGHT)
                self.create_line(
                    x,
                    RULER_H,
                    x,
                    self._height,
                    fill=theme.PANEL_HOVER,
                    width=1,
                )
                self.create_text(
                    x + 5,
                    11,
                    text=format_short_time(value),
                    fill=theme.MUTED,
                    font=theme.FONT_TINY,
                    anchor="w",
                )
            value += step

        if self.analysis and self.segments:
            label = f"음성 구간 {len(self.segments)}개"
            self.create_text(
                self._width - 10,
                11,
                text=label,
                fill=theme.FAINT,
                font=theme.FONT_TINY,
                anchor="e",
            )

    def _draw_waveform(self, top: int, height: int) -> None:
        if not self.analysis or not self.analysis.envelope:
            center = top + height / 2
            message_lines = (
                "자막 파일을 열어 시작하세요",
                "Ctrl+Shift+O로 미디어 열기",
                "(FFmpeg가 있으면 MP4/MKV도 지원)",
            )
            # Draw each line independently.  Tk Canvas can compress newline
            # line boxes on small/high-DPI layouts, making the empty-state
            # hint look as if the Korean text is overlapping.
            line_gap = min(22, max(16, height // 8))
            for line_number, text in enumerate(message_lines):
                y = center + (line_number - 1) * line_gap
                self.create_text(
                    self._width / 2,
                    y,
                    text=text,
                    fill=theme.FAINT,
                    font=theme.FONT_SMALL,
                    anchor="center",
                )
            return

        envelope = self.analysis.envelope
        frame_hz = self.analysis.frame_hz
        middle = top + height / 2
        amplitude = height / 2 - 4
        step = 2 if self._width > 500 else 3

        upper: list[float] = []
        lower: list[float] = []
        for x in range(0, int(self._width) + step, step):
            t0 = self._time(x)
            t1 = self._time(x + step)
            start = max(0, int(t0 * frame_hz))
            end = min(len(envelope), max(start + 1, int(t1 * frame_hz)))
            if start >= len(envelope):
                peak = 0.0
            else:
                peak = max(envelope[start:end])
            value = min(1.0, peak / self._env_scale) * amplitude
            upper.append(x)
            upper.append(middle - value)
            lower.append(x)
            lower.append(middle + value)

        points = upper + list(reversed(lower))
        if len(points) >= 6:
            self.create_polygon(
                points,
                fill="#193a34",
                outline="",
            )
            self.create_line(
                upper,
                fill=theme.WAVE,
                width=1,
            )
            self.create_line(
                lower,
                fill=theme.WAVE,
                width=1,
            )
        self.create_line(0, middle, self._width, middle, fill=theme.BORDER)

    def _draw_speech_bands(self, top: int, height: int) -> None:
        if not self.segments:
            return
        for start, end in self.segments:
            if end < self.view_start or start > self.view_start + self.view_span:
                continue
            x0 = max(0, self._x(start))
            x1 = min(self._width, self._x(end))
            if x1 - x0 < 1:
                continue
            self.create_rectangle(
                x0,
                top,
                x1,
                top + height,
                fill="#285f63",
                outline="",
                stipple="gray25",
            )

    def _cue_colors(self, index: int) -> tuple[str, str, str]:
        if index == self.selected:
            return theme.ACCENT, "#b9dcff", "#071421"
        if index in self.active:
            return theme.ACCENT_DIM, theme.ACCENT, "#ffffff"
        return "#263447", "#3c506c", "#cbd8e8"

    def _draw_cues(self) -> None:
        if not self.doc:
            return
        for index, cue in enumerate(self.doc.cues):
            if cue.end < self.view_start or cue.start > self.view_start + self.view_span:
                continue
            lane = self._lanes.get(index, 0)
            y0 = CUE_TOP + lane * LANE_H + CUE_INSET
            y1 = y0 + LANE_H - CUE_INSET * 2
            x0 = max(-20, self._x(cue.start))
            x1 = min(self._width + 20, self._x(cue.end))
            if x1 - x0 < 2:
                x1 = x0 + 2
            fill, outline, text_color = self._cue_colors(index)
            self.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                fill=fill,
                outline=outline,
                width=1,
            )
            width = x1 - x0
            if width > 26:
                label = f"{index + 1}. {cue.display_text()}"
                if width > 190:
                    label = f"{index + 1}  {format_clock(cue.start)[3:12]}  {cue.display_text()}"
                if self._label_font.measure(label) > width - 12:
                    label = str(index + 1)
                    if self._label_font.measure(label) > width - 8:
                        label = ""
                if label:
                    self.create_text(
                        max(4, x0 + 7),
                        (y0 + y1) / 2,
                        text=label,
                        fill=text_color,
                        font=theme.FONT_TINY,
                        anchor="w",
                    )
            if index == self.selected:
                handle_w = 3
                self.create_rectangle(
                    x0 + 1,
                    y0 + 4,
                    x0 + 1 + handle_w,
                    y1 - 4,
                    fill="#dbe9ff",
                    outline="",
                )
                self.create_rectangle(
                    x1 - 1 - handle_w,
                    y0 + 4,
                    x1 - 1,
                    y1 - 4,
                    fill="#dbe9ff",
                    outline="",
                )

    def _draw_playhead(self) -> None:
        x = self._x(self.playhead)
        if -10 <= x <= self._width + 10:
            self._playhead_item = self.create_line(
                x,
                0,
                x,
                self._height,
                fill=theme.ACCENT,
                width=1,
            )
            self.create_polygon(
                x - 5,
                0,
                x + 5,
                0,
                x,
                8,
                fill=theme.ACCENT,
                outline="",
                tags="playhead_tip",
            )
        else:
            self._playhead_item = None

    def _position_playhead(self) -> None:
        if self._playhead_item is None:
            self._draw_playhead()
            return
        x = self._x(self.playhead)
        if x < -10 or x > self._width + 10:
            self.update_idletasks()
            self.redraw()
            return
        self.coords(self._playhead_item, x, 0, x, self._height)
        items = self.find_withtag("playhead_tip")
        for item in items:
            self.delete(item)
        self.create_polygon(
            x - 5,
            0,
            x + 5,
            0,
            x,
            8,
            fill=theme.ACCENT,
            outline="",
            tags="playhead_tip",
        )

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------
    def _cue_at(self, x: float, y: float) -> tuple[int, str] | None:
        if not self.doc or y < CUE_TOP:
            return None
        lane = int((y - CUE_TOP) // LANE_H)
        if lane < 0 or lane >= self._lane_count:
            return None
        best = None
        for index, cue in enumerate(self.doc.cues):
            if self._lanes.get(index, 0) != lane:
                continue
            x0 = self._x(cue.start)
            x1 = self._x(cue.end)
            if x0 - 4 <= x <= x1 + 4:
                distance = 0 if x0 <= x <= x1 else min(abs(x - x0), abs(x - x1))
                if best is None or distance < best[0]:
                    edge = ""
                    if abs(x - x0) <= 7:
                        edge = "start"
                    elif abs(x - x1) <= 7:
                        edge = "end"
                    best = (distance, index, edge)
        if best is None:
            return None
        return best[1], best[2]

    def _on_press(self, event) -> None:
        self.focus_set()
        found = self._cue_at(event.x, event.y)
        if found:
            index, edge = found
            cue = self.doc.cues[index]
            if self.on_select:
                self.on_select(index)
            self._drag = {
                "index": index,
                "mode": edge or "move",
                "x": event.x,
                "start": cue.start,
                "end": cue.end,
                "moved": False,
            }
            self._drag_snap = None
            self.configure(cursor="sb_h_double_arrow" if edge else "fleur")
            return

        self._drag = None
        if self.on_seek:
            self.on_seek(max(0.0, self._time(event.x)))

    def _on_drag(self, event) -> None:
        if not self._drag or not self.doc:
            return
        data = self._drag
        cue = self.doc.cues[data["index"]]
        delta = (event.x - data["x"]) / max(1, self._width) * self.view_span
        start = data["start"]
        end = data["end"]
        mode = data["mode"]
        snapped = None

        if mode == "start":
            new_start = max(0.0, min(end - MIN_DURATION, start + delta))
            new_start, snapped = self._snap_time(new_start, "start")
            start = new_start
        elif mode == "end":
            new_end = max(start + MIN_DURATION, end + delta)
            new_end, snapped = self._snap_time(new_end, "end")
            end = new_end
        else:
            duration = end - start
            new_start = max(0.0, start + delta)
            snapped_start = self._snap_time(new_start, "start")[0]
            if snapped_start != new_start:
                new_start = max(0.0, snapped_start)
                snapped = snapped_start
            start = new_start
            end = new_start + duration

        if abs(start - data["start"]) < 1e-4 and abs(end - data["end"]) < 1e-4:
            return
        data["moved"] = True
        self._drag_snap = snapped
        if self.on_cue_drag:
            self.on_cue_drag(data["index"], start, end, False)
        self.redraw()

    def _on_release(self, event) -> None:
        if not self._drag:
            return
        data = self._drag
        self._drag = None
        self._drag_snap = None
        self.configure(cursor="xterm")
        if data["moved"] and self.on_cue_drag:
            cue = self.doc.cues[data["index"]] if self.doc else None
            if cue:
                self.on_cue_drag(data["index"], cue.start, cue.end, True)
        self.redraw()

    def _on_double(self, event) -> None:
        found = self._cue_at(event.x, event.y)
        if found and self.on_seek and self.doc:
            self.on_seek(self.doc.cues[found[0]].start)

    def _on_motion(self, event) -> None:
        if self._drag:
            return
        found = self._cue_at(event.x, event.y)
        cursor = "xterm"
        if found:
            cursor = "sb_h_double_arrow" if found[1] else "fleur"
        if cursor != self._hover_cursor:
            self._hover_cursor = cursor
            self.configure(cursor=cursor)

    def _snap_time(self, seconds: float, kind: str) -> tuple[float, float | None]:
        if not self.snap_enabled or not self.segments:
            return seconds, None
        tolerance = max(0.03, self.view_span * 8.0 / max(1, self._width))
        values = (
            [start for start, _ in self.segments]
            if kind == "start"
            else [end for _, end in self.segments]
        )
        if not values:
            return seconds, None
        nearest = min(values, key=lambda value: abs(value - seconds))
        if abs(nearest - seconds) <= tolerance:
            return nearest, nearest
        return seconds, None

    def _on_wheel(self, event) -> str:
        steps = int(event.delta / 120) if event.delta else 0
        return self._wheel_step(event, steps)

    def _wheel_step(self, event, steps: int) -> str:
        if not steps:
            return "break"
        anchor = self._time(event.x)
        if event.state & 0x0001:  # Shift: scroll horizontally
            self.pan(self.view_span * 0.08 * -steps)
        else:
            self.zoom(1.22**steps, anchor)
        return "break"
