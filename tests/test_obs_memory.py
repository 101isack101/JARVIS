from pathlib import Path
import time

from memory.obsidian_vault import ObsidianVault
from memory.rag import VaultRAG
from memory.tools import ToolContext, ToolDispatcher
from obs_memory.config import OBSMemoryConfig
from obs_memory.controller import OBSMemoryController
from security.approvals import AutoApprovalBroker


class FakeArtifacts:
    def __init__(self, work_dir: Path):
        self.wav_path = work_dir / "audio.wav"
        self.wav_path.write_bytes(b"fake wav")
        frames = work_dir / "frames"
        frames.mkdir(parents=True, exist_ok=True)
        frame = frames / "frame_01.png"
        frame.write_bytes(b"png")
        self.frame_paths = [frame]
        self.duration_s = 42
        self.work_dir = work_dir


class FakeTranscriber:
    def transcribe(self, wav_path):
        return "Isaac depura un error, prueba una solucion y decide documentarlo."


class FakeSynthesizer:
    def synthesize(self, **kwargs):
        class Result:
            markdown = (
                "## Resumen\n- Se resolvio un problema durante la sesion.\n\n"
                "## Pendientes\n- Continuar validacion."
            )
            used_reasoner = True

        return Result()


class FakeOBS:
    def __init__(self, output_path: str = ""):
        self.active = False
        self.output_path = output_path

    def status(self):
        class Status:
            active = self.active
            paused = False
            timecode = ""
            output_path = ""

        return Status()

    def start_recording(self):
        self.active = True
        return {"ok": True, "started": True}

    def stop_recording(self):
        self.active = False
        return {"ok": True, "stopped": True, "output_path": self.output_path}


class FakeController:
    def status(self):
        return {"ok": True, "fake": True}

    def start(self, title=None):
        return {"ok": True, "started": True, "title": title}

    def stop(self, title=None, process=True):
        return {"ok": True, "stopped": True, "title": title, "process": process}

    def enqueue_process(self, path, title=None):
        return {"job_id": "fakejob", "status": "queued", "video_path": str(path), "title": title}


def test_obs_memory_process_file_writes_note_and_deletes_video(tmp_path, monkeypatch):
    from obs_memory import controller as controller_mod

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    recording_dir = tmp_path / "recordings"
    recording_dir.mkdir()
    video = recording_dir / "debug_session.mkv"
    video.write_bytes(b"fake video")

    config = OBSMemoryConfig(
        enabled=True,
        recording_dir=recording_dir,
        data_dir=tmp_path / "data",
        output_folder="obs_sessions",
        retention="delete_video_after_success",
        analysis_mode="episodic",
    )
    monkeypatch.setattr(
        controller_mod,
        "extract_artifacts",
        lambda path, work_dir, keyframes: FakeArtifacts(work_dir),
    )
    controller = OBSMemoryController(
        vault=vault,
        config=config,
        obs_client=FakeOBS(),
        transcriber=FakeTranscriber(),
        synthesizer=FakeSynthesizer(),
    )

    result = controller.process_file(video, title="Debug Session")

    assert result["ok"] is True
    assert result["video_deleted"] is True
    assert not video.exists()
    note = vault_root / result["note_path"]
    assert note.exists()
    text = note.read_text(encoding="utf-8")
    assert "Debug Session" in text
    assert "Se resolvio un problema" in text


def test_obs_memory_config_keeps_video_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_OBS_RETENTION", raising=False)

    config = OBSMemoryConfig.from_env(root=Path("."))

    assert config.retention == "keep_video"
    assert config.allow_external_video_paths is False


