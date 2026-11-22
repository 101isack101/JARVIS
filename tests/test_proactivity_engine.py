import json
from datetime import date
from types import SimpleNamespace

import pytest

from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine


def _write_card(vault, project, body, frontmatter=None):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"type": "project-memory-card", "importance": "high", "confidence": "high"}
    fm.update(frontmatter or {})
    notes_mod.write_note(vault, path, body=body, frontmatter=fm)


def _write_session(vault, name, body):
    base = vault.memory_path / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(body, encoding="utf-8")


class FakeRAG:
    def search(self, query, top_k=3):
        return []


def test_build_briefing_mentions_stale_pending(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    _write_session(
        vault, "2026-05-10_2100_sesion.md",
        "# S\n\n## Resumen\n- x\n\n## Pendientes\n- (ninguno)\n\n## Proyectos tocados\n- [[03-PROJECTS/polymath]]\n",
    )
    cfg = ProactivityConfig(min_score=0.0, stale_pending_days=7)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    block = eng.build_briefing(vault, today=date(2026, 5, 30))
    assert "BRIEFING PROACTIVO" in block
    assert "Polymath IDE" in block


def test_build_briefing_empty_when_disabled(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    cfg = ProactivityConfig(enabled=False)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    assert eng.build_briefing(vault, today=date(2026, 5, 30)) == ""


def test_build_briefing_failsafe_on_broken_vault(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    class Boom:
        memory_path = tmp_path / "nope"

        def __getattr__(self, name):
            raise RuntimeError("boom")

    assert eng.build_briefing(Boom(), today=date(2026, 5, 30)) == ""


def test_observe_then_next_opportunity_emits_struct(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    eng.observe(vault, FakeRAG(), "sigamos con Polymath IDE")
    struct = eng.next_opportunity()
    assert struct is not None
    assert struct["project"] == "Polymath IDE"
    assert "what" in struct and "why_now" in struct


def test_next_opportunity_none_when_no_candidates(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    assert eng.next_opportunity() is None


def test_dismiss_last_marks_cooldown(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    cfg = ProactivityConfig(min_score=0.0, cooldown_days=7)
    path = tmp_path / "state.json"
    eng = ProactivityEngine(config=cfg, state_path=path)
    eng.observe(vault, FakeRAG(), "Polymath IDE")
    assert eng.next_opportunity() is not None
    eng.dismiss_last()

    eng2 = ProactivityEngine(config=cfg, state_path=path)
    eng2.observe(vault, FakeRAG(), "Polymath IDE")
    assert eng2.next_opportunity() is None


# --- Error journal: los fallos fail-safe deben dejar rastro ---------------


def _journal(tmp_path, monkeypatch):
    path = tmp_path / "errors.jsonl"
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(path))
    monkeypatch.setenv("JARVIS_ERROR_JOURNAL", "true")
    return path


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _boom(*_args, **_kwargs):
    raise RuntimeError("boom")


def _engine(tmp_path):
    return ProactivityEngine(
        config=ProactivityConfig(min_score=0.0), state_path=tmp_path / "state.json"
    )


def test_build_briefing_failure_is_journaled(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    eng = _engine(tmp_path)
    monkeypatch.setattr("proactivity.engine.build_project_states", _boom)

    assert eng.build_briefing(object(), today=date(2026, 5, 30)) == ""

    rows = _rows(journal)
    assert len(rows) == 1
    assert rows[0]["source"] == "proactivity.build_briefing"
    assert rows[0]["severity"] == "warning"
    assert rows[0]["error_type"] == "RuntimeError"


def test_observe_failure_is_journaled(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    eng = _engine(tmp_path)
    monkeypatch.setattr("proactivity.engine.build_project_states", _boom)

    assert eng.observe(object(), FakeRAG(), "hola jarvis") is None

    rows = _rows(journal)
    assert len(rows) == 1
    assert rows[0]["source"] == "proactivity.observe"
    assert rows[0]["severity"] == "warning"
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["context"] == {"turn_chars": len("hola jarvis")}


@pytest.mark.parametrize("failing", ["top_opportunity", "mark_offered"])
def test_next_opportunity_failure_is_journaled(tmp_path, monkeypatch, failing):
    journal = _journal(tmp_path, monkeypatch)
    eng = _engine(tmp_path)
    opp = SimpleNamespace(id="opp-1", suggestion_struct={"project": "X"})

    monkeypatch.setattr(
        eng.queue, "top_opportunity", _boom if failing == "top_opportunity" else (lambda: opp)
    )
    monkeypatch.setattr(
        eng.queue, "mark_offered", _boom if failing == "mark_offered" else (lambda _id: None)
    )

    assert eng.next_opportunity() is None
    assert eng._last_offered_id is None

    rows = _rows(journal)
    assert len(rows) == 1
    assert rows[0]["source"] == "proactivity.next_opportunity"
    assert rows[0]["severity"] == "warning"
    assert rows[0]["error_type"] == "RuntimeError"


def test_dismiss_last_failure_is_journaled(tmp_path, monkeypatch):
    journal = _journal(tmp_path, monkeypatch)
    eng = _engine(tmp_path)
    eng._last_offered_id = "opp-1"
    monkeypatch.setattr(eng.queue, "mark_dismissed", _boom)

    assert eng.dismiss_last() is None
    assert eng._last_offered_id is None

    rows = _rows(journal)
    assert len(rows) == 1
    assert rows[0]["source"] == "proactivity.dismiss_last"
    assert rows[0]["severity"] == "warning"
    assert rows[0]["error_type"] == "RuntimeError"
    assert rows[0]["context"] == {"opportunity_id": "opp-1"}
