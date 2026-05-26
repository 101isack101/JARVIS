"""
vision/screen.py - Captura de pantalla para Jarvis.

Captura un screenshot, lo reduce si hace falta y lo guarda en data/screenshots.
La tool puede devolver tambien bytes PNG como FunctionResponsePart para Gemini 3.
"""

from __future__ import annotations

import io
import os
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageGrab


@dataclass
class Screenshot:
    path: Path
    width: int
    height: int
    png_bytes: bytes
    mime_type: str = "image/png"

    def as_dict(self) -> dict:
        return {
            "captured": True,
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
        }


class ScreenCapture:
    def __init__(
        self,
        out_dir: Path,
        max_side: int = 1280,
        retention_hours: float | None = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.max_side = max_side
        if retention_hours is None:
            retention_hours = float(os.environ.get("JARVIS_SCREENSHOT_RETENTION_HOURS", "24"))
        self.retention_hours = max(0.0, float(retention_hours))
        self._last: Screenshot | None = None
        self.cleanup_old()

    @property
    def last(self) -> Screenshot | None:
        return self._last

    def capture(self) -> Screenshot:
        img = ImageGrab.grab(all_screens=True)
        return self._finalize(img, prefix="screen")

    def capture_region(self, bbox: tuple[int, int, int, int]) -> Screenshot:
        """Captura solo la region (left, top, right, bottom) de la pantalla virtual.

        bbox usa coordenadas absolutas del virtual screen (compatibles con
        las que devuelve RegionSelector). Mucho mas barato en tokens que
        capturar toda la pantalla cuando Isaac solo quiere mostrar un trozo
        especifico (una imagen, un mensaje, un grafico, un error, etc).
        """
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        return self._finalize(img, prefix="region")

    def _finalize(self, img: Image.Image, prefix: str) -> Screenshot:
        """Pipeline comun: convert RGB, thumbnail, save PNG, devolver Screenshot."""
        self.cleanup_old()
        img = img.convert("RGB")
        img.thumbnail((self.max_side, self.max_side), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png_bytes = buf.getvalue()

        ts = time.strftime("%Y%m%d-%H%M%S")
        path = self.out_dir / f"{prefix}-{ts}.png"
        path.write_bytes(png_bytes)

        shot = Screenshot(
            path=path,
            width=img.width,
            height=img.height,
            png_bytes=png_bytes,
        )
        self._last = shot
        return shot

    def cleanup_old(self) -> int:
        """Borra screenshots antiguos segun JARVIS_SCREENSHOT_RETENTION_HOURS.

        Si retention_hours es 0, borra todos los PNG previos antes de guardar el
        nuevo screenshot. Valores negativos quedan normalizados a 0 en __init__.
        """
        cutoff = time.time() - (self.retention_hours * 3600)
        removed = 0
        for path in self.out_dir.glob("*.png"):
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
