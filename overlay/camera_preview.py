"""
overlay/camera_preview.py - Ventana de preview de lo que ve JARVIS por la camara.

DEBE crearse y actualizarse SOLO en el main thread (tkinter). El consumidor
(jarvis.py) ya marshalla via _tk(), asi que los metodos publicos asumen main thread.
Pinta cada CameraFrame (JPEG) y dibuja el crosshair (reticula central + box semantico).

El box semantico se recibe en coordenadas NORMALIZADAS 0..1000 (convencion de
Gemini, box_2d=[ymin,xmin,ymax,xmax]) y se convierte a pixeles usando la
geometria real de la imagen letterboxed (ox/oy/w/h) que guarda update_frame.
Asi la mira queda alineada con cualquier aspect ratio (4:3, 16:9, etc.).
"""

from __future__ import annotations

import io
import os
import tkinter as tk

from PIL import Image, ImageTk

from vision.detect import box_to_pixels


class CameraPreviewWindow:
    def __init__(self, parent: tk.Misc) -> None:
        self._parent = parent
        self._top: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._size = int(os.environ.get("JARVIS_CAMERA_PREVIEW_SIZE", "480"))
        self._enabled = os.environ.get("JARVIS_CAMERA_PREVIEW", "1") == "1"
        # Box en coords NORMALIZADAS 0..1000 ([ymin,xmin,ymax,xmax]) o None.
        self._box_norm: list[int] | None = None
        self._box_label: str = ""
        # Geometria de la ultima imagen pintada: (ox, oy, w, h) en px del canvas.
        self._img_rect: tuple[int, int, int, int] | None = None

    def show(self) -> None:
        if not self._enabled or self._top is not None:
            return
        self._top = tk.Toplevel(self._parent)
        self._top.title("JARVIS - Camara")
        self._top.attributes("-topmost", True)
        self._top.protocol("WM_DELETE_WINDOW", self.hide)
        self._canvas = tk.Canvas(
            self._top, width=self._size, height=self._size,
            bg="black", highlightthickness=0,
        )
        self._canvas.pack()

    def hide(self) -> None:
        self._box_norm = None
        self._img_rect = None
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
        self._top = None
        self._canvas = None
        self._photo = None

    def update_frame(self, jpeg_bytes: bytes) -> None:
        if not self._enabled or self._canvas is None:
            return
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        img.thumbnail((self._size, self._size), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        ox = (self._size - img.width) // 2
        oy = (self._size - img.height) // 2
        self._img_rect = (ox, oy, img.width, img.height)
        self._canvas.create_image(ox, oy, anchor="nw", image=self._photo)
        self._draw_crosshair(img.width, img.height, ox, oy)

    def set_focus_box(self, box_norm: list[int] | None, label: str = "") -> None:
        """box_norm = [ymin,xmin,ymax,xmax] en 0..1000 (coords de Gemini), o None."""
        self._box_norm = list(box_norm) if box_norm is not None else None
        self._box_label = label

    def _draw_crosshair(self, w: int, h: int, ox: int, oy: int) -> None:
        c = self._canvas
        if c is None:
            return
        if self._box_norm is not None:
            # Convertir normalizado -> px usando la geometria REAL de la imagen.
            x1, y1, x2, y2 = box_to_pixels(self._box_norm, width=w, height=h, ox=ox, oy=oy)
            c.create_rectangle(x1, y1, x2, y2, outline="#39FF14", width=2)
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            c.create_line(cx - 10, cy, cx + 10, cy, fill="#39FF14", width=2)
            c.create_line(cx, cy - 10, cx, cy + 10, fill="#39FF14", width=2)
            if self._box_label:
                c.create_text(x1 + 2, y1 - 8, anchor="w", fill="#39FF14",
                              text=self._box_label, font=("Segoe UI", 9, "bold"))
        else:
            # Reticula central tenue
            cx, cy = ox + w // 2, oy + h // 2
            c.create_line(cx - 12, cy, cx + 12, cy, fill="#39FF14", width=1)
            c.create_line(cx, cy - 12, cx, cy + 12, fill="#39FF14", width=1)
