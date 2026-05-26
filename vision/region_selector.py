"""
vision/region_selector.py - Snipping overlay para seleccion de region.

Muestra un overlay translucido sobre toda la pantalla virtual y permite
a Isaac arrastrar el mouse para definir un rectangulo. Solo esa region
se captura y se envia a Gemini, ahorrando tokens vs full-screen.

Threading:
  - show() debe correr en main thread tkinter (crea Toplevel + grab_set).
  - El callback on_select se invoca tambien en main thread.
  - Hotkey thread debe schedular show() via root.after(0, ...).
"""

from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable

if sys.platform == "win32":
    import ctypes


# Win32 GetSystemMetrics indices para virtual screen (multi-monitor)
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def _virtual_screen_bbox(fallback_root: tk.Misc) -> tuple[int, int, int, int]:
    """Devuelve (x, y, width, height) del virtual screen (todos los monitores).

    En non-Windows o si Win32 falla, usa el monitor primario via tkinter.
    """
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            return (
                user32.GetSystemMetrics(_SM_XVIRTUALSCREEN),
                user32.GetSystemMetrics(_SM_YVIRTUALSCREEN),
                user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN),
                user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN),
            )
        except Exception:
            pass
    return (0, 0, fallback_root.winfo_screenwidth(), fallback_root.winfo_screenheight())


class RegionSelector:
    """Overlay translucido que captura un rectangulo dibujado con el mouse.

    Uso (desde main thread tkinter):
        sel = RegionSelector(parent_root, on_select=callback)
        sel.show()
        # callback recibe bbox (left, top, right, bottom) en coords
        # absolutas de pantalla virtual, o None si Isaac presiono Esc
        # o el rectangulo fue demasiado pequeno (probable click accidental).
    """

    MIN_SIZE_PX = 8     # rect mas pequeno que esto = cancel
    HIDE_DELAY_MS = 80  # espera tras destroy para que Win32 remueva la ventana

    def __init__(
        self,
        parent: tk.Misc,
        on_select: Callable[[tuple[int, int, int, int] | None], None],
    ) -> None:
        self.parent = parent
        self.on_select = on_select
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._start_xy: tuple[int, int] | None = None
        self._rect_id: int | None = None
        self._origin: tuple[int, int] = (0, 0)

    def show(self) -> None:
        if self._win is not None:
            return  # ya visible, ignora
        x, y, w, h = _virtual_screen_bbox(self.parent)
        self._origin = (x, y)

        win = tk.Toplevel(self.parent)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.3)
        win.configure(bg="black")
        win.geometry(f"{w}x{h}+{x}+{y}")

        canvas = tk.Canvas(
            win, bg="black", highlightthickness=0, cursor="crosshair",
        )
        canvas.pack(fill="both", expand=True)

        canvas.create_text(
            w // 2, 30,
            text="Arrastra para seleccionar la region  ·  Esc para cancelar",
            fill="#3ecf8e", font=("Segoe UI", 14, "bold"),
        )

        canvas.bind("<Button-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        win.bind("<Escape>", lambda _: self._finish(None))

        win.focus_force()
        try:
            win.grab_set()
        except tk.TclError:
            pass  # grab puede fallar si otra ventana ya tiene grab

        self._win = win
        self._canvas = canvas

    def _on_press(self, e: tk.Event) -> None:
        self._start_xy = (e.x, e.y)
        if self._canvas is not None:
            self._rect_id = self._canvas.create_rectangle(
                e.x, e.y, e.x, e.y,
                outline="#3ecf8e", width=2,
            )

    def _on_drag(self, e: tk.Event) -> None:
        if self._rect_id is None or self._start_xy is None or self._canvas is None:
            return
        sx, sy = self._start_xy
        self._canvas.coords(self._rect_id, sx, sy, e.x, e.y)

    def _on_release(self, e: tk.Event) -> None:
        if self._start_xy is None:
            self._finish(None)
            return
        sx, sy = self._start_xy
        x1, y1 = min(sx, e.x), min(sy, e.y)
        x2, y2 = max(sx, e.x), max(sy, e.y)
        if (x2 - x1) < self.MIN_SIZE_PX or (y2 - y1) < self.MIN_SIZE_PX:
            self._finish(None)
            return
        ox, oy = self._origin
        bbox = (x1 + ox, y1 + oy, x2 + ox, y2 + oy)
        self._finish(bbox)

    def _finish(self, bbox: tuple[int, int, int, int] | None) -> None:
        win = self._win
        self._win = None
        self._canvas = None
        self._start_xy = None
        self._rect_id = None
        if win is not None:
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.withdraw()
                win.update_idletasks()
            except Exception:
                pass
            # Schedule destroy + callback con pequeno delay para que
            # Win32 ya removio la ventana y la captura no la incluye
            self.parent.after(self.HIDE_DELAY_MS, lambda: self._do_callback(bbox, win))
        else:
            self._do_callback(bbox, None)

    def _do_callback(self, bbox, win) -> None:
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        try:
            self.on_select(bbox)
        except Exception as exc:
            print(f"[region_selector] on_select callback error: {exc}")


# Smoke test: muestra el selector y printea el bbox elegido
if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    root = tk.Tk()
    root.geometry("300x100+50+50")
    root.title("RegionSelector smoke test")

    result = {"bbox": "pending"}

    def on_select(bbox):
        result["bbox"] = bbox
        print(f"[smoke] bbox seleccionado: {bbox}")
        root.after(500, root.destroy)

    def trigger():
        print("[smoke] mostrando selector en 1s. Arrastra un rect o Esc.")
        root.after(1000, lambda: RegionSelector(root, on_select).show())

    btn = tk.Button(root, text="Probar selector", command=trigger)
    btn.pack(expand=True, fill="both", padx=20, pady=20)

    root.mainloop()
    print(f"[OK] smoke test result: {result['bbox']}")
