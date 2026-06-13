"""Render del briefing proactivo de arranque (Fase 3).

Produce un bloque ESTRUCTURADO para el system_prompt. La narración la hace
Gemini (prompt-first): el bloque entrega datos, no una frase ya hecha.
"""

from __future__ import annotations

from .opportunity_queue import Opportunity

_BAR = "═" * 11


def _line(opp: Opportunity) -> str:
    s = opp.suggestion_struct
    why = s.get("why_now") or {}
    detail = why.get("pending") or why.get("decision") or why.get("snippet") or ""
    detail = " ".join(str(detail).split())
    if len(detail) > 140:
        detail = detail[:137].rstrip() + "..."
    tag = opp.signal.kind
    return f"- [{s.get('project')}] ({tag}) {detail}".rstrip()


def render_briefing(opportunities: list[Opportunity], *, top_k: int = 3) -> str:
    top = [o for o in opportunities][: max(0, top_k)]
    if not top:
        return ""
    lines = [f"{_BAR} BRIEFING PROACTIVO {_BAR}"]
    lines.extend(_line(o) for o in top)
    lines.append(f"{_BAR}{_BAR}")
    lines.append("(Menciónalo solo si encaja al abrir; no recites la lista. Una sugerencia, no un informe.)")
    return "\n".join(lines)
