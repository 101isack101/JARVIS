import time
from pathlib import Path

from vision.camera import CameraCapture, CameraWatchController
from tests.test_camera import _factory


class FakeSession:
    def __init__(self):
        self.frames = []

    def send_video_frame(self, jpeg):
        self.frames.append(jpeg)


def _make_controller(tmp_path, gate_ok=True, **kw):
    cam = CameraCapture(out_dir=tmp_path, index=0, fps=50.0, device_factory=_factory())
    sess = FakeSession()
    events = []
    ctrl = CameraWatchController(
        camera=cam,
        session=sess,
        on_state=lambda active: events.append(active),
        on_frame=lambda f: None,
        gate_check=lambda: gate_ok,
        **kw,
    )
    return ctrl, cam, sess, events


def test_watch_streams_frames_then_autostops(tmp_path):
    ctrl, cam, sess, events = _make_controller(tmp_path)
    res = ctrl.start(duration_s=0.2)
    assert res["ok"] is True and res["active"] is True
    time.sleep(0.5)
    assert ctrl.is_active() is False         # auto-stop por timeout
    assert len(sess.frames) >= 1             # streameo al menos un frame
    assert events[0] is True and events[-1] is False  # overlay ON luego OFF


def test_watch_explicit_stop_releases_camera(tmp_path):
    ctrl, cam, sess, events = _make_controller(tmp_path)
    ctrl.start(duration_s=10)
    time.sleep(0.1)
    out = ctrl.stop()
    assert out["ok"] is True
    assert ctrl.is_active() is False


def test_watch_blocked_by_budget(tmp_path):
    ctrl, cam, sess, events = _make_controller(tmp_path, gate_ok=False)
    res = ctrl.start(duration_s=1)
    assert res["ok"] is False
    assert ctrl.is_active() is False
    assert sess.frames == []
