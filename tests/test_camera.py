import sys
from pathlib import Path
import struct

import pytest

from vision.camera import CameraCapture, CameraFrame
from vision.native_capture import native_vision_status


class FakeDevice:
    """Simula cv2.VideoCapture: devuelve frames RGB sinteticos."""

    def __init__(self, frames=None, opened=True):
        # frame BGR 8x8 (lo que entrega cv2). numpy opcional: usamos lista->np en read.
        import numpy as np
        self._frames = frames or [np.full((8, 8, 3), 127, dtype=np.uint8)]
        self._i = 0
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def read(self):
        import numpy as np
        if not self._opened:
            return False, None
        frame = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return True, frame

    def release(self):
        self.released = True


def _factory(opened=True):
    return lambda index: FakeDevice(opened=opened)


def test_auto_index_tries_until_a_camera_opens(tmp_path, monkeypatch):
    calls = []

    def factory(index):
        calls.append(index)
        return FakeDevice(opened=index == 2)

    monkeypatch.setenv("JARVIS_CAMERA_INDEX", "auto")
    monkeypatch.setenv("JARVIS_CAMERA_SCAN_MAX", "4")
    cam = CameraCapture(out_dir=tmp_path, device_factory=factory)

    frame = cam.capture()

    assert isinstance(frame, CameraFrame)
    assert calls == [0, 1, 2]
    assert cam.index == 2


def test_capture_returns_valid_frame(tmp_path):
    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory())
    frame = cam.capture()
    assert isinstance(frame, CameraFrame)
    assert frame.mime_type == "image/jpeg"
    assert frame.jpeg_bytes[:3] == b"\xff\xd8\xff"  # cabecera JPEG
    assert frame.path.exists()
    assert frame.width > 0 and frame.height > 0


def test_capture_device_not_opened_raises(tmp_path):
    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory(opened=False))
    with pytest.raises(RuntimeError) as err:
        cam.capture()
    assert "Intentos OpenCV" in str(err.value)
    assert "JARVIS_CAMERA_INDEX=auto" in str(err.value)


def test_capture_releases_device_in_ondemand(tmp_path):
    holder = {}
    def factory(index):
        dev = FakeDevice()
        holder["dev"] = dev
        return dev
    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=factory)
    cam.capture()
    assert holder["dev"].released is True  # open->grab->close


def test_camera_look_returns_attach_image(tmp_path):
    from memory.tools import ToolContext, camera_look

    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory())
    ctx = ToolContext(vault=None, rag=None, camera=cam)
    out = camera_look(ctx, reason="mira esto")
    assert out["captured"] is True
    assert out["reason"] == "mira esto"
    attach = out["__attach_image"]
    assert attach["mime_type"] == "image/jpeg"
    assert attach["source"] == "camera"
    assert attach["png_bytes"][:3] == b"\xff\xd8\xff"  # reutiliza la clave png_bytes


def test_camera_look_without_camera_reports_error():
    from memory.tools import ToolContext, camera_look

    ctx = ToolContext(vault=None, rag=None, camera=None)
    out = camera_look(ctx, reason="x")
    assert out["captured"] is False
    assert "error" in out


def test_camera_look_during_watch_reports_clear_error(tmp_path):
    from memory.tools import ToolContext, camera_look

    class ActiveWatch:
        def is_active(self):
            return True

    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory())
    ctx = ToolContext(vault=None, rag=None, camera=cam, camera_watch=ActiveWatch())
    out = camera_look(ctx, reason="mira esto")
    assert out["captured"] is False
    assert out["active"] is True
    assert "Modo vision activo" in out["error"]


def test_native_vision_status_no_crashea():
    status = native_vision_status()
    assert isinstance(status.available, bool)
    assert status.path


def test_native_vision_required_falla_claro_si_no_hay_exe(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_VISION_NATIVE", "required")
    monkeypatch.setenv("JARVIS_VISION_NATIVE_EXE", str(tmp_path / "missing.exe"))
    monkeypatch.setenv("JARVIS_CAMERA_INDEX", "0")

    cam = CameraCapture(out_dir=tmp_path)

    with pytest.raises(RuntimeError) as err:
        cam.capture()
    assert "Vision nativa requerida" in str(err.value)


def test_native_vision_protocol_reader():
    import io
    from vision import native_capture

    jpeg = b"\xff\xd8\xff_fake_jpeg"
    payload = struct.pack("<4sIIIQ", b"JVF1", 320, 240, len(jpeg), 123) + jpeg
    frame = native_capture._read_frame(io.BytesIO(payload), timeout_s=1.0)

    assert frame.width == 320
    assert frame.height == 240
    assert frame.jpeg_bytes == jpeg
    assert frame.timestamp_ns == 123
