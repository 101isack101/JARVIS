from proactivity.project_state import ProjectState, _open_questions_from_body


def test_open_questions_excludes_resolved():
    body = (
        "## Preguntas abiertas\n\n"
        "- 2026-06-15 ¿Pregunta viva? <!-- ksi-gap:{\"gap_id\":\"g1\",\"kind\":\"poor_card\",\"status\":\"open\"} -->\n"
        "- 2026-06-15 ¿Ya resuelta? <!-- ksi-gap:{\"gap_id\":\"g2\",\"kind\":\"poor_card\",\"status\":\"resolved\"} -->\n"
    )
    qs = _open_questions_from_body(body)
    assert len(qs) == 1
    assert qs[0]["gap_id"] == "g1"
    assert "¿Pregunta viva?" in qs[0]["text"]


def test_project_state_has_open_questions_field():
    st = ProjectState(
        project="JARVIS", last_touched=None, staleness_days=None,
        open_pendings=[], open_decisions=[], current_state=[],
        importance="normal", confidence="medium",
    )
    assert st.open_questions == []


from proactivity.signals import _knowledge_gap, _ctx_knowledge_gap
from proactivity.opportunity_queue import _WHAT_BY_KIND, _suggestion_struct, opportunity_id


def _state(project, questions):
    return ProjectState(
        project=project, last_touched=None, staleness_days=None,
        open_pendings=[], open_decisions=[], current_state=[],
        importance="normal", confidence="medium",
        open_questions=[{"text": q, "gap_id": gid} for q, gid in questions],
    )


def test_knowledge_gap_emits_one_signal_per_question():
    st = _state("JARVIS", [("¿Q1?", "g1"), ("¿Q2?", "g2")])
    sigs = _knowledge_gap([st], cfg=None)
    assert len(sigs) == 2
    assert all(s.kind == "knowledge_gap" for s in sigs)
    assert {s.payload["gap_id"] for s in sigs} == {"g1", "g2"}
    assert sigs[0].payload["snippet"] == "¿Q1?"


def test_ctx_knowledge_gap_prioritizes_active_project():
    a = _state("A", [("¿qA?", "ga")])
    b = _state("B", [("¿qB?", "gb")])
    sigs = _ctx_knowledge_gap("B", [a, b])
    assert len(sigs) == 1
    assert sigs[0].project == "B"
    assert sigs[0].base_priority >= 0.7


def test_opportunity_id_keys_on_gap_id():
    from proactivity.signals import Signal
    s1 = Signal(kind="knowledge_gap", project="JARVIS", payload={"snippet": "texto A", "gap_id": "g1"}, base_priority=0.5)
    s2 = Signal(kind="knowledge_gap", project="JARVIS", payload={"snippet": "texto REFRASEADO", "gap_id": "g1"}, base_priority=0.5)
    assert opportunity_id(s1) == opportunity_id(s2)


def test_what_by_kind_has_knowledge_gap():
    assert "knowledge_gap" in _WHAT_BY_KIND
    from proactivity.signals import Signal
    sig = Signal(kind="knowledge_gap", project="JARVIS", payload={"snippet": "¿Q?", "gap_id": "g1"}, base_priority=0.5)
    assert _suggestion_struct(sig)["what"] == _WHAT_BY_KIND["knowledge_gap"]
