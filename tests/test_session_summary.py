"""Tests para memory/session_summary.py — síntesis Claude + recall."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memory.obsidian_vault import ObsidianVault
from memory.session_journal import SessionJournal
from memory.session_summary import (
    SESSIONS_SUBDIR,
    build_recent_recall_block,
    load_recent_summaries,
    search_session_summaries,
    synthesize_and_save,
    load_last_summary,
)


@pytest.fixture
def temp_vault(tmp_path: Path) -> ObsidianVault:
    return ObsidianVault(
        vault_path=tmp_path,
        memory_folder="Jarvis Memory",
        read_all=True,
    )


class StubReasoner:
    """ClaudeReasoner falso: devuelve un .text fijo sin tocar la red."""

    def __init__(self, text: str):
        self._text = text
        self.calls: list[str] = []

    def ask(self, prompt, context_extra=None, max_tokens=1024):
        self.calls.append(prompt)

        class _R:
            text = self._text

        return _R()


def test_synthesize_writes_note_with_frontmatter(temp_vault, tmp_path):
    journal = SessionJournal(tmp_path / "journal.jsonl")
    journal.append_turn("revisemos agentics", "el agente corrió hace 18 min")
    journal.append_turn("algun fallo?", "ninguno, todo verde")
    journal.append_turn("dejemos polymath", "anotado como pendiente")
    reasoner = StubReasoner(
        "## Resumen\n- Revisamos Agentics.\n\n## Pendientes\n- Retomar Polymath.\n\n"
        "## Proyectos tocados\n- [[03-PROJECTS/jarvis]]"
    )

    path = synthesize_and_save(
        journal, reasoner, temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is not None
    assert path.exists()
    assert SESSIONS_SUBDIR.replace("/", "\\") in str(path) or SESSIONS_SUBDIR in str(path.as_posix())
    text = path.read_text(encoding="utf-8")
    assert "type: session-journal" in text
    assert "session_id: abc12345" in text
    assert "## Resumen" in text
    assert "## Pendientes" in text
    assert journal.has_pending() is False


def test_synthesize_skips_when_below_min_turns(temp_vault, tmp_path):
    journal = SessionJournal(tmp_path / "journal.jsonl")
    journal.append_turn("hola", "buenas")
    reasoner = StubReasoner("no debería llamarse")

    path = synthesize_and_save(
        journal, reasoner, temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is None
    assert reasoner.calls == []
    assert journal.has_pending() is True


def test_synthesize_keeps_journal_if_write_fails(temp_vault, tmp_path, monkeypatch):
    journal = SessionJournal(tmp_path / "journal.jsonl")
    for i in range(3):
        journal.append_turn(f"u{i}", f"j{i}")
    reasoner = StubReasoner("## Resumen\n- algo")

    import memory.session_summary as mod

    def boom(*args, **kwargs):
        raise OSError("disco lleno")

    monkeypatch.setattr(mod.notes_mod, "write_note", boom)

    path = synthesize_and_save(
        journal, reasoner, temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is None
    assert journal.has_pending() is True


def test_load_last_summary_picks_most_recent(temp_vault):
    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-26_1000_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# Sesión vieja\n\n"
        "## Resumen\n- viejo\n\n## Pendientes\n- nada\n",
        encoding="utf-8",
    )
    (base / "2026-05-28_1500_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# Sesión nueva\n\n"
        "## Resumen\n- nuevo\n\n## Pendientes\n- retomar X\n",
        encoding="utf-8",
    )

    out = load_last_summary(temp_vault, max_chars=1000)
    assert out is not None
    assert "nuevo" in out
    assert "retomar X" in out
    assert "viejo" not in out


def test_load_recent_summaries_returns_compact_session_map(temp_vault):
    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    for name, marker in [
        ("2026-05-29_1000_sesion.md", "viejo"),
        ("2026-05-30_1100_sesion.md", "ayer"),
        ("2026-05-31_1200_sesion.md", "hoy"),
    ]:
        (base / name).write_text(
            "---\ntype: session-journal\n---\n\n# S\n\n"
            f"## Resumen\n- {marker}\n\n## Pendientes\n- revisar {marker}\n",
            encoding="utf-8",
        )

    recent = load_recent_summaries(temp_vault, limit=2, max_chars_each=300)
    block = build_recent_recall_block(recent)

    assert [item["date"] for item in recent] == ["2026-05-31", "2026-05-30"]
    assert "MAPA DE SESIONES RECIENTES" in block
    assert "hoy" in block
    assert "ayer" in block
    assert "viejo" not in block


def test_search_session_summaries_understands_ayer(temp_vault):
    from datetime import date

    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-30_2326_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n"
        "## Resumen\n- Hablamos de la UI de Jarvis y el nucleo neural.\n\n"
        "## Pendientes\n- Integrar el bridge web.\n",
        encoding="utf-8",
    )
    (base / "2026-05-31_1956_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n"
        "## Resumen\n- Revisamos memoria temporal.\n\n## Pendientes\n- Ninguno.\n",
        encoding="utf-8",
    )

    result = search_session_summaries(
        temp_vault,
        query="UI Jarvis",
        when="ayer",
        today=date(2026, 5, 31),
    )

    assert result["target_date"] == "2026-05-30"
    assert result["found"] == 1
    assert "nucleo neural" in result["sessions"][0]["summary"]


def test_search_session_summaries_falls_back_to_date_when_terms_miss(temp_vault):
    from datetime import date

    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-30_2100_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n"
        "## Resumen\n- Revisamos decisiones generales.\n\n"
        "## Pendientes\n- Ninguno.\n",
        encoding="utf-8",
    )

    result = search_session_summaries(
        temp_vault,
        query="tema que no aparece",
        when="ayer",
        today=date(2026, 5, 31),
    )

    assert result["found"] == 1
    assert result["sessions"][0]["date"] == "2026-05-30"


def test_load_last_summary_returns_none_when_empty(temp_vault):
    assert load_last_summary(temp_vault, max_chars=1000) is None


def test_load_last_summary_respects_max_chars(temp_vault):
    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-28_1500_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n## Resumen\n- "
        + ("x" * 5000)
        + "\n",
        encoding="utf-8",
    )
    out = load_last_summary(temp_vault, max_chars=200)
    assert out is not None
    assert len(out) <= 200


def test_synthesize_keeps_journal_if_reasoner_raises(temp_vault, tmp_path):
    """Si Claude está caído (ask lanza excepción), la nota no se escribe y el
    journal queda intacto para reintentar como huérfano al próximo arranque.
    Este es el corazón de la garantía de durabilidad del spec."""
    journal = SessionJournal(tmp_path / "journal.jsonl")
    for i in range(3):
        journal.append_turn(f"u{i}", f"j{i}")

    class BoomReasoner:
        def ask(self, prompt, context_extra=None, max_tokens=1024):
            raise RuntimeError("Claude caído")

    path = synthesize_and_save(
        journal, BoomReasoner(), temp_vault, min_turns=3, session_id="abc12345"
    )

    assert path is None
    assert journal.has_pending() is True  # journal NO se limpió


def test_load_last_summary_excludes_proyectos_tocados(temp_vault):
    """load_last_summary devuelve solo Resumen + Pendientes; la sección
    'Proyectos tocados' (que viene después de Pendientes) NO debe filtrarse."""
    base = temp_vault.memory_path / SESSIONS_SUBDIR
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-05-28_1500_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n"
        "## Resumen\n- algo importante\n\n"
        "## Pendientes\n- retomar Y\n\n"
        "## Proyectos tocados\n- [[03-PROJECTS/secreto]]\n",
        encoding="utf-8",
    )

    out = load_last_summary(temp_vault, max_chars=1000)
    assert out is not None
    assert "algo importante" in out
    assert "retomar Y" in out
    assert "secreto" not in out  # la sección Proyectos tocados se excluye
    assert "Proyectos tocados" not in out


def test_build_recall_block_wraps_with_header():
    from memory.session_summary import build_recall_block

    block = build_recall_block("## Resumen\n- algo\n\n## Pendientes\n- retomar X")
    assert "CONTEXTO DE SESION ANTERIOR" in block
    assert "retomar X" in block


def test_build_recall_block_empty_returns_empty_string():
    from memory.session_summary import build_recall_block

    assert build_recall_block(None) == ""
    assert build_recall_block("") == ""
