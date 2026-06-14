"""Juicio del reasoner SOLO sobre candidatos del detector. Presupuestado y
fail-safe: cualquier fallo o falta de budget devuelve None (se omite la fusión).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .events import MemoryEvent

_INSTRUCTIONS = (
    "Eres el bibliotecario de JARVIS. Te paso varias memorias que un detector marcó "
    "como POSIBLES duplicados. Decide si dicen lo MISMO. Responde SOLO un objeto JSON: "
    '{"is_true_duplicate": true|false, "canonical_text": "<texto fusionado conciso>"}. '
    "Si no son el mismo hecho, is_true_duplicate=false y canonical_text=\"\"."
)


@dataclass(frozen=True)
class MergeVerdict:
    is_true_duplicate: bool
    canonical_text: str
    member_ids: list[str]


def _extract_json(text: str) -> dict | None:
    """Primer objeto JSON balanceado dentro del texto (self-heal básico)."""
    s = text or ""
    start = s.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = s.find("{", start + 1)
    return None


def judge_merge(reasoner, cluster: list[MemoryEvent], *, token_budget: int, max_tokens: int = 300):
    if reasoner is None or token_budget <= 0 or len(cluster) < 2:
        return None
    payload = "\n".join(f"- ({e.id}) {e.text}" for e in cluster)
    try:
        resp = reasoner.ask(_INSTRUCTIONS, context_extra="MEMORIAS:\n" + payload, max_tokens=max_tokens)
        data = _extract_json(getattr(resp, "text", "") or "")
    except Exception:
        return None
    if not isinstance(data, dict) or "is_true_duplicate" not in data:
        return None
    return MergeVerdict(
        is_true_duplicate=bool(data.get("is_true_duplicate")),
        canonical_text=str(data.get("canonical_text") or "").strip(),
        member_ids=[e.id for e in cluster],
    )
