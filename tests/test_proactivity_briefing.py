from proactivity.signals import Signal
from proactivity.opportunity_queue import Opportunity, opportunity_id


def _opp(kind, project, why):
    sig = Signal(kind=kind, project=project, payload={"pending": why}, base_priority=0.6, evidence=[f"card:{project}"])
    return Opportunity(
        id=opportunity_id(sig), signal=sig, score=0.6,
        suggestion_struct={"what": "x", "project": project, "why_now": {"pending": why}, "evidence": [f"card:{project}"], "action_hint": kind},
    )


def test_render_briefing_lists_top_k():
    from proactivity.briefing import render_briefing
    opps = [
        _opp("stale_pending", "Upwork Agent", "setup .env + RSS + Discord webhook"),
        _opp("stale_project", "MTurk HITL Agent", "smoke test pendiente"),
        _opp("cross_project", "Interview_Copilot", "FAISS reutilizable"),
        _opp("stale_pending", "Polymath IDE", "conectar agente"),
    ]
    block = render_briefing(opps, top_k=3)
    assert "BRIEFING PROACTIVO" in block
    assert "Upwork Agent" in block
    assert "MTurk HITL Agent" in block
    # respeta top_k=3: el cuarto no aparece
    assert "Polymath IDE" not in block


def test_render_briefing_empty_when_no_opportunities():
    from proactivity.briefing import render_briefing
    assert render_briefing([], top_k=3) == ""
