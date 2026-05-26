"""Preferencias persistentes de comportamiento para Jarvis.

Estas preferencias viven en data/preferences.json para que no dependan solo
del prompt base. El archivo se crea/actualiza al arranque con defaults seguros.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PREFERENCES: dict[str, Any] = {
    "obsidian_notes": {
        "granular_by_default": True,
        "rule": (
            "Cuando documentes aprendizaje o temas tecnicos, crea una nota "
            "separada por tema especifico y conectala con wikilinks. Evita "
            "meter varios temas nuevos en una nota principal salvo que Isaac "
            "lo pida explicitamente."
        ),
    },
    "voice_experience": {
        "shorter_answers_by_default": False,
        "rule": (
            "No aplicar la politica de respuestas mas cortas por defecto; "
            "mantener el criterio actual de concision natural segun contexto."
        ),
    },
}


def ensure_runtime_preferences(path: Path) -> dict[str, Any]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefs = _deep_copy(DEFAULT_PREFERENCES)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                prefs = _deep_merge(prefs, loaded)
        except json.JSONDecodeError:
            backup = path.with_suffix(path.suffix + ".broken")
            try:
                path.replace(backup)
            except OSError:
                pass
    path.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
    return prefs


def preferences_prompt_block(prefs: dict[str, Any]) -> str:
    obsidian_rule = prefs.get("obsidian_notes", {}).get(
        "rule",
        DEFAULT_PREFERENCES["obsidian_notes"]["rule"],
    )
    voice_rule = prefs.get("voice_experience", {}).get(
        "rule",
        DEFAULT_PREFERENCES["voice_experience"]["rule"],
    )
    return (
        "═══════════ PREFERENCIAS PERSISTENTES DE ISAAC ═══════════\n\n"
        f"- Obsidian: {obsidian_rule}\n"
        f"- Voz: {voice_rule}"
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_merge(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _deep_copy(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))
