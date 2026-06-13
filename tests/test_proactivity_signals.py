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


from types import SimpleNamespace

from proactivity.signals import detect_contextual_signals


class FakeRAG:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search(self, query, top_k=3):
        self.queries.append((query, top_k))
        return self.results[:top_k]


def _rag(score, text, title="Nota", rel_path="Jarvis Memory/Interview_Copilot.md"):
    return SimpleNamespace(score=score, chunk=SimpleNamespace(title=title, rel_path=rel_path, text=text))


def test_ctx_pending_fires_when_active_project_has_pendings():
    cfg = ProactivityConfig()
    states = [_state(project="Polymath IDE")]
    rag = FakeRAG()
    signals = detect_contextual_signals(
        "sigamos con Polymath IDE el server", states, rag, cfg
    )
    assert any(s.kind == "ctx_pending" and s.project == "Polymath IDE" for s in signals)


def test_cross_project_fires_on_high_score_other_project():
    cfg = ProactivityConfig()
    states = [_state(project="Polymath IDE")]
    rag = FakeRAG(results=[_rag(0.82, "Implementamos FAISS RAG local", title="Interview_Copilot")])
    signals = detect_contextual_signals(
        "quiero búsqueda semántica con FAISS", states, rag, cfg
    )
    assert any(s.kind == "cross_project" for s in signals)
    cp = next(s for s in signals if s.kind == "cross_project")
    assert cp.evidence


def test_cross_project_ignores_low_score():
    cfg = ProactivityConfig()
    rag = FakeRAG(results=[_rag(0.10, "ruido")])
    signals = detect_contextual_signals("algo", [_state()], rag, cfg)
    assert all(s.kind != "cross_project" for s in signals)


def test_contextual_rag_failure_is_fail_safe():
    cfg = ProactivityConfig()

    class Boom:
        def search(self, *a, **k):
            raise RuntimeError("boom")

    signals = detect_contextual_signals("Polymath IDE", [_state()], Boom(), cfg)
    assert all(s.kind != "cross_project" for s in signals)
