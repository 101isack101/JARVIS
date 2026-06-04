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
