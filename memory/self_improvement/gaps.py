"""Detección de lagunas de conocimiento (KSI Fase 2).

Tres detectores deterministas sobre el estado del vault + el reasoner para
formular preguntas naturales. Reusa confianza y contradicciones de Fase 1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from . import confidence as conf_mod
from .detectors import detect_contradictions


@dataclass(frozen=True)
class KnowledgeGap:
    gap_id: str
    kind: str
    project: str
    key: str
    context: str = ""
    question: str = ""


def gap_id(kind: str, project: str, key: str) -> str:
    raw = f"{kind}|{project}|{key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _make(kind: str, project: str, key: str, context: str) -> KnowledgeGap:
    return KnowledgeGap(
        gap_id=gap_id(kind, project, key),
        kind=kind, project=project, key=key, context=context,
    )


def detect_poor_cards(states, cfg) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for st in states:
        if getattr(st, "staleness_days", None) is None:
            continue  # proyecto sin actividad reciente: no molestar
        total = len(st.open_pendings) + len(st.open_decisions) + len(st.current_state)
        if not st.current_state or total < getattr(cfg, "min_card_bullets", 4):
            out.append(_make(
                "poor_card", st.project, st.project,
                f"El proyecto {st.project} tiene la Memory Card casi vacía.",
            ))
    return out


def detect_stale_facts(events, cfg, *, today=None) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for ev in events:
        if ev.superseded_by:
            continue
        decayed = conf_mod.decayed(
            ev.confidence, ev.learned_at,
            half_life_days=cfg.decay_half_life_days, today=today,
        )
        if decayed < getattr(cfg, "stale_confidence", 0.3):
            out.append(_make(
                "stale_fact", ev.project, ev.id,
                f"Hecho sin reconfirmar hace tiempo: {ev.text}",
            ))
    return out


def detect_open_contradictions(events) -> list[KnowledgeGap]:
    out: list[KnowledgeGap] = []
    for a, b in detect_contradictions(events):
        key = "|".join(sorted([a.id, b.id]))
        out.append(_make(
            "open_contradiction", a.project, key,
            f"Contradicción sin resolver: '{a.text}' vs '{b.text}'",
        ))
    return out


def collect_gaps(states, events, cfg, *, today=None) -> list[KnowledgeGap]:
    gaps: list[KnowledgeGap] = []
    gaps.extend(detect_poor_cards(states, cfg))
    gaps.extend(detect_stale_facts(events, cfg, today=today))
    gaps.extend(detect_open_contradictions(events))
    return gaps
