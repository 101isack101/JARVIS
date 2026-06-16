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


def _knowledge_gap(states, cfg) -> list[Signal]:
    out: list[Signal] = []
    for st in states:
        for q in getattr(st, "open_questions", []):
            out.append(
                Signal(
                    kind="knowledge_gap",
                    project=st.project,
                    payload={"snippet": q["text"], "gap_id": q["gap_id"]},
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
    signals.extend(_knowledge_gap(states, cfg))
    return signals


from memory import triage as triage_mod

CROSS_MIN_SCORE = 0.35


def _ctx_pending(active_project: str | None, states: list[ProjectState]) -> list[Signal]:
    if not active_project:
        return []
    by_name = {st.project: st for st in states}
    st = by_name.get(active_project)
    if st is None or not st.open_pendings:
        return []
    return [
        Signal(
            kind="ctx_pending",
            project=active_project,
            payload={"pending": st.open_pendings[0]},
            base_priority=0.7,
            evidence=[f"card:{active_project}"],
        )
    ]


def _ctx_knowledge_gap(active_project, states) -> list[Signal]:
    if not active_project:
        return []
    by_name = {st.project: st for st in states}
    st = by_name.get(active_project)
    if st is None:
        return []
    out: list[Signal] = []
    for q in getattr(st, "open_questions", []):
        out.append(
            Signal(
                kind="knowledge_gap",
                project=active_project,
                payload={"snippet": q["text"], "gap_id": q["gap_id"]},
                base_priority=0.72,
                evidence=[f"card:{active_project}"],
            )
        )
    return out


def _cross_project(turn_text: str, active_project: str | None, rag) -> list[Signal]:
    try:
        results = rag.search(turn_text, top_k=3)
    except Exception:
        return []
    out: list[Signal] = []
    for r in results or []:
        if getattr(r, "score", 0.0) < CROSS_MIN_SCORE:
            continue
        title = getattr(r.chunk, "title", "") or ""
        rel = getattr(r.chunk, "rel_path", "") or ""
        # heurística: si la nota relevante NO es del proyecto activo, es una
        # conexión cross-proyecto (intuición).
        if active_project and active_project.lower() in (title + " " + rel).lower():
            continue
        snippet = " ".join((r.chunk.text or "").split())[:200]
        out.append(
            Signal(
                kind="cross_project",
                project=active_project or title,
                payload={"snippet": snippet, "source_title": title},
                base_priority=0.55,
                evidence=[rel or title],
            )
        )
        break  # una conexión cross por turno basta (anti-ruido)
    return out


def detect_contextual_signals(
    turn_text: str,
    states: list[ProjectState],
    rag,
    cfg: ProactivityConfig,
) -> list[Signal]:
    """Señales que dependen del texto del turno (detección en tiempo real)."""
    active = triage_mod.detect_project(turn_text or "")
    signals: list[Signal] = []
    signals.extend(_ctx_pending(active, states))
    signals.extend(_cross_project(turn_text or "", active, rag))
    signals.extend(_ctx_knowledge_gap(active, states))
    return signals
