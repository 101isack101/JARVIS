"""Fachada del motor de proactividad (Fase 3, runtime).

Único punto de contacto entre jarvis.py y el motor determinista. Encapsula
config + OpportunityQueue + pipeline (states → signals → ingest → render/peek).
Fail-safe absoluto: ningún método propaga excepciones.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from telemetry.error_journal import record_error

from .briefing import render_briefing
from .config import ProactivityConfig
from .opportunity_queue import OpportunityQueue
from .project_state import build_project_states
from .signals import detect_contextual_signals, detect_startup_signals


class ProactivityEngine:
    def __init__(self, *, config: ProactivityConfig, state_path: Path) -> None:
        self.config = config
        self.queue = OpportunityQueue(Path(state_path), config=config)
        self._last_offered_id: str | None = None

    def build_briefing(self, vault, *, today: date | None = None) -> str:
        if not self.config.enabled:
            return ""
        try:
            states = build_project_states(vault, today=today)
            self.queue.ingest(detect_startup_signals(states, self.config))
            opps = self.queue.peek_top(self.config.briefing_top_k)
        except Exception as exc:
            record_error(
                "proactivity.build_briefing",
                exc=exc,
                severity="warning",
                message="Fallo al construir el briefing proactivo; se devuelve vacío",
            )
            return ""
        return render_briefing(opps, top_k=self.config.briefing_top_k)

    def observe(self, vault, rag, turn_text: str, semantic_memory=None) -> None:
        if not self.config.enabled or not (turn_text or "").strip():
            return
        try:
            states = build_project_states(vault)
            searcher = semantic_memory or rag
            self.queue.ingest(detect_contextual_signals(turn_text, states, searcher, self.config))
        except Exception as exc:
            record_error(
                "proactivity.observe",
                exc=exc,
                severity="warning",
                message="Fallo al observar el turno para señales proactivas; se ignora",
                context={"turn_chars": len(turn_text)},
            )

    def next_opportunity(self) -> dict | None:
        if not self.config.enabled:
            return None
        try:
            opp = self.queue.top_opportunity()
            if opp is None:
                return None
            self.queue.mark_offered(opp.id)
            self._last_offered_id = opp.id
            return opp.suggestion_struct
        except Exception as exc:
            record_error(
                "proactivity.next_opportunity",
                exc=exc,
                severity="warning",
                message="Fallo al obtener la siguiente oportunidad proactiva; se devuelve None",
            )
            return None

    def dismiss_last(self) -> None:
        if self._last_offered_id:
            try:
                self.queue.mark_dismissed(self._last_offered_id)
            except Exception as exc:
                record_error(
                    "proactivity.dismiss_last",
                    exc=exc,
                    severity="warning",
                    message="Fallo al descartar la última oportunidad ofrecida; se ignora",
                    context={"opportunity_id": self._last_offered_id},
                )
            self._last_offered_id = None
