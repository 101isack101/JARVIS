"""Obsidian persistence for JARVIS Study Mode."""

from __future__ import annotations

import re
from pathlib import Path

from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault

from .ledger import now_iso


class StudyObsidianWriter:
    def __init__(self, vault: ObsidianVault) -> None:
        self.vault = vault

    def resolve_note_path(self, note_path: str | None, session_title: str) -> Path:
        rel = (note_path or "").strip().replace("\\", "/").strip("/")
        if not rel:
            rel = f"Study Mode/{_safe_filename(session_title)}.md"
        if not rel.lower().endswith(".md"):
            rel += ".md"
        target = (self.vault.memory_path / rel).resolve()
        self.vault.assert_writable(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def ensure_note(self, path: Path, session_title: str, source_hint: str = "") -> None:
        if path.exists():
            return
        body = (
            f"# {session_title}\n\n"
            "## Session\n\n"
            f"- Created: `{now_iso()}`\n"
            f"- Source: {source_hint or 'Study Mode'}\n"
            "- Owner: JARVIS Study Mode\n"
        )
        notes_mod.write_note(
            self.vault,
            path,
            body=body,
            tags=["jarvis-study", "second-brain"],
        )

    def append_synthesis(self, path: Path, markdown: str, section_title: str = "Jarvis Study Notes") -> dict:
        note = notes_mod.append_section(
            self.vault,
            path,
            section_title=section_title,
            content=markdown,
        )
        return {
            "ok": True,
            "path": str(path.relative_to(self.vault.vault_path)),
            "title": note.title,
        }


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", name or "Study Session")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120] or "Study Session"
