"""Memory triage and Project Memory Cards for Jarvis.

This module is deliberately deterministic. It runs inside memory writes, so it
must be cheap, predictable, and safe to execute during a voice turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from security.secret_filter import redact_secrets

from . import notes as notes_mod
from .obsidian_vault import ObsidianVault

PROJECT_CARD_FOLDER = "Project Memory Cards"

PROJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "JARVIS": ("jarvis",),
    "Agentics_Code_Team": ("agentics", "agentics_code_team", "agentics code team"),
    "Course_Capture": ("course_capture", "course capture", "course-capture"),
    "Interview_Copilot": ("interview_copilot", "interview copilot"),
    "LinkedIn Copilot": ("linkedin copilot", "linkedin_copilot", "linkedin-job-agent"),
    "n8n Lead Ingestion": ("n8n", "lead ingestion", "whatsapp b2b"),
    "Polymath IDE": ("polymath", "polymath ide"),
    "MTurk HITL Agent": ("mturk", "mtturk", "hitl agent"),
    "ai-news-agent": ("ai-news", "ai news", "ai-news-agent"),
}

SECTION_BY_KIND = {
    "decision": "Decisions",
    "todo": "Pending",
    "procedural": "Procedures",
    "preference": "Preferences",
    "study": "Learning Notes",
    "project_fact": "Facts",
    "semantic": "Notes",
}


@dataclass(frozen=True)
class MemoryTriage:
    should_save: bool
    memory_kind: str
    importance: str
    confidence: str
    tags: list[str]
    project: str | None = None
    reason: str = ""
    target_title: str | None = None

    @property
    def updates_project_card(self) -> bool:
        return self.project is not None and self.memory_kind in {
            "decision",
            "todo",
            "procedural",
            "preference",
            "study",
            "project_fact",
            "semantic",
        }


def triage_memory(title: str, content: str, tags: list[str] | None = None) -> MemoryTriage:
    clean_tags = normalize_tags(tags)
    raw = "\n".join([title or "", content or "", " ".join(clean_tags)])
    if not (content or "").strip():
        return MemoryTriage(
            should_save=False,
            memory_kind="empty",
            importance="none",
            confidence="high",
            tags=clean_tags,
            reason="empty-content",
        )
    if redact_secrets(raw) != raw:
        return MemoryTriage(
            should_save=False,
            memory_kind="sensitive",
            importance="blocked",
            confidence="high",
            tags=sorted(set(clean_tags + ["sensitive"])),
            reason="secret-like-content",
        )

    memory_kind = infer_memory_kind(clean_tags, raw)
    project = detect_project(raw)
    importance = infer_importance(memory_kind, raw)
    confidence = infer_confidence(raw)
    enriched_tags = sorted(set(clean_tags + ["jarvis-memory", memory_kind] + ([project_tag(project)] if project else [])))
    return MemoryTriage(
        should_save=True,
        memory_kind=memory_kind,
        importance=importance,
        confidence=confidence,
        tags=enriched_tags,
        project=project,
        reason="classified",
        target_title=title.strip() or None,
    )


def normalize_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    for tag in tags or []:
        clean = str(tag).strip().strip("#").lower().replace(" ", "-")
        if clean:
            out.append(clean)
    return sorted(set(out))


def infer_memory_kind(tags: list[str], text: str) -> str:
    haystack = " ".join(tags + [text.lower()])
    if any(k in haystack for k in ("preference", "preferencia", "prefiero", "estilo")):
        return "preference"
    if any(k in haystack for k in ("decision", "decidimos", "decidido", "rationale", "tradeoff")):
        return "decision"
    if any(k in haystack for k in ("todo", "pending", "pendiente", "retomar", "follow-up")):
        return "todo"
    if any(k in haystack for k in ("procedure", "procedimiento", "runbook", "comando", "paso")):
        return "procedural"
    if any(k in haystack for k in ("study", "aprendizaje", "flashcard", "curso", "estudio")):
        return "study"
    if any(k in haystack for k in ("project", "proyecto")) or detect_project(haystack):
        return "project_fact"
    return "semantic"


def infer_importance(memory_kind: str, text: str) -> str:
    haystack = text.lower()
    if any(k in haystack for k in ("importante", "critico", "crítico", "no olvidar", "recuerda esto")):
        return "high"
    if memory_kind in {"decision", "preference", "todo"}:
        return "high"
    if memory_kind in {"procedural", "project_fact"}:
        return "normal"
    return "low"


def infer_confidence(text: str) -> str:
    haystack = text.lower()
    if any(k in haystack for k in ("decidimos", "prefiero", "recuerda", "no olvidar", "queda pendiente")):
        return "high"
    if any(k in haystack for k in ("creo", "quizas", "quizás", "tal vez", "posible")):
        return "low"
    return "medium"


def detect_project(text: str) -> str | None:
    haystack = text.lower()
    for project, aliases in PROJECT_ALIASES.items():
        if any(alias.lower() in haystack for alias in aliases):
            return project
    return None


def project_tag(project: str) -> str:
    return "project-" + re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-")


def project_card_path(vault: ObsidianVault, project: str) -> Path:
    path = vault.memory_path / PROJECT_CARD_FOLDER / f"{safe_filename(project)}.md"
    vault.assert_writable(path)
    return path


def update_project_memory_card(
    vault: ObsidianVault,
    triage: MemoryTriage,
    *,
    source_title: str,
    content: str,
) -> dict:
    if not triage.updates_project_card or not triage.project:
        return {"updated": False, "reason": "no-project-card-target"}

    path = project_card_path(vault, triage.project)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        note = notes_mod.read_note(vault, path)
    else:
        note = notes_mod.write_note(
            vault,
            path,
            body=initial_project_card_body(triage.project),
            frontmatter={
                "type": "project-memory-card",
                "project": triage.project,
                "importance": "high",
                "confidence": "medium",
            },
            tags=["project-memory-card", project_tag(triage.project)],
        )

    section = SECTION_BY_KIND.get(triage.memory_kind, "Notes")
    bullet = project_card_bullet(
        source_title=source_title,
        content=content,
        importance=triage.importance,
        confidence=triage.confidence,
    )
    body = append_bullet_to_section(note.body, section, bullet)
    note.frontmatter["project"] = triage.project
    note.frontmatter["type"] = "project-memory-card"
    note.frontmatter["last_confirmed"] = now_iso()
    existing_tags = note.tags
    note.frontmatter["tags"] = sorted(set(existing_tags + ["project-memory-card", project_tag(triage.project)]))
    saved = notes_mod.write_note(vault, path, body, note.frontmatter)
    return {
        "updated": True,
        "path": str(path.relative_to(vault.vault_path)),
        "title": saved.title,
        "section": section,
    }


def initial_project_card_body(project: str) -> str:
    return (
        f"# {project} - Memory Card\n\n"
        "## Objective\n\n"
        "- (pending)\n\n"
        "## Current State\n\n"
        "- (pending)\n\n"
        "## Facts\n\n"
        "- (pending)\n\n"
        "## Decisions\n\n"
        "- (pending)\n\n"
        "## Pending\n\n"
        "- (pending)\n\n"
        "## Procedures\n\n"
        "- (pending)\n\n"
        "## Preferences\n\n"
        "- (pending)\n\n"
        "## Learning Notes\n\n"
        "- (pending)\n\n"
        "## Risks\n\n"
        "- (pending)\n\n"
        "## Sources\n\n"
        "- Generated by JARVIS memory triage.\n"
    )


def append_bullet_to_section(body: str, section: str, bullet: str) -> str:
    heading = f"## {section}"
    lines = body.rstrip().splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        lines.extend(["", heading, "", bullet])
        return "\n".join(lines).rstrip() + "\n"

    insert_at = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            insert_at = i
            break
    section_lines = lines[start + 1 : insert_at]
    if "- (pending)" in [line.strip() for line in section_lines]:
        pending_idx = start + 1 + next(i for i, line in enumerate(section_lines) if line.strip() == "- (pending)")
        lines.pop(pending_idx)
        insert_at -= 1
    lines.insert(insert_at, bullet)
    return "\n".join(lines).rstrip() + "\n"


def project_card_bullet(source_title: str, content: str, importance: str, confidence: str) -> str:
    summary = " ".join((content or "").strip().split())
    if len(summary) > 220:
        summary = summary[:217].rstrip() + "..."
    return f"- {today()} [{importance}/{confidence}] {summary} (source: [[{source_title}]])"


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", name or "Project")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120] or "Project"


def today() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
