from memory.self_improvement.events import MemoryEvent, parse_bullet
from memory.self_improvement.projection import rebuild_card_body, snapshot_previous


def _ev(text, section, conf, learned="2026-06-14"):
    return MemoryEvent(id=text[:8], text=text, section=section, project="JARVIS",
                       learned_at=learned, confidence=conf)


def test_rebuild_preserves_all_events_sorted_by_confidence():
    events = [
        _ev("baja", "Facts", 0.3),
        _ev("alta", "Facts", 0.9),
        _ev("decision", "Decisions", 0.7),
    ]
    body = rebuild_card_body("JARVIS", events)
    assert "## Facts" in body and "## Decisions" in body
    assert body.index("alta") < body.index("baja")
    fact_lines = [l for l in body.splitlines() if l.startswith("- ") and "<!-- ksi:" in l]
    assert len(fact_lines) == 3
    parsed = parse_bullet(fact_lines[0], section="Facts", project="JARVIS")
    assert parsed is not None


def test_snapshot_copies_existing_card(tmp_path):
    card = tmp_path / "JARVIS.md"
    card.write_text("contenido previo", encoding="utf-8")
    snap = snapshot_previous(tmp_path, card)
    assert snap is not None and snap.exists()
    assert snap.read_text(encoding="utf-8") == "contenido previo"
    assert snap != card


def test_snapshot_missing_card_returns_none(tmp_path):
    assert snapshot_previous(tmp_path, tmp_path / "noexiste.md") is None
