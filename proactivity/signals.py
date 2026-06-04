"""Detectores deterministas: convierten ProjectState (+ contexto del turno) en Signals.

Reglas puras, sin estado y sin LLM. Las señales de ARRANQUE no necesitan
contexto conversacional; las CONTEXTUALES reciben el texto del turno y el RAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import ProactivityConfig
from .project_state import ProjectState

_IMPORTANCE_RANK = {"low": 0, "normal": 1, "high": 2}


@dataclass(frozen=True)
class Signal:
    kind: str          # stale_pending | stale_project | open_loop | cross_project | ctx_pending
    project: str
    payload: dict
    base_priority: float
    evidence: list[str] = field(default_factory=list)


def _stale_pending(states: list[ProjectState], cfg: ProactivityConfig) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        if st.staleness_days is None or st.staleness_days < cfg.stale_pending_days:
            continue
        if not st.open_pendings:
            continue
        out.append(
            Signal(
                kind="stale_pending",
                project=st.project,
                payload={"pending": st.open_pendings[0], "days": st.staleness_days},
                base_priority=0.6,
                evidence=[f"card:{st.project}"],
            )
        )
    return out


def _stale_project(states: list[ProjectState], cfg: ProactivityConfig) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        if st.staleness_days is None or st.staleness_days < cfg.stale_project_days:
            continue
        if _IMPORTANCE_RANK.get(st.importance, 1) < _IMPORTANCE_RANK["normal"]:
            continue
        out.append(
            Signal(
                kind="stale_project",
                project=st.project,
                payload={"days": st.staleness_days, "importance": st.importance},
                base_priority=0.45,
                evidence=[f"card:{st.project}"],
            )
        )
    return out


def _open_loop(states: list[ProjectState], cfg: ProactivityConfig) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        # decisión registrada pero el proyecto lleva tiempo sin tocarse:
        # bucle abierto (algo se decidió y no avanzó).
        if not st.open_decisions:
            continue
        if st.staleness_days is None or st.staleness_days < cfg.stale_project_days:
            continue
        out.append(
            Signal(
                kind="open_loop",
                project=st.project,
                payload={"decision": st.open_decisions[0], "days": st.staleness_days},
                base_priority=0.5,
                evidence=[f"card:{st.project}"],
            )
        )
    return out


def detect_startup_signals(
    states: list[ProjectState], cfg: ProactivityConfig
) -> list[Signal]:
    """Señales que no necesitan contexto conversacional (briefing de arranque)."""
    signals: list[Signal] = []
    signals.extend(_stale_pending(states, cfg))
    signals.extend(_stale_project(states, cfg))
    signals.extend(_open_loop(states, cfg))
    return signals
