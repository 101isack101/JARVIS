"""Métricas de salud de la memoria. Sin esto, 'recursivo' es fe, no ingeniería."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .events import MemoryEvent

HEALTH_PATH = "self-improvement/health.md"


def compute_health(events, clusters, contradictions) -> dict:
    total = len(events)
    projects = len({e.project for e in events})
    avg_conf = round(sum(e.confidence for e in events) / total, 4) if total else 0.0
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total_events": total,
        "projects": projects,
        "duplicate_clusters": len(clusters),
        "open_contradictions": len(contradictions),
        "avg_confidence": avg_conf,
    }


def write_health(memory_path: Path, health: dict) -> Path:
    path = Path(memory_path) / HEALTH_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Salud de la memoria (KSI)", "", "```json",
            json.dumps(health, ensure_ascii=False, indent=2), "```", ""]
    path.write_text("\n".join(body), encoding="utf-8")
    return path
