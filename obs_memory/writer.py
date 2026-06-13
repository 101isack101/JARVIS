"""Obsidian writer for OBS episodic memory."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[<>:\"/\\|?*]+", "_", name or "OBS Session")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:100] or "OBS Session"


class OBSMemoryWriter:
    def __init__(self, vault: ObsidianVault, output_folder: str) -> None:
        self.vault = vault
        self.output_folder = output_folder.strip().strip("/\\") or "obs_sessions"

    def write_session(
        self,
        *,
        title: str,
        markdown: str,
        source_video: Path,
        transcript_path: Path | None,
        frame_paths: list[Path],
        duration_s: int,
        used_reasoner: bool,
        retention: str,
    ) -> Path:
        now = datetime.now()
        fname = f"{now.strftime('%Y-%m-%d_%H%M')}_{safe_filename(title)}.md"
        path = self.vault.memory_path / self.output_folder / fname
        body = (
            f"# {title}\n\n"
            "## Metadata\n"
            f"- Captured: `{now.isoformat(timespec='seconds')}`\n"
            f"- Source video: `{source_video.name}`\n"
            f"- Duration: `{duration_s}s`\n"
            f"- Reasoner synthesis: `{used_reasoner}`\n"
            f"- Retention: `{retention}`\n"
        )
        if transcript_path is not None:
            body += f"- Transcript artifact: `{transcript_path.name}`\n"
        if frame_paths:
            body += "- Keyframes:\n" + "".join(f"  - `{p.name}`\n" for p in frame_paths)
        body += "\n" + markdown.strip() + "\n"
        notes_mod.write_note(
            self.vault,
            path,
            body=body,
            frontmatter={
                "type": "obs-episode",
                "project": "[[03-PROJECTS/jarvis]]",
                "date": now.strftime("%Y-%m-%d"),
                "source": "OBS Studio",
                "source_video": source_video.name,
                "duration_s": duration_s,
                "generated_by": "jarvis-obs-memory",
                "reasoner_used": used_reasoner,
            },
            tags=["jarvis-obs", "episodic-memory"],
        )
        return path
