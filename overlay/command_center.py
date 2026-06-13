"""Command Center window for Jarvis overlay observability."""

from __future__ import annotations

import os
import tkinter as tk
from typing import TYPE_CHECKING

from overlay.ui_theme import (
    BG,
    BORDER,
    BORDER_SOFT,
    CONTROL,
    CONTROL_ACTIVE,
    CONTROL_HOVER,
    DANGER,
    FONT_DISPLAY,
    FONT_MONO,
    FONT_UI,
    OK,
    PANEL,
    SURFACE,
    SURFACE_ALT,
    TEXT_DIM,
    TEXT_FAINT,
    TEXT_PRIMARY,
    WARN,
    STATE_LABELS,
    WINDOW_RADIUS,
)
from overlay.ui_widgets import apply_window_rounding, attach_tooltip
from jarvis_version import JARVIS_VERSION_LABEL

if TYPE_CHECKING:
    from overlay.window import JarvisOverlay


class CommandCenter:
    """Secondary dashboard window for state, memory, events and logs."""

    def __init__(self, overlay: "JarvisOverlay") -> None:
        self.overlay = overlay
        self.window: tk.Toplevel | None = None
        self.tab = "overview"
        self.frames: dict[str, tk.Frame] = {}
        self.tabs: dict[str, tk.Label] = {}
        self.overview: dict[str, tk.Label] = {}
        self.text_widgets: dict[str, tk.Text] = {}
        self.memory_status_label: tk.Label | None = None
        self._after_id: str | None = None
        self.tab_labels = {
            "overview": "Resumen",
            "memory": "Memoria",
            "events": "Eventos",
            "logs": "Logs",
        }

    def open(self) -> None:
        if self.window is not None:
            try:
                if self.window.winfo_exists():
                    self.window.lift()
                    self.window.focus_force()
                    return
            except tk.TclError:
                self.window = None

        win = tk.Toplevel(self.overlay.root)
        self.window = win
        win.title(f"JARVIS Command Center {JARVIS_VERSION_LABEL}")
        win.attributes("-topmost", True)
        win.configure(bg=BG)
        win.geometry("880x600+150+120")
        win.minsize(760, 480)
        win.protocol("WM_DELETE_WINDOW", self.close)
        apply_window_rounding(win, WINDOW_RADIUS)

        shell = tk.Frame(win, bg=BG)
        shell.pack(fill="both", expand=True, padx=18, pady=18)

        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x")

        tk.Label(
            header,
            text=f"JARVIS Center {JARVIS_VERSION_LABEL}",
            bg=BG,
            fg=TEXT_PRIMARY,
            font=(FONT_DISPLAY, 17, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            header,
            text="system telemetry and memory surface",
            bg=BG,
            fg=TEXT_FAINT,
            font=(FONT_UI, 8),
        ).pack(side="left", padx=(10, 0), pady=(5, 0))

        tabs = tk.Frame(shell, bg=BG)
        tabs.pack(fill="x", pady=(16, 12))
        self.tabs.clear()
        for key, label in self.tab_labels.items():
            tab = tk.Label(
                tabs,
                text=label,
                bg=CONTROL_ACTIVE if key == self.tab else CONTROL,
                fg=TEXT_PRIMARY if key == self.tab else TEXT_DIM,
                font=(FONT_DISPLAY, 8, "bold"),
                padx=16,
                pady=8,
                cursor="hand2",
            )
            tab.pack(side="left", padx=(0, 6))
            tab.bind("<Button-1>", lambda _, tab_key=key: self.select_tab(tab_key))
            self.tabs[key] = tab

        content = tk.Frame(shell, bg=BG)
        content.pack(fill="both", expand=True)
        self.frames = {
            "overview": self._build_overview(content),
            "memory": self._build_memory(content),
            "events": self._build_events(content),
            "logs": self._build_logs(content),
        }
        self.select_tab(self.tab)
        self._refresh_loop()
        self.overlay.log_event("Command Center abierto")

    def close(self) -> None:
        win = self.window
        self.window = None
        if self._after_id is not None and win is not None:
            try:
                win.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def select_tab(self, tab: str) -> None:
        self.tab = tab
        for key, frame in self.frames.items():
            if key == tab:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for key, label in self.tabs.items():
            active = key == tab
            label.config(
                bg=CONTROL_ACTIVE if active else CONTROL,
                fg=TEXT_PRIMARY if active else TEXT_DIM,
            )
        self.refresh_once()

    def _build_overview(self, parent: tk.Misc) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        grid = tk.Frame(frame, bg=BG)
        grid.pack(fill="x")
        self.overview.clear()
        cards = (
            ("state", "STATE"),
            ("mode", "MODE"),
            ("connection", "CONNECTION"),
            ("privacy", "PRIVACY"),
            ("duration", "SESSION"),
            ("total", "TOTAL COST"),
            ("gemini", "GEMINI"),
            ("claude", "CLAUDE"),
            ("memory", "MEMORY"),
        )
        for idx, (key, title) in enumerate(cards):
            card = tk.Frame(grid, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER_SOFT)
            row = idx // 3
            col = idx % 3
            card.grid(row=row, column=col, sticky="nsew", padx=5, pady=5)
            grid.grid_columnconfigure(col, weight=1, uniform="overview")
            tk.Label(
                card,
                text=title,
                bg=SURFACE,
                fg=TEXT_FAINT,
                font=(FONT_DISPLAY, 8, "bold"),
                anchor="w",
            ).pack(fill="x", padx=11, pady=(9, 0))
            value = tk.Label(
                card,
                text="-",
                bg=SURFACE,
                fg=TEXT_PRIMARY,
                font=(FONT_DISPLAY, 11, "bold"),
                anchor="w",
                justify="left",
                wraplength=170,
            )
            value.pack(fill="x", padx=11, pady=(3, 12))
            self.overview[key] = value

        usage_panel = tk.Frame(frame, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER_SOFT)
        usage_panel.pack(fill="both", expand=True, pady=(14, 0))
        tk.Label(
            usage_panel,
            text="MODEL USAGE",
            bg=SURFACE,
            fg=TEXT_FAINT,
            font=(FONT_DISPLAY, 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=11, pady=(9, 0))
        usage = tk.Text(
            usage_panel,
            height=8,
            bg=SURFACE_ALT,
            fg=TEXT_DIM,
            insertbackground=TEXT_DIM,
            font=(FONT_MONO, 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            highlightcolor=BORDER,
            wrap="none",
        )
        usage.pack(fill="both", expand=True, padx=11, pady=(7, 11))
        usage.config(state="disabled")
        self.text_widgets["usage"] = usage
        return frame

    def _build_memory(self, parent: tk.Misc) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        top = tk.Frame(frame, bg=BG)
        top.pack(fill="x", pady=(0, 8))
        self.memory_status_label = tk.Label(
            top,
            text="Sin actividad de memoria todavia",
            bg=BG,
            fg=TEXT_FAINT,
            font=(FONT_DISPLAY, 8, "bold"),
            anchor="w",
        )
        self.memory_status_label.pack(side="left", fill="x", expand=True)
        text = self._make_text(frame, wrap="word")
        self.text_widgets["memory"] = text
        return frame

    def _build_events(self, parent: tk.Misc) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        self.text_widgets["events"] = self._make_text(frame, wrap="word")
        return frame

    def _build_logs(self, parent: tk.Misc) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        toolbar = tk.Frame(frame, bg=BG)
        toolbar.pack(fill="x", pady=(0, 8))
        tk.Label(
            toolbar,
            text=str(self.overlay.log_path),
            bg=BG,
            fg=TEXT_FAINT,
            font=(FONT_UI, 8),
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        refresh = tk.Label(
            toolbar,
            text="Actualizar",
            bg=CONTROL,
            fg=TEXT_DIM,
            font=(FONT_DISPLAY, 8, "bold"),
            padx=12,
            pady=5,
            cursor="hand2",
        )
        refresh.pack(side="right")
        refresh.bind("<Button-1>", lambda _: self.refresh_once())
        self._wire_hover(refresh, CONTROL, CONTROL_HOVER)
        attach_tooltip(refresh, "Refrescar logs")
        self.text_widgets["logs"] = self._make_text(frame, wrap="none")
        return frame

    def _make_text(self, parent: tk.Misc, wrap: str) -> tk.Text:
        body = tk.Frame(parent, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER_SOFT)
        body.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(body, bg=BG, troughcolor=PANEL)
        scrollbar.pack(side="right", fill="y")
        text = tk.Text(
            body,
            bg=SURFACE_ALT,
            fg=TEXT_DIM,
            insertbackground=TEXT_DIM,
            font=(FONT_MONO, 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            highlightcolor=BORDER,
            wrap=wrap,
            yscrollcommand=scrollbar.set,
        )
        text.pack(side="left", fill="both", expand=True, padx=10, pady=10)
        scrollbar.config(command=text.yview)
        text.config(state="disabled")
        return text

    def _refresh_loop(self) -> None:
        if self.window is None:
            return
        try:
            if not self.window.winfo_exists():
                self.window = None
                return
        except tk.TclError:
            self.window = None
            return
        self.refresh_once()
        self._after_id = self.window.after(1000, self._refresh_loop)

    def refresh_once(self) -> None:
        if self.window is None:
            return
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            return

        snap = self.overlay.tracker.snapshot()
        report = self.overlay.gate.evaluate(self.overlay.tracker)
        duration = self._format_duration(snap.session_duration_s())
        self._set_overview("state", STATE_LABELS.get(self.overlay.state, self.overlay.state))
        self._set_overview("mode", self.overlay.mode)
        self._set_overview("connection", self.overlay.connection_label.cget("text"))
        self._set_overview("privacy", self.overlay.privacy_label_text())
        self._set_overview("duration", duration)
        self._set_overview("total", f"${snap.total_cost_usd:.4f}")
        self._set_overview(
            "gemini",
            f"${report.gemini.spent_usd:.4f} / ${report.gemini.limit_usd:.2f}",
            report.gemini.status.color,
        )
        self._set_overview(
            "claude",
            f"${report.claude.spent_usd:.4f} / ${report.claude.limit_usd:.2f}",
            report.claude.status.color,
        )

        memory_ok = sum(1 for e in self.overlay.memory_events if e.get("status") == "ok")
        memory_running = sum(1 for e in self.overlay.memory_events if e.get("status") == "running")
        memory_errors = sum(1 for e in self.overlay.memory_events if e.get("status") == "error")
        memory_text = f"{memory_ok} ok"
        if memory_running:
            memory_text += f" | {memory_running} activo"
        if memory_errors:
            memory_text += f" | {memory_errors} error"
        self._set_overview("memory", memory_text, WARN if memory_running else TEXT_PRIMARY)
        self._render_tab_counts(len(self.overlay.memory_events), len(self.overlay.event_history))

        usage_lines = []
        for model, usage in sorted(snap.by_model.items()):
            total_tokens = (
                usage.input_tokens
                + usage.output_tokens
                + usage.cache_write_tokens
                + usage.cache_read_tokens
            )
            usage_lines.append(
                f"{model}\n"
                f"  tokens={total_tokens} in={usage.input_tokens} out={usage.output_tokens} "
                f"cache_w={usage.cache_write_tokens} cache_r={usage.cache_read_tokens} "
                f"events={usage.events} cost=${usage.cost_usd:.6f}"
            )
        self._set_text(
            self.text_widgets.get("usage"),
            "\n\n".join(usage_lines) if usage_lines else "Sin uso registrado en esta sesion.",
        )

        event_lines = [
            f"{stamp} [{level.upper()}] {message}"
            for stamp, level, message in self.overlay.event_history[-120:]
        ]
        self._set_text(
            self.text_widgets.get("events"),
            "\n".join(event_lines) if event_lines else "Sin eventos todavia.",
        )

        memory_lines = []
        for event in self.overlay.memory_events[-80:]:
            elapsed = event.get("elapsed_ms")
            elapsed_text = f" ({elapsed:.0f}ms)" if isinstance(elapsed, (int, float)) else ""
            memory_lines.append(
                f"{event.get('stamp', '--:--:--')} "
                f"[{str(event.get('status', 'info')).upper()}] "
                f"{event.get('summary', event.get('name', 'memoria'))}{elapsed_text}\n"
                f"  {event.get('detail', '')}"
            )
        self._set_text(
            self.text_widgets.get("memory"),
            "\n\n".join(memory_lines) if memory_lines else "Sin actividad de memoria todavia.",
        )
        self._render_memory_status()

        if self.tab == "logs":
            self._set_text(self.text_widgets.get("logs"), self._read_log_tail())

    def _render_memory_status(self) -> None:
        if self.memory_status_label is None:
            return
        last = self.overlay.memory_events[-1] if self.overlay.memory_events else None
        if last is None:
            self.memory_status_label.config(text="Sin actividad de memoria todavia", fg=TEXT_FAINT)
            return
        status = last.get("status")
        color = OK if status == "ok" else WARN if status == "running" else DANGER
        detail = last.get("detail", last.get("summary", "sin detalle"))
        self.memory_status_label.config(text=f"Ultima memoria: {detail}", fg=color)

    def _render_tab_counts(self, memory_count: int, event_count: int) -> None:
        counts = {
            "overview": "",
            "memory": f" {memory_count}" if memory_count else "",
            "events": f" {event_count}" if event_count else "",
            "logs": "",
        }
        for key, tab in self.tabs.items():
            tab.config(text=f"{self.tab_labels[key]}{counts[key]}")

    def _set_overview(self, key: str, text: str, fg: str | None = None) -> None:
        label = self.overview.get(key)
        if label is not None:
            label.config(text=text, fg=fg or TEXT_PRIMARY)

    @staticmethod
    def _set_text(widget: tk.Text | None, content: str) -> None:
        if widget is None:
            return
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.see("end")
        widget.config(state="disabled")

    def _read_log_tail(self, max_bytes: int = 80_000) -> str:
        log_path = self.overlay.log_path
        if not log_path.exists():
            return "Log file no encontrado todavia."
        try:
            with log_path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - max_bytes))
                data = fh.read()
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()[-220:]
            return "\n".join(lines) if lines else "Log vacio."
        except OSError as exc:
            return f"No pude leer el log: {type(exc).__name__}: {exc}"

    @staticmethod
    def _format_duration(seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {sec}s"
        return f"{sec}s"

    @staticmethod
    def _wire_hover(widget: tk.Widget, normal_bg: str, hover_bg: str) -> None:
        widget.bind("<Enter>", lambda _: widget.config(bg=hover_bg))
        widget.bind("<Leave>", lambda _: widget.config(bg=normal_bg))
