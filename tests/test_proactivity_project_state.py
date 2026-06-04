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


from datetime import date

from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from proactivity.project_state import ProjectState, build_project_states


def _write_card(vault, project, body, frontmatter=None):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"type": "project-memory-card", "importance": "high", "confidence": "medium"}
    fm.update(frontmatter or {})
    notes_mod.write_note(vault, path, body=body, frontmatter=fm)
    return path


def _write_session(vault, name, body):
    base = vault.memory_path / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(body, encoding="utf-8")


def test_build_states_derives_pendings_and_staleness(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault,
        "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n\n"
        "## Decisions\n\n- 2026-05-02 [high/high] usar WebSocket\n",
        frontmatter={"importance": "high", "confidence": "high"},
    )
    _write_session(
        vault,
        "2026-05-20_2100_sesion.md",
        "# Sesion\n\n## Resumen\n- algo\n\n## Pendientes\n- (ninguno)\n\n"
        "## Proyectos tocados\n- [[03-PROJECTS/polymath]]\n",
    )

    states = build_project_states(vault, today=date(2026, 5, 30))
    by_name = {s.project: s for s in states}

    assert "Polymath IDE" in by_name
    poly = by_name["Polymath IDE"]
    assert isinstance(poly, ProjectState)
    assert poly.last_touched == date(2026, 5, 20)
    assert poly.staleness_days == 10
    assert any("conectar el agente" in p for p in poly.open_pendings)
    assert any("WebSocket" in d for d in poly.open_decisions)
    assert poly.importance == "high"


def test_project_without_card_or_session_is_absent(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n\n- algo\n")

    states = build_project_states(vault, today=date(2026, 5, 30))
    names = {s.project for s in states}
    assert "Polymath IDE" in names
    assert "Agentics_Code_Team" not in names
    assert {s.project: s for s in states}["Polymath IDE"].last_touched is None
