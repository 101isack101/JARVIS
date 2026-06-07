import asyncio
import time
from pathlib import Path

from gemini.session import JarvisSession, SessionConfig, SessionCallbacks
from vision.camera import CameraCapture, CameraWatchController
from tests.test_camera import _factory


class FakeLiveSession:
    def __init__(self):
        self.video_blobs = []

    async def send_realtime_input(self, **kwargs):
        if "video" in kwargs:
            self.video_blobs.append(kwargs["video"])


def test_async_send_video_builds_jpeg_blob():
    cfg = SessionConfig(api_key="x")
    sess = JarvisSession(cfg, SessionCallbacks())
    fake = FakeLiveSession()
    sess._session = fake
    asyncio.run(sess._async_send_video(b"\xff\xd8\xff_fake_jpeg"))
    assert len(fake.video_blobs) == 1
    blob = fake.video_blobs[0]
    assert blob.mime_type == "image/jpeg"
    assert blob.data == b"\xff\xd8\xff_fake_jpeg"


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


def test_camera_watch_tool_start_stop(tmp_path):
    from memory.tools import ToolContext, camera_watch

    ctrl, cam, sess, events = _make_controller(tmp_path)
    ctx = ToolContext(vault=None, rag=None, camera_watch=ctrl)
    out = camera_watch(ctx, action="start", duration_s=10)
    assert out["ok"] is True
    assert out["active"] is True
    out2 = camera_watch(ctx, action="stop")
    assert out2["ok"] is True
    assert out2["active"] is False
    assert ctrl.is_active() is False


def test_camera_watch_tool_without_controller():
    from memory.tools import ToolContext, camera_watch

    ctx = ToolContext(vault=None, rag=None, camera_watch=None)
    out = camera_watch(ctx, action="start")
    assert out["ok"] is False
    assert out["active"] is False
    assert "error" in out
