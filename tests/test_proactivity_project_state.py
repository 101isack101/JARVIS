from proactivity.project_state import parse_card_sections, section_bullets


CARD = (
    "# Polymath IDE - Memory Card\n\n"
    "## Current State\n\n"
    "- 2026-05-01 [normal/high] editor Monaco integrado (source: [[s1]])\n\n"
    "## Decisions\n\n"
    "- 2026-05-02 [high/high] usar WebSocket para el agente (source: [[s2]])\n\n"
    "## Pending\n\n"
    "- 2026-05-03 [high/high] conectar el agente al server (source: [[s3]])\n"
    "- 2026-05-04 [normal/medium] escribir tests e2e (source: [[s4]])\n\n"
    "## Procedures\n\n"
    "- (pending)\n"
)


def test_parse_card_sections_splits_by_heading():
    sections = parse_card_sections(CARD)
    assert "Pending" in sections
    assert "Decisions" in sections
    assert "Current State" in sections
    # el placeholder de sección vacía se descarta
    assert sections["Procedures"] == []


def test_section_bullets_strips_marker_and_ignores_placeholder():
    sections = parse_card_sections(CARD)
    pend = section_bullets(sections, "Pending")
    assert len(pend) == 2
    assert pend[0].startswith("2026-05-03")
    assert "conectar el agente al server" in pend[0]
    assert section_bullets(sections, "Procedures") == []
    assert section_bullets(sections, "NoExiste") == []
