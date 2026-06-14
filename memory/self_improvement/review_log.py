"""Traza auditable append-only de cada corrida del improver."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

REVIEW_LOG_PATH = "self-improvement/review-log.md"


def append_review_log(memory_path: Path, actions: list[str]) -> Path:
    path = Path(memory_path) / REVIEW_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    block = [f"\n## {stamp}"]
    block.extend(f"- {a}" for a in (actions or ["(sin acciones)"]))
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(block) + "\n")
    return path