def test_obs_memory_process_file_blocks_external_video_path(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    recording_dir = tmp_path / "recordings"
    recording_dir.mkdir()
    external = tmp_path / "external.mkv"
    external.write_bytes(b"fake video")
    config = OBSMemoryConfig(
        enabled=True,
        recording_dir=recording_dir,
        data_dir=tmp_path / "data",
        output_folder="obs_sessions",
        retention="keep_video",
        analysis_mode="episodic",
    )
    controller = OBSMemoryController(
        vault=vault,
        config=config,
        obs_client=FakeOBS(),
        transcriber=FakeTranscriber(),
        synthesizer=FakeSynthesizer(),
    )

    result = controller.process_file(external, title="External")

    assert result["ok"] is False
    assert "fuera de JARVIS_OBS_RECORDING_DIR" in result["error"]
    assert external.exists()


def test_obs_memory_tool_requires_approval(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    ctx = ToolContext(vault=vault, rag=rag, obs_memory=FakeController())
    dispatcher = ToolDispatcher(ctx)

    denied = dispatcher.call("obs_memory", {"action": "start", "title": "Test"})

    assert denied["ok"] is False
    assert "HITL" in denied["error"]


def test_obs_memory_tool_dispatches_with_approval(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    approvals = AutoApprovalBroker(approve=True)
    ctx = ToolContext(
        vault=vault,
        rag=rag,
        obs_memory=FakeController(),
        approvals=approvals,
    )
    dispatcher = ToolDispatcher(ctx)

    result = dispatcher.call("obs_memory", {"action": "start", "title": "Sesion OBS"})

    assert "obs_memory" in dispatcher.tool_names
    assert result["ok"] is True
    assert result["started"] is True
    assert approvals.requests[0][0] == "write"


def test_obs_memory_stop_queues_background_processing(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    recording_dir = tmp_path / "recordings"
    recording_dir.mkdir()
    video = recording_dir / "session.mkv"
    video.write_bytes(b"fake video")
    config = OBSMemoryConfig(
        enabled=True,
        recording_dir=recording_dir,
        data_dir=tmp_path / "data",
        output_folder="obs_sessions",
        retention="delete_video_after_success",
        process_background=True,
        analysis_mode="episodic",
    )
    controller = OBSMemoryController(
        vault=vault,
        config=config,
        obs_client=FakeOBS(output_path=str(video)),
        transcriber=FakeTranscriber(),
        synthesizer=FakeSynthesizer(),
    )
    called = []

    def fake_enqueue(path, title=None):
        called.append((Path(path), title))
        return {"job_id": "abc123", "status": "queued", "video_path": str(path)}

    monkeypatch.setattr(controller, "enqueue_process", fake_enqueue)

    result = controller.stop(title="Background Test", process=True)

    assert result["ok"] is True
    assert result["processing"] == "background"
    assert result["job"]["job_id"] == "abc123"
    assert called == [(video.resolve(), "Background Test")]


def test_obs_memory_tool_process_file_queues_background(tmp_path):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    approvals = AutoApprovalBroker(approve=True)
    ctx = ToolContext(
        vault=vault,
        rag=rag,
        obs_memory=FakeController(),
        approvals=approvals,
    )
    dispatcher = ToolDispatcher(ctx)

    result = dispatcher.call(
        "obs_memory",
        {"action": "process_file", "path": "C:/tmp/test.mkv", "title": "Manual"},
    )

    assert result["ok"] is True
    assert result["processing"] == "background"
    assert result["job"]["job_id"] == "fakejob"


def test_obs_memory_background_job_notifies_on_done(tmp_path, monkeypatch):
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    vault = ObsidianVault(vault_root, read_all=True)
    video = tmp_path / "session.mkv"
    video.write_bytes(b"fake video")
    notifications = []
    config = OBSMemoryConfig(
        enabled=True,
        recording_dir=tmp_path,
        data_dir=tmp_path / "data",
        output_folder="obs_sessions",
        retention="delete_video_after_success",
        analysis_mode="episodic",
    )
    controller = OBSMemoryController(
        vault=vault,
        config=config,
        obs_client=FakeOBS(),
        transcriber=FakeTranscriber(),
        synthesizer=FakeSynthesizer(),
        on_job_done=notifications.append,
    )
    monkeypatch.setattr(
        controller,
        "process_file",
        lambda path, title=None: {
            "ok": True,
            "note_path": "obs_sessions/test.md",
            "video_deleted": True,
        },
    )

    job = controller.enqueue_process(video, title="Curso Demo")
    deadline = time.time() + 3
    while time.time() < deadline and not notifications:
        time.sleep(0.05)

    assert job["status"] in {"queued", "running", "done"}
    assert notifications
    assert notifications[0]["status"] == "done"
    assert notifications[0]["title"] == "Curso Demo"
    assert notifications[0]["note_path"] == "obs_sessions/test.md"
