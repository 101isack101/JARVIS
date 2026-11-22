import json
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
    notifications = []
    controller = StudyModeController(
        vault=vault,
        reasoner=reasoner,
        config=StudyModeConfig(capture_interval_s=999, max_chars_per_capture=2000),
        on_flush_done=notifications.append,
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
    assert notifications and notifications[0]["ok"] is True
    note_path = vault.memory_path / "Study Mode" / "LangGraph.md"
    assert "Nota background de prueba" in note_path.read_text(encoding="utf-8")


# --- Error journal: fallos tragados de callback y observer ---------------------


def _journal(monkeypatch, tmp_path):
    path = tmp_path / "journal" / "error_journal.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(path))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "true")
    return path


def _entries(path, source):
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return [row for row in rows if row["source"] == source]


def _flush_with_callback(tmp_path, callback):
    vault = ObsidianVault(tmp_path, read_all=True)
    controller = StudyModeController(
        vault=vault,
        reasoner=FakeReasoner(),
        config=StudyModeConfig(capture_interval_s=999, max_chars_per_capture=2000),
        on_flush_done=callback,
    )
    controller.start(
        title="Journal Study",
        note_path="Study Mode/Journal.md",
        continuous=False,
        capture_now=False,
    )
    controller.add_observation("Observacion para forzar un flush en background.")
    assert controller.flush_background()["ok"] is True
    assert controller._flush_thread is not None
    controller._flush_thread.join(timeout=3)
    assert not controller._flush_thread.is_alive()
    return controller


def test_flush_callback_failure_is_journaled_and_result_kept(tmp_path, monkeypatch):
    journal = _journal(monkeypatch, tmp_path)

    def boom(result):
        raise RuntimeError("overlay down")

    controller = _flush_with_callback(tmp_path, boom)

    assert controller._last_flush_result["ok"] is True
    assert controller._flush_status["running"] is False
    rows = _entries(journal, "study.flush_done")
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"
    assert rows[0]["error_type"] == "RuntimeError"


def test_flush_callback_success_writes_nothing(tmp_path, monkeypatch):
    journal = _journal(monkeypatch, tmp_path)
    seen = []

    _flush_with_callback(tmp_path, seen.append)

    assert seen and seen[0]["ok"] is True
    assert _entries(journal, "study.flush_done") == []


def _run_observer(tmp_path, outcomes):
    """Corre el observer real; ``outcomes`` = lista "raise"/"ok"/"not_ok" por intento.

    El fake de capture_page detiene el loop en el ultimo intento.
    """
    vault = ObsidianVault(tmp_path, read_all=True)
    controller = StudyModeController(
        vault=vault,
        reasoner=FakeReasoner(),
        config=StudyModeConfig(capture_interval_s=0.01, max_chars_per_capture=2000),
    )
    attempts = []

    def fake_capture_page(intent="manual", **kwargs):
        attempts.append(intent)
        outcome = outcomes[len(attempts) - 1]
        if len(attempts) == len(outcomes):
            controller._stop_event.set()
        if outcome == "raise":
            raise RuntimeError(f"capture fail {len(attempts)}")
        if outcome == "not_ok":
            return {"ok": False, "captured": False, "error": "sin texto"}
        return {"ok": True, "captured": False, "duplicate": True}

    controller.capture_page = fake_capture_page
    controller.start(
        title="Observer Study",
        note_path="Study Mode/Observer.md",
        continuous=True,
        capture_now=False,
    )
    assert controller._thread is not None
    controller._thread.join(timeout=2)
    assert not controller._thread.is_alive()
    assert attempts == ["continuous_observer"] * len(outcomes)
    return controller


def test_observer_three_failures_journal_one_entry(tmp_path, monkeypatch):
    journal = _journal(monkeypatch, tmp_path)

    _run_observer(tmp_path, ["raise", "raise", "raise"])

    rows = _entries(journal, "study.observer")
    assert len(rows) == 1
    assert rows[0]["severity"] == "warning"
    assert rows[0]["error_type"] == "RuntimeError"
    assert "capture fail 1" in rows[0]["error_message"]


def test_observer_failure_success_failure_journals_two_entries(tmp_path, monkeypatch):
    journal = _journal(monkeypatch, tmp_path)

    _run_observer(tmp_path, ["raise", "ok", "raise"])

    rows = _entries(journal, "study.observer")
    assert len(rows) == 2
    assert "capture fail 1" in rows[0]["error_message"]
    assert "capture fail 3" in rows[1]["error_message"]


def test_observer_ok_false_result_is_not_journaled(tmp_path, monkeypatch):
    journal = _journal(monkeypatch, tmp_path)

    _run_observer(tmp_path, ["not_ok", "not_ok", "not_ok"])

    assert _entries(journal, "study.observer") == []
