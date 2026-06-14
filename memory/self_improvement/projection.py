"""Regeneración aditiva de Project Memory Cards desde eventos + snapshots.

La card es OUTPUT: se reconstruye desde los eventos, nunca se parchea in-place.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .events import MemoryEvent, serialize_bullet

SNAPSHOT_SUBDIR = "self-improvement/snapshots"
# Orden canónico de secciones (igual que triage.initial_project_card_body)
_SECTION_ORDER = [
    "Objective", "Current State", "Facts", "Decisions", "Pending",
    "Procedures", "Preferences", "Learning Notes", "Risks", "Notes", "Sources",
]


def snapshot_previous(memory_path: Path, card_path: Path) -> Path | None:
    card_path = Path(card_path)
    if not card_path.exists():
        return None
    snap_dir = Path(memory_path) / SNAPSHOT_SUBDIR
    snap_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = snap_dir / f"{card_path.stem}_{ts}.md"
    shutil.copy2(card_path, dest)
    return dest


def rebuild_card_body(project: str, events: list[MemoryEvent]) -> str:
    by_section: dict[str, list[MemoryEvent]] = {}
    for ev in events:
        by_section.setdefault(ev.section, []).append(ev)

    ordered = list(_SECTION_ORDER)
    for section in by_section:
        if section not in ordered:
            ordered.append(section)

    lines = [f"# {project} - Memory Card", ""]
    for section in ordered:
        evs = by_section.get(section)
        if not evs:
            continue
        evs = sorted(evs, key=lambda e: e.confidence, reverse=True)
        lines.append(f"## {section}")
        lines.append("")
        lines.extend(serialize_bullet(ev) for ev in evs)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
