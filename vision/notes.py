"""Nota de visión: texto analizado, imagen citada, secretos fuera."""

from __future__ import annotations

import re
from datetime import datetime

from security.secret_filter import redact_secrets

_NOTE_ROOT = "JARVIS/01-CAPTURES/vision"


def vision_note_relpath(title: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    raw = (title or "captura").replace("\\", " ").replace("/", " ")
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:40] or "captura"
    return f"{_NOTE_ROOT}/{when:%Y-%m-%d-%H%M%S}-{slug}.md"


def build_vision_note(
    *,
    source: str,
    image_ref: str,
    reason: str,
    analysis: str,
    title: str,
) -> str:
    safe_title = redact_secrets(title or "Captura visual").replace("\n", " ").strip() or "Captura visual"
    safe_analysis = redact_secrets(analysis or "").strip()
    safe_reason = redact_secrets(reason or "").strip()
    safe_image = _image_name(image_ref)
    return (
        f"# {safe_title}\n\n"
        f"Fuente: {source}\n\n"
        "## Analisis\n\n"
        f"{safe_analysis}\n\n"
        "## Captura\n\n"
        f"Imagen: `{safe_image}`\n"
        f"Motivo: {safe_reason or 'sin motivo'}\n"
    )


def _image_name(image_ref: str) -> str:
    name = (image_ref or "").replace("\\", "/").split("/")[-1]
    return name or "sin-imagen"
