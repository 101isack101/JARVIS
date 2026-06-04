"""Snapshot determinista de estado por proyecto (Fase 3).

Deriva, por proyecto conocido, un ProjectState a partir de:
- la Project Memory Card (secciones Pending / Decisions / Current State),
- las notas de sesión (última fecha que mencionó el proyecto).

Solo lee archivos; sin LLM, sin embeddings. Fail-safe por proyecto.
"""

from __future__ import annotations

from dataclasses import dataclass
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
