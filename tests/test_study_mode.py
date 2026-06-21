import threading
import time

from memory.obsidian_vault import ObsidianVault
from study.mode import StudyModeController, StudyModeConfig


class FakeReasoner:
    def ask(self, prompt, context_extra=None, max_tokens=1024):
        class Response:
            text = "## Resumen ejecutivo\n\n- Nota sintetizada de prueba.\n\n## Fuentes\n\n- Test"

        return Response()


class BlockingReasoner:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def ask(self, prompt, context_extra=None, max_tokens=1024):
        self.started.set()
        self.release.wait(timeout=2)

        class Response:
            text = "## Resumen ejecutivo\n\n- Nota background de prueba.\n\n## Fuentes\n\n- Test"

        return Response()


def test_study_mode_observation_flushes_to_obsidian(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    controller = StudyModeController(
        vault=vault,
        reasoner=FakeReasoner(),
        config=StudyModeConfig(capture_interval_s=999, max_chars_per_capture=2000),
    )

    started = controller.start(
        title="AWS Lambda Study",
        note_path="Study Mode/AWS Lambda.md",
        continuous=False,
        capture_now=False,
    )
    assert started["ok"] is True

    captured = controller.add_observation(
        "Lambda event source mappings conectan streams/queues con funciones."
    )
    assert captured["captured"] is True

    flushed = controller.flush_now()
    assert flushed["ok"] is True
    assert flushed["flushed"] is True
    assert flushed["used_reasoner"] is True

    note_path = vault.memory_path / "Study Mode" / "AWS Lambda.md"
    text = note_path.read_text(encoding="utf-8")
    assert "AWS Lambda Study" in text
    assert "Nota sintetizada de prueba" in text

    stopped = controller.stop(flush=False)
    assert stopped["ok"] is True


def test_study_mode_background_flush_returns_before_reasoner_finishes(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    reasoner = BlockingReasoner()
    controller = StudyModeController(
        vault=vault,
        reasoner=reasoner,
        config=StudyModeConfig(capture_interval_s=999, max_chars_per_capture=2000),
    )
    controller.start(
        title="LangGraph Study",
        note_path="Study Mode/LangGraph.md",
        continuous=False,
        capture_now=False,
    )
    controller.add_observation("Reflection Agents usan un generador y un critico.")

    t0 = time.perf_counter()
    result = controller.flush_background()
    elapsed = time.perf_counter() - t0

    assert result["ok"] is True
    assert result["background"] is True
    assert elapsed < 0.2
    assert reasoner.started.wait(timeout=1)

    reasoner.release.set()
    assert controller._flush_thread is not None
    controller._flush_thread.join(timeout=2)

    status = controller.status()
    assert status["flush"]["running"] is False
    assert status["flush"]["last_ok"] is True
    note_path = vault.memory_path / "Study Mode" / "LangGraph.md"
    assert "Nota background de prueba" in note_path.read_text(encoding="utf-8")
