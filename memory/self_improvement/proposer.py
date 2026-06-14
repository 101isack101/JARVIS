"""Convierte veredictos destructivos en Signals para la OpportunityQueue (HITL).

Nada se aplica aquí: solo se PROPONE. El usuario aprueba en el morning briefing.
El payload usa la clave "snippet" porque es la que opportunity_id y briefing._line leen.
"""

from __future__ import annotations

from proactivity.signals import Signal

from .events import MemoryEvent
from .judge import MergeVerdict


def to_signals(
    merges: list[MergeVerdict],
    contradictions: list[tuple[MemoryEvent, MemoryEvent]],
    *,
    project_by_members: dict[str, str],
) -> list[Signal]:
    out: list[Signal] = []
    for v in merges:
        if not v.is_true_duplicate:
            continue
        project = next((project_by_members.get(mid, "") for mid in v.member_ids), "")
        out.append(
            Signal(
                kind="memory_merge",
                project=project or "general",
                payload={"snippet": v.canonical_text, "members": list(v.member_ids)},
                base_priority=0.7,
                evidence=[f"merge:{','.join(v.member_ids)}"],
            )
        )
    for a, b in contradictions:
        out.append(
            Signal(
                kind="memory_supersede",
                project=a.project or "general",
                payload={"snippet": f"{a.text}  ⟷  {b.text}", "members": [a.id, b.id]},
                base_priority=0.65,
                evidence=[f"contradiction:{a.id},{b.id}"],
            )
        )
    return out
