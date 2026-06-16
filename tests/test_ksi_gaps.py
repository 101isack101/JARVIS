from memory.self_improvement.events import MemoryEvent
from memory.self_improvement.gaps import (
    KnowledgeGap,
    collect_gaps,
    detect_poor_cards,
    detect_stale_facts,
    gap_id,
)


class _Cfg:
    min_card_bullets = 4
    stale_confidence = 0.3
    decay_half_life_days = 45


class _State:
    def __init__(self, project, current_state, pendings, decisions, staleness_days):
        self.project = project
        self.current_state = current_state
        self.open_pendings = pendings
        self.open_decisions = decisions
        self.staleness_days = staleness_days


def _ev(text, project="JARVIS", conf=0.6, learned="2026-06-15", sup=None):
    return MemoryEvent(id=text[:8], text=text, section="Facts", project=project,
                       learned_at=learned, confidence=conf, superseded_by=sup)


def test_gap_id_stable():
    a = gap_id("poor_card", "JARVIS", "JARVIS")
    b = gap_id("poor_card", "JARVIS", "JARVIS")
    assert a == b and len(a) == 16


def test_detect_poor_cards():
    rich = _State("Rich", ["a"], ["b", "c"], ["d", "e"], staleness_days=2)
    poor = _State("Poor", [], [], [], staleness_days=3)
    gaps = detect_poor_cards([rich, poor], _Cfg())
    assert [g.project for g in gaps] == ["Poor"]
    assert gaps[0].kind == "poor_card"


def test_poor_card_ignores_projects_without_activity():
    ghost = _State("Ghost", [], [], [], staleness_days=None)
    assert detect_poor_cards([ghost], _Cfg()) == []


def test_detect_stale_facts():
    fresh = _ev("hecho fresco", conf=0.8, learned="2026-06-15")
    stale = _ev("hecho viejo", conf=0.8, learned="2026-01-01")
    superseded = _ev("ya reemplazado", conf=0.1, learned="2026-01-01", sup="x")
    gaps = detect_stale_facts([fresh, stale, superseded], _Cfg(), today="2026-06-15")
    keys = [g.context for g in gaps]
    assert any("hecho viejo" in k for k in keys)
    assert not any("fresco" in k for k in keys)
    assert not any("reemplazado" in k for k in keys)


def test_collect_gaps_merges_all_kinds():
    poor = _State("Poor", [], [], [], staleness_days=3)
    stale = _ev("hecho viejo", conf=0.8, learned="2026-01-01", project="Poor")
    gaps = collect_gaps([poor], [stale], _Cfg(), today="2026-06-15")
    kinds = {g.kind for g in gaps}
    assert "poor_card" in kinds and "stale_fact" in kinds
