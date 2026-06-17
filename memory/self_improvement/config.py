"""Configuración del motor de auto-mejora de conocimiento (KSI).

Mismo patrón que proactivity/config.py: frozen dataclass + from_env con defaults
seguros. Prefijo de entorno: JARVIS_KSI_.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class KnowledgeImproverConfig:
    enabled: bool = True
    sim_threshold: float = 0.86         # coseno mínimo para considerar duplicado
    token_budget: int = 1500            # tokens máximos para el reasoner por corrida
    decay_half_life_days: int = 45      # vida media del decaimiento de confianza
    min_cluster_size: int = 2           # tamaño mínimo de cluster de duplicados
    min_card_bullets: int = 4           # umbral de "card pobre"
    stale_confidence: float = 0.3       # confianza decaída bajo la cual un hecho es "obsoleto"
    rag_curation_enabled: bool = False  # re-ranking del RAG por uso real (gate JARVIS_RAG_CURATION)
    use_threshold: float = 0.55         # coseno minimo respuesta<->chunk para contar "usado"
    cold_start_min: int = 5             # recuperaciones minimas antes de salir de factor neutral
    factor_floor: float = 0.6           # multiplicador minimo del score en rerank
    factor_ceil: float = 1.4            # multiplicador maximo del score en rerank
    usage_decay_days: int = 45          # vida media para decaer cuentas en housekeeping

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KnowledgeImproverConfig":
        env = env if env is not None else os.environ
        d = cls()

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
            enabled=_bool("JARVIS_KSI_ENABLED", d.enabled),
            sim_threshold=_float("JARVIS_KSI_SIM_THRESHOLD", d.sim_threshold),
            token_budget=_int("JARVIS_KSI_TOKEN_BUDGET", d.token_budget),
            decay_half_life_days=_int("JARVIS_KSI_DECAY_HALF_LIFE_DAYS", d.decay_half_life_days),
            min_cluster_size=_int("JARVIS_KSI_MIN_CLUSTER_SIZE", d.min_cluster_size),
            min_card_bullets=_int("JARVIS_KSI_MIN_CARD_BULLETS", d.min_card_bullets),
            stale_confidence=_float("JARVIS_KSI_STALE_CONFIDENCE", d.stale_confidence),
            rag_curation_enabled=_bool("JARVIS_RAG_CURATION", d.rag_curation_enabled),
            use_threshold=_float("JARVIS_KSI_USE_THRESHOLD", d.use_threshold),
            cold_start_min=_int("JARVIS_KSI_COLD_START_MIN", d.cold_start_min),
            factor_floor=_float("JARVIS_KSI_FACTOR_FLOOR", d.factor_floor),
            factor_ceil=_float("JARVIS_KSI_FACTOR_CEIL", d.factor_ceil),
            usage_decay_days=_int("JARVIS_KSI_USAGE_DECAY_DAYS", d.usage_decay_days),
        )
