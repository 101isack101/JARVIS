from proactivity.config import ProactivityConfig
from proactivity.signals import Signal
from proactivity.opportunity_queue import Opportunity, OpportunityQueue, opportunity_id


def _signal(kind="stale_pending", project="Polymath IDE", prio=0.6, payload=None):
    return Signal(
        kind=kind,
        project=project,
        payload=payload or {"pending": "conectar el agente"},
        base_priority=prio,
        evidence=[f"card:{project}"],
    )


def test_opportunity_id_is_stable_and_distinguishes():
    a = opportunity_id(_signal())
    b = opportunity_id(_signal())
    c = opportunity_id(_signal(project="MTurk HITL Agent"))
    assert a == b
    assert a != c


def test_ingest_then_top_returns_highest_score(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([
        _signal(prio=0.4, payload={"pending": "menor"}),
        _signal(kind="ctx_pending", prio=0.9, payload={"pending": "mayor"}),
    ])
    top = q.top_opportunity()
    assert isinstance(top, Opportunity)
    assert top.signal.kind == "ctx_pending"
    assert "what" in top.suggestion_struct


def test_min_score_filters_weak_opportunities(tmp_path):
    cfg = ProactivityConfig(min_score=0.95)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([_signal(prio=0.1)])
    assert q.top_opportunity() is None


def test_dedup_same_id_not_offered_twice_in_session(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    q = OpportunityQueue(tmp_path / "state.json", config=cfg)
    q.ingest([_signal()])
    top = q.top_opportunity()
    assert top is not None
    q.mark_offered(top.id)
    assert q.top_opportunity() is None  # ya ofrecida en esta sesión
