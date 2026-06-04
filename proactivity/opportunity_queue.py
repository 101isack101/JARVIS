"""Priorización y anti-spam de oportunidades proactivas (Fase 3).

Convierte Signals en Opportunities puntuadas, deduplica dentro de la sesión,
aplica cooldown entre sesiones y persiste el historial en JSON. El "aprendizaje"
de qué ignora Isaac es un contador determinista, sin ML.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import ProactivityConfig
from .signals import Signal


@dataclass(frozen=True)
class Opportunity:
    id: str
    signal: Signal
    score: float
    suggestion_struct: dict


def opportunity_id(signal: Signal) -> str:
    """Hash estable por (kind, project, payload-clave) para dedup/cooldown."""
    key = (
        signal.payload.get("pending")
        or signal.payload.get("decision")
        or signal.payload.get("snippet")
        or ""
    )
    raw = f"{signal.kind}|{signal.project}|{key}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


_WHAT_BY_KIND = {
    "stale_pending": "Retomar un pendiente que lleva días abierto",
    "stale_project": "Volver a un proyecto importante sin tocar",
    "open_loop": "Cerrar una decisión que no avanzó",
    "cross_project": "Reutilizar algo ya resuelto en otro proyecto",
    "ctx_pending": "Hay un pendiente abierto del proyecto que mencionaste",
}


def _suggestion_struct(signal: Signal) -> dict:
    return {
        "what": _WHAT_BY_KIND.get(signal.kind, "Oportunidad"),
        "project": signal.project,
        "why_now": signal.payload,
        "evidence": list(signal.evidence),
        "action_hint": signal.kind,
    }


def _score(signal: Signal) -> float:
    return round(signal.base_priority, 4)


class OpportunityQueue:
    def __init__(self, state_path: Path, *, config: ProactivityConfig) -> None:
        self.state_path = Path(state_path)
        self.config = config
        self._history = self._load()           # {id: {offered_at, dismissed_at, count}}
        self._offered_this_session: set[str] = set()
        self._candidates: list[Opportunity] = []

    # ---- persistencia ----
    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}  # archivo ausente o corrupto: se reinicia sin romper

    def _save(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._history, indent=2), encoding="utf-8")
        except Exception:
            pass  # fail-safe: no persistir no es un error fatal

    # ---- ingest / consulta ----
    def ingest(self, signals: list[Signal]) -> None:
        for sig in signals:
            score = _score(sig)
            if score < self.config.min_score:
                continue
            opp = Opportunity(
                id=opportunity_id(sig),
                signal=sig,
                score=score,
                suggestion_struct=_suggestion_struct(sig),
            )
            self._candidates.append(opp)

    def _in_cooldown(self, opp_id: str, now: datetime) -> bool:
        rec = self._history.get(opp_id)
        if not rec:
            return False
        dismissed = rec.get("dismissed_at")
        if not dismissed:
            return False
        try:
            when = datetime.fromisoformat(dismissed)
        except (ValueError, TypeError):
            return False
        return (now - when) < timedelta(days=self.config.cooldown_days)

    def top_opportunity(self, *, now: datetime | None = None) -> Opportunity | None:
        now = now or datetime.now()
        if len(self._offered_this_session) >= self.config.max_per_session:
            return None
        ranked = sorted(self._candidates, key=lambda o: o.score, reverse=True)
        for opp in ranked:
            if opp.id in self._offered_this_session:
                continue
            if self._in_cooldown(opp.id, now):
                continue
            return opp
        return None

    def mark_offered(self, opp_id: str) -> None:
        self._offered_this_session.add(opp_id)
        rec = self._history.setdefault(opp_id, {})
        rec["offered_at"] = datetime.now().isoformat()
        rec["count"] = int(rec.get("count", 0)) + 1
        self._save()

    def mark_dismissed(self, opp_id: str) -> None:
        rec = self._history.setdefault(opp_id, {})
        rec["dismissed_at"] = datetime.now().isoformat()
        self._save()
