"""Small reusable Tkinter widgets for the Jarvis overlay UI."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk

from overlay.ui_theme import BORDER, FONT_UI, PANEL, TEXT_PRIMARY, WINDOW_RADIUS


def apply_window_rounding(window: tk.Tk | tk.Toplevel, radius: int = WINDOW_RADIUS) -> None:
    """Clip a borderless Tk window to rounded corners on Windows.

    Keep this callback non-reentrant. Calling ``update_idletasks()`` from an
    ``after``/``<Configure>`` path can make Tk run more pending callbacks while
    it is already handling geometry updates, which was enough to recurse until
    JARVIS crashed when the overlay was minimized during long OBS sessions.
    """
    if sys.platform != "win32":
        return

    after_id: str | None = None
    dwm_preference_applied = False

    def apply() -> None:
        nonlocal after_id, dwm_preference_applied
        after_id = None
        try:
            if not bool(window.winfo_exists()):
                return
            width = max(1, int(window.winfo_width()))
            height = max(1, int(window.winfo_height()))
            if width <= 1 or height <= 1:
                schedule()
                return
            hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
            if not hwnd:
                hwnd = window.winfo_id()
            region = ctypes.windll.gdi32.CreateRoundRectRgn(
                0,
                0,
                width + 1,
                height + 1,
                radius,
                radius,
            )
            applied = ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
            if not applied:
                try:
                    ctypes.windll.gdi32.DeleteObject(region)
                except Exception:
                    pass

            # Windows 11 also honors DWM rounded-corner preference for framed
            # toplevels. The region above is the reliable fallback.
            if not dwm_preference_applied:
                preference = ctypes.c_int(2)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd,
                    33,
                    ctypes.byref(preference),
                    ctypes.sizeof(preference),
                )
                dwm_preference_applied = True
        except Exception:
            pass

    def schedule(_event=None) -> None:
        nonlocal after_id
        if after_id is not None:
            return
        try:
            after_id = window.after(80, apply)
        except tk.TclError:
            pass

    def cancel(_event=None) -> None:
        nonlocal after_id
        if after_id is None:
            return
        try:
            window.after_cancel(after_id)
        except Exception:
            pass
        after_id = None

    schedule()
    window.bind("<Configure>", schedule, add="+")
    window.bind("<Destroy>", cancel, add="+")


class Tooltip:
    """Delayed hover tooltip for compact icon-like controls."""

    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._window is not None:
            return
        try:
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except tk.TclError:
            return
        win = tk.Toplevel(self.widget)
        self._window = win
        win.wm_overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BORDER)
        win.geometry(f"+{x}+{y}")
        tk.Label(
            win,
            text=self.text,
            bg=PANEL,
            fg=TEXT_PRIMARY,
            font=(FONT_UI, 8),
            padx=8,
            pady=4,
        ).pack(padx=1, pady=1)
        apply_window_rounding(win, radius=12)

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None


def attach_tooltip(widget: tk.Widget, text: str) -> None:
    Tooltip(widget, text)
