"""
memory/session_summary.py - Síntesis del journal en nota fechada + recall.

Responsabilidad única: convertir un SessionJournal en una nota-diario destilada
por Claude, y leer la última nota para inyectarla al arranque.

Las notas viven en `Jarvis Memory/sessions/` (dentro de la barrera assert_writable
del vault). RAG las indexa automáticamente, así que también son recall-ables.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault
from memory.session_journal import SessionJournal

# Subcarpeta DENTRO de memory_folder (Jarvis Memory) → respeta assert_writable.
SESSIONS_SUBDIR = "sessions"

_SYNTHESIS_INSTRUCTIONS = (
    "Eres el cronista de JARVIS. Te paso el transcript crudo de una sesión de voz "
    "entre Isaac y JARVIS. Destílalo en una nota breve en español con EXACTAMENTE "
    "estas tres secciones markdown y nada más:\n\n"
    "## Resumen\n- 3 a 5 bullets de lo que se conversó o hizo.\n\n"
    "## Pendientes\n- Items accionables que quedaron abiertos (si no hay, escribe "
    "'- (ninguno)').\n\n"
    "## Proyectos tocados\n- Wikilinks tipo [[03-PROJECTS/nombre]] de proyectos "
    "mencionados (si ninguno, escribe '- (ninguno)').\n\n"
    "No inventes. Sé conciso. No agregues encabezado de título ni frontmatter."
)


def _format_transcript(turns: list[dict]) -> str:
    lines: list[str] = []
    for t in turns:
        user = (t.get("user") or "").strip()
        jarvis = (t.get("jarvis") or "").strip()
        if user:
            lines.append(f"Isaac: {user}")
        if jarvis:
            lines.append(f"JARVIS: {jarvis}")
    return "\n".join(lines)


def synthesize_and_save(
    journal: SessionJournal,
    reasoner,
    vault: ObsidianVault,
    min_turns: int,
    session_id: str,
) -> Path | None:
    """Sintetiza el journal en una nota fechada. Devuelve el path o None.

    - Si turn_count < min_turns → None (sesión trivial, sin nota, sin gastar Claude).
    - Si la escritura falla → NO limpia el journal (reintenta como huérfano).
    - Solo limpia el journal tras escribir con éxito.
    """
    turns = journal.read_turns()
    if len(turns) < min_turns:
        return None
    if reasoner is None:
        return None

    transcript = _format_transcript(turns)
    try:
        resp = reasoner.ask(
            _SYNTHESIS_INSTRUCTIONS,
            context_extra="TRANSCRIPT:\n" + transcript,
            max_tokens=600,
        )
        synthesized = (resp.text or "").strip()
    except Exception:
        return None  # Claude caído → journal queda como huérfano

    if not synthesized:
        return None

    now = datetime.now()
    fname = f"{now.strftime('%Y-%m-%d_%H%M')}_sesion.md"
    path = vault.memory_path / SESSIONS_SUBDIR / fname

    frontmatter = {
        "type": "session-journal",
        "project": "[[03-PROJECTS/jarvis]]",
        "date": now.strftime("%Y-%m-%d"),
        "session_id": session_id,
        "generated_by": "claude-sonnet-4-6",
    }
    body = f"# Sesión {now.strftime('%Y-%m-%d %H:%M')}\n\n{synthesized}\n"

    try:
        notes_mod.write_note(
            vault,
            path,
            body=body,
            frontmatter=frontmatter,
            tags=["jarvis-session", "session-journal"],
        )
    except Exception:
        return None  # NO limpiar journal: se reintenta al próximo arranque

    journal.clear()
    return path


def _sessions_dir(vault: ObsidianVault) -> Path:
    return vault.memory_path / SESSIONS_SUBDIR


def _extract_recall_sections(text: str) -> str:
    """Devuelve Resumen + Pendientes del cuerpo (omite frontmatter y Proyectos)."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    keep: list[str] = []
    capture = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            capture = heading in ("resumen", "pendientes")
        if capture:
            keep.append(line)
    return "\n".join(keep).strip()


def load_last_summary(vault: ObsidianVault, max_chars: int) -> str | None:
    """Lee la nota de sesión más reciente. Devuelve Resumen + Pendientes o None.

    Ordena por nombre de archivo descendente: el naming YYYY-MM-DD_HHMM es
    cronológico, así que el primero es el más nuevo.
    """
    base = _sessions_dir(vault)
    if not base.exists():
        return None
    files = sorted(base.glob("*_sesion.md"), reverse=True)
    if not files:
        return None
    try:
        text = files[0].read_text(encoding="utf-8")
    except OSError:
        return None
    sections = _extract_recall_sections(text)
    if not sections:
        return None
    return sections[:max_chars]
