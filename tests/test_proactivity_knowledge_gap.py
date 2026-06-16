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
