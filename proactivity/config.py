"""Configuración del motor de proactividad, leída de entorno con defaults seguros."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProactivityConfig:
    enabled: bool = True
    stale_pending_days: int = 7
    stale_project_days: int = 14
    max_per_session: int = 3
    cooldown_days: int = 7
    briefing_top_k: int = 3
    min_score: float = 0.35

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProactivityConfig":
        env = env if env is not None else os.environ
        d = cls()  # defaults

        def _bool(key: str, default: bool) -> bool:
            raw = env.get(key)
            if raw is None:
                return default
            return raw.strip().lower() in _TRUE

        def _int(key: str, default: int) -> int:
            try:
                return int(str(env.get(key, default)).strip())
            except (ValueError, TypeError):
                return default

        def _float(key: str, default: float) -> float:
            try:
                return float(str(env.get(key, default)).strip())
            except (ValueError, TypeError):
                return default

        return cls(
            enabled=_bool("JARVIS_PROACTIVITY_ENABLED", d.enabled),
            stale_pending_days=_int("JARVIS_PROACTIVITY_STALE_PENDING_DAYS", d.stale_pending_days),
            stale_project_days=_int("JARVIS_PROACTIVITY_STALE_PROJECT_DAYS", d.stale_project_days),
            max_per_session=_int("JARVIS_PROACTIVITY_MAX_PER_SESSION", d.max_per_session),
            cooldown_days=_int("JARVIS_PROACTIVITY_COOLDOWN_DAYS", d.cooldown_days),
            briefing_top_k=_int("JARVIS_PROACTIVITY_BRIEFING_TOP_K", d.briefing_top_k),
            min_score=_float("JARVIS_PROACTIVITY_MIN_SCORE", d.min_score),
        )
