"""
vision/camera.py - Captura de webcam para Jarvis.

Gemelo de vision/screen.py pero para la camara frontal (cv2). Encode JPEG
(la webcam comprime mucho mejor que PNG y el path de video realtime de
Gemini Live espera image/jpeg). El motor es inyectable para tests
(device_factory) y nunca toca tkinter.
"""

from __future__ import annotations

import io
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

try:  # cv2 es pesado; en algunos entornos de test se inyecta device_factory
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore


def _default_device_factory(index: int) -> Any:
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("opencv-python (cv2) no esta instalado")
    # CAP_DSHOW = backend DirectShow, el correcto en Windows.
    return cv2.VideoCapture(index, cv2.CAP_DSHOW)


@dataclass
class CameraFrame:
    path: Path
    width: int
    height: int
    jpeg_bytes: bytes
    mime_type: str = "image/jpeg"

    def as_dict(self) -> dict:
        return {
            "captured": True,
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
        }


class CameraCapture:
    def __init__(
        self,
        out_dir: Path,
        index: int | None = None,
        max_side: int = 1280,
        retention_hours: float | None = None,
        fps: float | None = None,
        warmup_frames: int = 3,
        device_factory: Callable[[int], Any] = _default_device_factory,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.index = int(os.environ.get("JARVIS_CAMERA_INDEX", "0")) if index is None else int(index)
        self.max_side = int(os.environ.get("JARVIS_CAMERA_MAX_SIDE", str(max_side)))
        if retention_hours is None:
            retention_hours = float(os.environ.get("JARVIS_CAMERA_RETENTION_HOURS", "24"))
        self.retention_hours = max(0.0, float(retention_hours))
        self.fps = float(os.environ.get("JARVIS_CAMERA_FPS", "1.0")) if fps is None else float(fps)
        self.warmup_frames = max(0, int(warmup_frames))
        self._device_factory = device_factory
        self._device: Any | None = None
        self._lock = threading.Lock()
        self._last: CameraFrame | None = None
        self.cleanup_old()

    @property
    def last(self) -> CameraFrame | None:
        return self._last

    # ---- On-demand: open -> warmup -> grab -> close ----

    def capture(self) -> CameraFrame:
        with self._lock:
            dev = self._device_factory(self.index)
            try:
                if not dev.isOpened():
                    raise RuntimeError(
                        "No pude abrir la camara. Verifica que no este en uso por "
                        "otra app y revisa Configuracion -> Privacidad -> Camara."
                    )
                self._warmup(dev)
                ok, frame_bgr = dev.read()
                if not ok or frame_bgr is None:
                    raise RuntimeError("La camara no devolvio frame.")
                return self._finalize(frame_bgr, prefix="cam")
            finally:
                try:
                    dev.release()
                except Exception:
                    pass

    # ---- Watch: open once / read N / close ----

    def open(self) -> None:
        # Path de arranque del modo vision: lo llama UN solo thread (el controller)
        # una vez. El warmup corre bajo el lock; aceptable porque es startup y no
        # compite con read_frame/close hasta que open() retorna.
        with self._lock:
            if self._device is not None:
                return
            dev = self._device_factory(self.index)
            if not dev.isOpened():
                try:
                    dev.release()
                except Exception:
                    pass
                raise RuntimeError("No pude abrir la camara para modo vision.")
            self._warmup(dev)
            self._device = dev

    def read_frame(self) -> CameraFrame:
        with self._lock:
            if self._device is None:
                raise RuntimeError("Camara no abierta (llama open() primero).")
            ok, frame_bgr = self._device.read()
            if not ok or frame_bgr is None:
                raise RuntimeError("La camara no devolvio frame.")
            return self._finalize(frame_bgr, prefix="watch")

    def close(self) -> None:
        with self._lock:
            if self._device is not None:
                try:
                    self._device.release()
                except Exception:
                    pass
                self._device = None

    # ---- internals ----

    def _warmup(self, dev: Any) -> None:
        for _ in range(self.warmup_frames):
            dev.read()

    def _finalize(self, frame_bgr: np.ndarray, prefix: str) -> CameraFrame:
        # Siempre invocado bajo self._lock (desde capture/read_frame), por eso
        # la escritura de self._last aqui es segura. La property `last` lo lee sin
        # lock: bajo el GIL la asignacion de atributo es atomica (no crash).
        self.cleanup_old()
        # cv2 entrega BGR; PIL espera RGB.
        frame_rgb = frame_bgr[:, :, ::-1]
        img = Image.fromarray(np.ascontiguousarray(frame_rgb)).convert("RGB")
        img.thumbnail((self.max_side, self.max_side), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        jpeg_bytes = buf.getvalue()

        ts = time.strftime("%Y%m%d-%H%M%S")
        path = self.out_dir / f"{prefix}-{ts}-{int(time.time()*1000) % 1000:03d}.jpg"
        path.write_bytes(jpeg_bytes)

        frame = CameraFrame(path=path, width=img.width, height=img.height, jpeg_bytes=jpeg_bytes)
        self._last = frame
        return frame

    def cleanup_old(self) -> int:
        cutoff = time.time() - (self.retention_hours * 3600)
        removed = 0
        for path in self.out_dir.glob("*.jpg"):
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


class CameraWatchController:
    """Modo vision continuo acotado. Abre la camara, streamea frames a `fps`
    por session.send_video_frame() y se apaga solo por timeout/stop/budget.

    Threading: el loop corre en un hilo daemon propio. Nunca toca tkinter:
    notifica al exterior via callbacks (on_state, on_frame) que el consumidor
    (jarvis.py) marshalla con _tk().
    """

    def __init__(
        self,
        camera: "CameraCapture",
        session: Any,
        on_state: Callable[[bool], None] = lambda _a: None,
        on_frame: Callable[[Any], None] = lambda _f: None,
        gate_check: Callable[[], bool] = lambda: True,
        on_log: Callable[[str], None] = lambda _m: None,
        default_s: float | None = None,
        max_s: float | None = None,
    ) -> None:
        self.camera = camera
        self.session = session
        self.on_state = on_state
        self.on_frame = on_frame
        self.gate_check = gate_check
        self.on_log = on_log
        self.default_s = float(os.environ.get("JARVIS_CAMERA_WATCH_DEFAULT_S", "90")) if default_s is None else float(default_s)
        self.max_s = float(os.environ.get("JARVIS_CAMERA_WATCH_MAX_S", "180")) if max_s is None else float(max_s)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = False
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        return self._active

    def start(self, duration_s: float | None = None) -> dict:
        with self._lock:
            if self._active:
                return {"ok": True, "active": True, "note": "modo vision ya activo"}
            if not self.gate_check():
                return {"ok": False, "active": False, "error": "presupuesto Gemini agotado"}
            dur = self.default_s if not duration_s else float(duration_s)
            dur = max(0.1, min(dur, self.max_s))
            try:
                self.camera.open()
            except Exception as exc:
                return {"ok": False, "active": False, "error": f"{type(exc).__name__}: {exc}"}
            self._stop.clear()
            self._active = True
            self._thread = threading.Thread(
                target=self._loop, args=(dur,), name="JarvisCameraWatch", daemon=True
            )
            self._thread.start()
        self.on_state(True)
        self.on_log(f"[WATCH] modo vision ON ({dur:.0f}s)")
        return {"ok": True, "active": True, "duration_s": dur}

    def stop(self) -> dict:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=3.0)
        self._teardown()
        return {"ok": True, "active": False}

    def _loop(self, duration_s: float) -> None:
        period = 1.0 / max(0.1, self.camera.fps)
        deadline = time.time() + duration_s
        try:
            while not self._stop.is_set() and time.time() < deadline:
                if not self.gate_check():
                    self.on_log("[WATCH] stop: presupuesto agotado")
                    break
                try:
                    frame = self.camera.read_frame()
                    self.session.send_video_frame(frame.jpeg_bytes)
                    self.on_frame(frame)
                except Exception as exc:
                    self.on_log(f"[WATCH] frame error: {type(exc).__name__}: {exc}")
                self._stop.wait(period)
        finally:
            self._teardown()

    def _teardown(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._thread = None
        try:
            self.camera.close()
        except Exception:
            pass
        self.on_state(False)
        self.on_log("[WATCH] modo vision OFF")
