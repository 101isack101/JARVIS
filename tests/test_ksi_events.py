from memory.self_improvement.events import (
    MemoryEvent,
    event_id,
    parse_bullet,
    serialize_bullet,
)


def test_event_id_is_stable_and_normalized():
    a = event_id("Usar Sonnet 4.6 como reasoner")
    b = event_id("usar   sonnet 4.6 COMO reasoner")
    assert a == b
    assert len(a) == 16


def test_parse_legacy_bullet():
    line = "- 2026-06-10 [high/medium] Migrar a Tauri v2 (source: [[nota]])"
    ev = parse_bullet(line, section="Decisions", project="JARVIS")
    assert ev is not None
    assert ev.text == "Migrar a Tauri v2"
    assert ev.learned_at == "2026-06-10"
    assert ev.confidence == 0.6
    assert ev.section == "Decisions"
    assert ev.project == "JARVIS"
    assert ev.reinforced == 1


def test_parse_ksi_bullet_roundtrip():
    ev = MemoryEvent(
        id="abc123", text="Hecho X", section="Facts", project="JARVIS",
        source="card:JARVIS.md", learned_at="2026-06-14", confidence=0.85, reinforced=2,
    )
    line = serialize_bullet(ev)
    parsed = parse_bullet(line, section="Facts", project="JARVIS")
    assert parsed.text == "Hecho X"
    assert parsed.confidence == 0.85
    assert parsed.reinforced == 2
    assert parsed.id == "abc123"


def test_parse_ignores_pending_and_blank():
    assert parse_bullet("- (pending)", section="Facts", project="JARVIS") is None
    assert parse_bullet("   ", section="Facts", project="JARVIS") is None
    assert parse_bullet("## Facts", section="Facts", project="JARVIS") is None
