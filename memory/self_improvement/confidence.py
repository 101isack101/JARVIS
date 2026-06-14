"""Confianza numérica: mapeo desde el esquema legado high/medium/low, decaimiento
temporal con vida media, y refuerzo por reconfirmación.
"""

from __future__ import annotations

from datetime import date, datetime

_LEGACY = {"high": 0.85, "medium": 0.6, "low": 0.35}
_DEFAULT = 0.6
_REINFORCE_STEP = 0.05


def legacy_to_float(word: str) -> float:
    return _LEGACY.get((word or "").strip().lower(), _DEFAULT)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def decayed(confidence: float, learned_at: str, *, half_life_days: int, today: str | None = None) -> float:
    learned = _parse_date(learned_at)
    now = _parse_date(today) if today else date.today()
    if learned is None or now is None or half_life_days <= 0:
        return confidence
    age = (now - learned).days
    if age <= 0:
        return confidence
    factor = 0.5 ** (age / half_life_days)
    return round(confidence * factor, 4)


def reinforce(confidence: float, *, times: int) -> float:
    bumps = max(0, int(times) - 1)
    return round(min(1.0, confidence + _REINFORCE_STEP * bumps), 4)
