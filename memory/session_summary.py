"""Session journal synthesis and temporal recall helpers."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault
from memory.session_journal import SessionJournal

# Subfolder inside the writable Jarvis Memory folder.
SESSIONS_SUBDIR = "sessions"
DEFAULT_RECENT_LIMIT = 5
DEFAULT_SESSION_MAX_CHARS = 900
DEFAULT_CURRENT_SESSION_TURNS = 10
DEFAULT_CURRENT_SESSION_MAX_CHARS = 4500

_SYNTHESIS_INSTRUCTIONS = (
    "Eres el cronista de JARVIS. Te paso el transcript crudo de una sesion de voz "
    "entre Isaac y JARVIS. Destilalo en una nota breve en espanol con EXACTAMENTE "
    "estas tres secciones markdown y nada mas:\n\n"
    "## Resumen\n- 3 a 5 bullets de lo que se converso o hizo.\n\n"
    "## Pendientes\n- Items accionables que quedaron abiertos (si no hay, escribe "
    "'- (ninguno)').\n\n"
    "## Proyectos tocados\n- Wikilinks tipo [[03-PROJECTS/nombre]] de proyectos "
    "mencionados (si ninguno, escribe '- (ninguno)').\n\n"
    "No inventes. Se conciso. No agregues encabezado de titulo ni frontmatter."
)


def _format_transcript(turns: list[dict]) -> str:
    lines: list[str] = []
    for turn in turns:
        user = (turn.get("user") or "").strip()
        jarvis = (turn.get("jarvis") or "").strip()
        if user:
            lines.append(f"Isaac: {user}")
        if jarvis:
            lines.append(f"JARVIS: {jarvis}")
    return "\n".join(lines)


def _format_turn_for_recall(turn: dict) -> str:
    user = (turn.get("user") or "").strip()
    jarvis = (turn.get("jarvis") or "").strip()
    parts: list[str] = []
    if user:
        parts.append(f"Isaac: {user}")
    if jarvis:
        parts.append(f"JARVIS: {jarvis}")
    return "\n".join(parts)


def _limit_text(text: str, max_chars: int) -> str:
    max_chars = max(200, int(max_chars or DEFAULT_CURRENT_SESSION_MAX_CHARS))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 24].rstrip() + "\n...[truncated]"


def _score_turn_for_query(turn: dict, terms: list[str]) -> int:
    if not terms:
        return 0
    haystack = f"{turn.get('user', '')}\n{turn.get('jarvis', '')}".lower()
    return sum(1 for term in terms if term in haystack)


def current_session_recall(
    journal: SessionJournal,
    query: str = "",
    limit: int = DEFAULT_CURRENT_SESSION_TURNS,
    max_chars: int = DEFAULT_CURRENT_SESSION_MAX_CHARS,
) -> dict:
    """Recall over the still-open session journal.

    This is intentionally cheap and deterministic. It covers the gap where
    Gemini Live reconnects or compresses context before the session has been
    synthesized into a durable session note.
    """
    turns = journal.read_turns()
    limit = max(1, min(int(limit or DEFAULT_CURRENT_SESSION_TURNS), 20))
    max_chars = max(200, min(int(max_chars or DEFAULT_CURRENT_SESSION_MAX_CHARS), 12000))
    terms = _normalize_query(query)

    indexed = list(enumerate(turns))
    if terms:
        ranked = [
            (idx, turn, _score_turn_for_query(turn, terms))
            for idx, turn in indexed
        ]
        matches = [(idx, turn) for idx, turn, score in ranked if score > 0]
        tail = indexed[-min(3, limit):]
        merged: dict[int, dict] = {idx: turn for idx, turn in matches[-limit:]}
        merged.update({idx: turn for idx, turn in tail})
        selected = sorted(merged.items())[-limit:]
    else:
        selected = indexed[-limit:]

    if not selected and turns:
        selected = indexed[-limit:]

    chunks: list[str] = []
    payload_turns: list[dict] = []
    for idx, turn in selected:
        text = _format_turn_for_recall(turn)
        if not text:
            continue
        payload_turns.append(
            {
                "turn_index": idx,
                "ts": turn.get("ts"),
                "user": turn.get("user") or "",
                "jarvis": turn.get("jarvis") or "",
            }
        )
        chunks.append(f"### Turno {idx + 1}\n{text}")

    summary = _limit_text("\n\n".join(chunks).strip(), max_chars) if chunks else ""
    latest = turns[-1] if turns else {}
    return {
        "query": query or "",
        "found": len(payload_turns),
        "total_turns": len(turns),
        "source": "current_session_journal",
        "summary": summary,
        "turns": payload_turns,
        "latest_user": latest.get("user") or "",
        "latest_jarvis": latest.get("jarvis") or "",
    }


def build_current_session_block(
    journal: SessionJournal,
    *,
    limit: int = DEFAULT_CURRENT_SESSION_TURNS,
    max_chars: int = DEFAULT_CURRENT_SESSION_MAX_CHARS,
) -> str:
    """Build a compact live-session block for reconnect/system prompt restore."""
    recall = current_session_recall(
        journal,
        query="",
        limit=limit,
        max_chars=max_chars,
    )
    if not recall["summary"]:
        return ""
    bar = "=" * 11
    return (
        f"{bar} CONTEXTO VIVO DE ESTA SESION {bar}\n"
        f"{recall['summary']}\n"
        f"{bar}{bar}\n"
        "Este bloque restaura continuidad tras reconexion/compresion. "
        "Si Isaac dice 'lo que veniamos hablando', 'lo que te dije' o "
        "'hace rato', prioriza este contexto antes de memorias de ayer."
    )


def synthesize_and_save(
    journal: SessionJournal,
    reasoner,
    vault: ObsidianVault,
    min_turns: int,
    session_id: str,
) -> Path | None:
    """Synthesize pending journal turns into a dated session note."""
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
        return None

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
    body = f"# Sesion {now.strftime('%Y-%m-%d %H:%M')}\n\n{synthesized}\n"

    try:
        notes_mod.write_note(
            vault,
            path,
            body=body,
            frontmatter=frontmatter,
            tags=["jarvis-session", "session-journal"],
        )
    except Exception:
        return None

    journal.clear()
    return path


def _sessions_dir(vault: ObsidianVault) -> Path:
    return vault.memory_path / SESSIONS_SUBDIR


def _session_files(vault: ObsidianVault) -> list[Path]:
    base = _sessions_dir(vault)
    if not base.exists():
        return []
    return sorted(base.glob("*_sesion.md"), reverse=True)


def _session_date_from_name(path: Path) -> date | None:
    try:
        return datetime.strptime(path.name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _session_title_from_name(path: Path) -> str:
    return path.stem.replace("_sesion", "").replace("_", " ")


def _extract_recall_sections(text: str) -> str:
    """Return Resumen + Pendientes, excluding frontmatter and Proyectos."""
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


def _session_payload(vault: ObsidianVault, path: Path, max_chars: int) -> dict | None:
    try:
        vault.assert_readable(path)
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    sections = _extract_recall_sections(text)
    if not sections:
        return None
    rel = path.relative_to(vault.vault_path)
    session_date = _session_date_from_name(path)
    return {
        "title": _session_title_from_name(path),
        "path": str(rel),
        "date": session_date.isoformat() if session_date else None,
        "summary": sections[:max_chars],
    }


def load_last_summary(vault: ObsidianVault, max_chars: int) -> str | None:
    """Return the latest synthesized session summary."""
    files = _session_files(vault)
    if not files:
        return None
    payload = _session_payload(vault, files[0], max_chars)
    if payload is None:
        return None
    return payload["summary"]


def load_recent_summaries(
    vault: ObsidianVault,
    limit: int = DEFAULT_RECENT_LIMIT,
    max_chars_each: int = DEFAULT_SESSION_MAX_CHARS,
) -> list[dict]:
    """Return recent synthesized session notes using cheap file reads only."""
    limit = max(1, min(int(limit or DEFAULT_RECENT_LIMIT), 10))
    max_chars_each = max(120, min(int(max_chars_each or DEFAULT_SESSION_MAX_CHARS), 2000))
    out: list[dict] = []
    for path in _session_files(vault):
        payload = _session_payload(vault, path, max_chars_each)
        if payload is None:
            continue
        out.append(payload)
        if len(out) >= limit:
            break
    return out


def _normalize_query(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9_\-]{3,}", (text or "").lower())
    stop = {
        "que",
        "con",
        "del",
        "los",
        "las",
        "una",
        "uno",
        "para",
        "ayer",
        "hoy",
        "sesion",
        "conversacion",
        "pasada",
        "anterior",
        "recuerda",
        "recordar",
        "tuvimos",
        "hablamos",
    }
    return [word for word in words if word not in stop]


def _target_date_from_when(when: str, today: date | None = None) -> date | None:
    today = today or datetime.now().date()
    value = (when or "").strip().lower()
    if not value:
        return None
    if "anteayer" in value:
        return today - timedelta(days=2)
    if "ayer" in value or "anoche" in value:
        return today - timedelta(days=1)
    if "hoy" in value:
        return today
    iso = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", value)
    if iso:
        try:
            return datetime.strptime(iso.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None
    slash = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](20\d{2}))?\b", value)
    if slash:
        day = int(slash.group(1))
        month = int(slash.group(2))
        year = int(slash.group(3) or today.year)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return None


def search_session_summaries(
    vault: ObsidianVault,
    query: str = "",
    when: str = "",
    limit: int = DEFAULT_RECENT_LIMIT,
    max_chars_each: int = DEFAULT_SESSION_MAX_CHARS,
    today: date | None = None,
) -> dict:
    """Search session summaries by relative date and lightweight keywords."""
    limit = max(1, min(int(limit or DEFAULT_RECENT_LIMIT), 10))
    target_date = _target_date_from_when(when or query, today=today)
    terms = _normalize_query(query)
    candidates = load_recent_summaries(vault, limit=25, max_chars_each=max_chars_each)
    matches: list[dict] = []
    date_candidates: list[dict] = []

    for item in candidates:
        item_date = None
        if item.get("date"):
            try:
                item_date = datetime.strptime(item["date"], "%Y-%m-%d").date()
            except ValueError:
                item_date = None
        if target_date is not None and item_date != target_date:
            continue
        if target_date is not None:
            date_candidates.append(dict(item))

        haystack = f"{item.get('title', '')}\n{item.get('summary', '')}".lower()
        score = sum(1 for term in terms if term in haystack)
        if terms and score == 0:
            continue

        ranked = dict(item)
        ranked["score"] = score
        matches.append(ranked)

    if not matches and target_date is None and not terms:
        matches = candidates[:limit]
    if not matches and target_date is not None:
        matches = date_candidates[:limit]

    matches.sort(key=lambda item: (item.get("score", 0), item.get("path", "")), reverse=True)
    return {
        "query": query,
        "when": when,
        "target_date": target_date.isoformat() if target_date else None,
        "found": len(matches[:limit]),
        "sessions": matches[:limit],
    }


def build_recall_block(summary: str | None) -> str:
    """Wrap a single previous-session summary for the system prompt."""
    if not summary or not summary.strip():
        return ""
    bar = "=" * 11
    return (
        f"{bar} CONTEXTO DE SESION ANTERIOR {bar}\n"
        f"{summary.strip()}\n"
        f"{bar}{bar}\n"
        "(Usa esto solo si Isaac retoma algo de la sesion previa; "
        "no lo recites sin que venga al caso.)"
    )


def build_recent_recall_block(summaries: list[dict]) -> str:
    """Build a compact startup map from recent sessions."""
    if not summaries:
        return ""
    chunks = []
    for item in summaries:
        date_text = item.get("date") or "sin fecha"
        title = item.get("title") or "sesion"
        summary = (item.get("summary") or "").strip()
        if not summary:
            continue
        chunks.append(f"### {date_text} - {title}\n{summary}")
    if not chunks:
        return ""
    bar = "=" * 11
    return (
        f"{bar} MAPA DE SESIONES RECIENTES {bar}\n"
        + "\n\n".join(chunks)
        + "\n"
        + f"{bar}{bar}\n"
        + (
            "Usa este mapa solo para continuidad. Si Isaac dice ayer, anoche, "
            "la sesion pasada o la conversacion anterior, llama "
            "jarvis_session_recall antes de responder."
        )
    )
