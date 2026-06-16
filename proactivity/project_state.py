"""Snapshot determinista de estado por proyecto (Fase 3).

Deriva, por proyecto conocido, un ProjectState a partir de:
- la Project Memory Card (secciones Pending / Decisions / Current State),
- las notas de sesión (última fecha que mencionó el proyecto).

Solo lee archivos; sin LLM, sin embeddings. Fail-safe por proyecto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

PLACEHOLDER = "- (pending)"


def parse_card_sections(body: str) -> dict[str, list[str]]:
    """Devuelve {nombre_seccion: [linea_bullet, ...]} para cada `## Heading`.

    Las líneas de bullet conservan su texto pero sin el marcador inicial `- `.
    El placeholder de sección vacía `- (pending)` se descarta.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        stripped = line.strip()
        if not stripped or stripped == PLACEHOLDER:
            continue
        if stripped.startswith("- "):
            sections[current].append(stripped[2:].strip())
    return sections


def section_bullets(sections: dict[str, list[str]], name: str) -> list[str]:
    return list(sections.get(name, []))


def _open_questions_from_body(body: str) -> list[dict]:
    """Preguntas abiertas (no resueltas) de la card, como [{"text","gap_id"}]."""
    from memory.self_improvement.gaps import parse_questions_section
    out: list[dict] = []
    for gid, rec in parse_questions_section(body).items():
        if rec.get("status") == "open":
            out.append({"text": rec.get("display", ""), "gap_id": gid})
    return out


from datetime import datetime
from pathlib import Path

from memory import notes as notes_mod
from memory import triage as triage_mod
from memory.obsidian_vault import ObsidianVault

SESSIONS_SUBDIR = "sessions"


@dataclass(frozen=True)
class ProjectState:
    project: str
    last_touched: date | None
    staleness_days: int | None
    open_pendings: list[str]
    open_decisions: list[str]
    current_state: list[str]
    importance: str
    confidence: str
    open_questions: list[dict] = field(default_factory=list)


def _extract_section_text(body: str, heading: str) -> str:
    """Devuelve el texto crudo bajo `## heading` hasta el siguiente `## `."""
    out: list[str] = []
    capture = False
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            capture = line[3:].strip().lower() == heading.lower()
            continue
        if capture:
            out.append(line)
    return "\n".join(out)


def _session_files(vault: ObsidianVault) -> list[Path]:
    base = vault.memory_path / SESSIONS_SUBDIR
    if not base.exists():
        return []
    return sorted(base.glob("*_sesion.md"), reverse=True)


def _session_date(path: Path) -> date | None:
    try:
        return datetime.strptime(path.name[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _last_touched_map(vault: ObsidianVault) -> dict[str, date]:
    """Para cada proyecto conocido, la fecha de la sesión más reciente que lo
    mencionó en su sección `## Proyectos tocados`. Determinista por aliases."""
    touched: dict[str, date] = {}
    for path in _session_files(vault):
        sdate = _session_date(path)
        if sdate is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        haystack = _extract_section_text(text, "Proyectos tocados").lower()
        if not haystack.strip():
            continue
        for project, aliases in triage_mod.PROJECT_ALIASES.items():
            if any(alias.lower() in haystack for alias in aliases):
                if project not in touched or sdate > touched[project]:
                    touched[project] = sdate
    return touched


def _load_card(vault: ObsidianVault, project: str):
    try:
        path = triage_mod.project_card_path(vault, project)
    except Exception:
        return None
    if not path.exists():
        return None
    try:
        return notes_mod.read_note(vault, path)
    except Exception:
        return None


def build_project_states(
    vault: ObsidianVault, *, today: date | None = None
) -> list[ProjectState]:
    today = today or date.today()
    touched = _last_touched_map(vault)

    states: list[ProjectState] = []
    for project in triage_mod.PROJECT_ALIASES:
        note = _load_card(vault, project)
        last = touched.get(project)
        if note is None and last is None:
            continue  # ni card ni sesión: el proyecto no existe para el motor

        if note is not None:
            sections = parse_card_sections(note.body or "")
            importance = str(note.frontmatter.get("importance", "normal"))
            confidence = str(note.frontmatter.get("confidence", "medium"))
        else:
            sections = {}
            importance, confidence = "normal", "medium"

        staleness = (today - last).days if last is not None else None
        open_questions = _open_questions_from_body(note.body or "") if note is not None else []
        states.append(
            ProjectState(
                project=project,
                last_touched=last,
                staleness_days=staleness,
                open_pendings=section_bullets(sections, "Pending"),
                open_decisions=section_bullets(sections, "Decisions"),
                current_state=section_bullets(sections, "Current State"),
                importance=importance,
                confidence=confidence,
                open_questions=open_questions,
            )
        )
    return states
