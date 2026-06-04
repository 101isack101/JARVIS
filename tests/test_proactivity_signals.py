from datetime import date

from proactivity.config import ProactivityConfig
from proactivity.project_state import ProjectState
from proactivity.signals import Signal, detect_startup_signals


def _state(project="Polymath IDE", **kw):
    base = dict(
        project=project,
        last_touched=date(2026, 5, 20),
        staleness_days=10,
        open_pendings=["2026-05-03 [high/high] conectar el agente al server"],
        open_decisions=[],
        current_state=[],
        importance="high",
        confidence="high",
    )
    base.update(kw)
    return ProjectState(**base)


def test_stale_pending_fires_when_project_stale():
    cfg = ProactivityConfig(stale_pending_days=7)
    signals = detect_startup_signals([_state()], cfg)
    kinds = {s.kind for s in signals}
    assert "stale_pending" in kinds
    sp = next(s for s in signals if s.kind == "stale_pending")
    assert sp.project == "Polymath IDE"
    assert "conectar el agente al server" in sp.payload["pending"]
    assert sp.base_priority > 0


def test_stale_pending_does_not_fire_when_recent():
    cfg = ProactivityConfig(stale_pending_days=7)
    fresh = _state(staleness_days=2)
    signals = detect_startup_signals([fresh], cfg)
    assert all(s.kind != "stale_pending" for s in signals)


def test_no_pendings_no_stale_pending_signal():
    cfg = ProactivityConfig(stale_pending_days=7)
    empty = _state(open_pendings=[])
    signals = detect_startup_signals([empty], cfg)
    assert all(s.kind != "stale_pending" for s in signals)


def test_stale_project_fires_for_important_untouched_project():
    cfg = ProactivityConfig(stale_project_days=14)
    st = _state(staleness_days=20, importance="high", open_pendings=[])
    signals = detect_startup_signals([st], cfg)
    assert any(s.kind == "stale_project" for s in signals)


def test_stale_project_ignores_low_importance():
    cfg = ProactivityConfig(stale_project_days=14)
    st = _state(staleness_days=20, importance="low", open_pendings=[])
    signals = detect_startup_signals([st], cfg)
    assert all(s.kind != "stale_project" for s in signals)


def test_open_loop_fires_when_decisions_without_recent_progress():
    cfg = ProactivityConfig(stale_project_days=14)
    st = _state(
        staleness_days=20,
        open_pendings=[],
        open_decisions=["2026-05-02 [high/high] usar WebSocket para el agente"],
    )
    signals = detect_startup_signals([st], cfg)
    assert any(s.kind == "open_loop" for s in signals)
