"""Human approval dialog for risky Jarvis actions."""

from __future__ import annotations

import time
import tkinter as tk
from typing import Callable

from overlay.ui_theme import (
    ACCENT,
    ACCENT_DIM,
    BG,
    BORDER,
    BORDER_SOFT,
    CONTROL,
    CONTROL_HOVER,
    DANGER,
    DANGER_BG,
    FONT_DISPLAY,
    FONT_MONO,
    FONT_UI,
    SURFACE,
    SURFACE_ALT,
    TEXT_DIM,
    TEXT_FAINT,
    TEXT_PRIMARY,
    WARN,
    WARN_BG,
)

class ApprovalDialog:
    """In-overlay HITL approval panel.

    This intentionally avoids creating a native Toplevel. On Windows, transient
    Tk toplevels under load from audio/tool callbacks can become orphaned black
    windows. A child frame inside the main overlay cannot outlive its parent.
    """

    def __init__(
        self,
        root: tk.Misc,
        action,
        on_decision: Callable[[str, bool], None],
        log_event: Callable[[str, str], None],
    ) -> None:
        self.root = root
        self.action = action
        self.on_decision = on_decision
        self.log_event = log_event
        self.resolved = False
        self.window: tk.Widget | None = None
        self._timeout_after_id: str | None = None
        self._tick_after_id: str | None = None
        self._on_close: Callable[["ApprovalDialog"], None] | None = None

    def set_on_close(self, callback: Callable[["ApprovalDialog"], None]) -> None:
        self._on_close = callback

    def show(self) -> None:
        try:
            self.log_event(f"Aprobacion pendiente: {self.action.title}", "warn")
        except Exception:
            pass
        win = tk.Frame(
            self.root,
            bg=BORDER,
            highlightthickness=1,
            highlightbackground=BORDER,
            takefocus=True,
        )
        self.window = win
        win.place(relx=0.5, rely=0.52, anchor="center", width=640, height=382)

        shell = tk.Frame(win, bg=BG)
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(
            header,
            text="JARVIS APPROVAL",
            bg=BG,
            fg=TEXT_FAINT,
            font=(FONT_DISPLAY, 8, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Button(
            header,
            text="X",
            command=lambda: self.decide(False),
            bg=BG,
            fg=TEXT_FAINT,
            activebackground=CONTROL,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            padx=8,
            pady=2,
        ).pack(side="right")

        content = tk.Frame(shell, bg=BG)
        content.pack(fill="both", expand=True, padx=18, pady=(10, 18))

        hero_bg = DANGER_BG if self.action.risk == "destructive" else SURFACE
        hero = tk.Frame(content, bg=hero_bg, highlightthickness=1, highlightbackground=BORDER_SOFT)
        hero.pack(fill="x", pady=(0, 14))

        tk.Label(
            hero,
            text="SECURITY GATE",
            bg=hero_bg,
            fg=TEXT_FAINT,
            font=(FONT_DISPLAY, 8, "bold"),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(10, 0))

        tk.Label(
            hero,
            text=self.action.title,
            bg=hero_bg,
            fg=WARN if self.action.risk != "destructive" else DANGER,
            font=(FONT_DISPLAY, 16, "bold"),
            anchor="w",
            justify="left",
            wraplength=588,
        ).pack(fill="x", padx=12, pady=(3, 11))

        meta = tk.Frame(content, bg=BG)
        meta.pack(fill="x", pady=(0, 10))

        risk_label = self._make_chip(
            meta,
            f"Riesgo: {self.action.risk}",
            fg=DANGER if self.action.risk == "destructive" else WARN,
            bg=DANGER_BG if self.action.risk == "destructive" else WARN_BG,
        )
        risk_label.pack(side="left")

        countdown_label = tk.Label(
            meta,
            text="",
            bg=BG,
            fg=TEXT_FAINT,
            font=(FONT_UI, 8),
            anchor="e",
        )
        countdown_label.pack(side="right")

        details = tk.Text(
            content,
            height=9,
            bg=SURFACE_ALT,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            font=(FONT_MONO, 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            highlightcolor=BORDER,
            wrap="word",
        )
        details.pack(fill="both", expand=True)
        details.insert("1.0", self.action.details)
        details.config(state="disabled")

        btns = tk.Frame(content, bg=BG)
        btns.pack(fill="x", pady=(12, 0))

        tk.Button(
            btns,
            text="Rechazar",
            command=lambda: self.decide(False),
            bg=CONTROL,
            fg=TEXT_PRIMARY,
            activebackground=CONTROL_HOVER,
            activeforeground=TEXT_PRIMARY,
            relief="flat",
            padx=20,
            pady=9,
        ).pack(side="right", padx=(8, 0))

        tk.Button(
            btns,
            text="Aprobar accion",
            command=lambda: self.decide(True),
            bg=ACCENT_DIM,
            fg="#06110c",
            activebackground=ACCENT,
            activeforeground="#06110c",
            relief="flat",
            padx=20,
            pady=9,
        ).pack(side="right")

        win.bind("<Escape>", lambda _: self.decide(False))
        self._timeout_after_id = win.after(
            int(self.action.timeout_s * 1000),
            lambda: self.decide(False),
        )
        self._tick(countdown_label)
        try:
            win.lift()
            win.focus_set()
        except Exception:
            pass

    def decide(self, approved: bool) -> None:
        if self.resolved:
            return
        self.resolved = True
        self._cancel_timers()
        try:
            self._destroy_window()
        finally:
            self._notify_closed()
        try:
            self.log_event(
                "Accion aprobada" if approved else "Accion rechazada",
                "ok" if approved else "warn",
            )
        except Exception:
            pass
        try:
            self.on_decision(self.action.id, approved)
        except Exception:
            pass

    def _destroy_window(self) -> None:
        if self.window is None:
            return
        try:
            self.window.place_forget()
        except Exception:
            pass
        try:
            self.window.destroy()
        except Exception:
            pass
        self.window = None

    def _notify_closed(self) -> None:
        if self._on_close is None:
            return
        try:
            self._on_close(self)
        except Exception:
            pass

    def _tick(self, label: tk.Label) -> None:
        if self.resolved:
            return
        remaining = max(0, int(self.action.timeout_s - (time.time() - self.action.created_at)))
        label.config(text=f"Auto-rechazo en {remaining}s")
        if remaining <= 5:
            label.config(fg=DANGER)
        if remaining > 0:
            self._tick_after_id = label.after(1000, lambda: self._tick(label))

    def _cancel_timers(self) -> None:
        if self.window is None:
            return
        for after_id in (self._timeout_after_id, self._tick_after_id):
            if after_id is None:
                continue
            try:
                self.window.after_cancel(after_id)
            except Exception:
                pass
        self._timeout_after_id = None
        self._tick_after_id = None

    @staticmethod
    def _make_chip(parent: tk.Misc, text: str, fg: str = TEXT_DIM, bg: str = CONTROL) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=bg,
            fg=fg,
            font=(FONT_DISPLAY, 8, "bold"),
            padx=12,
            pady=5,
        )
