"""Fachada del motor de proactividad (Fase 3, runtime).

Único punto de contacto entre jarvis.py y el motor determinista. Encapsula
config + OpportunityQueue + pipeline (states → signals → ingest → render/peek).
Fail-safe absoluto: ningún método propaga excepciones.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
        except Exception:
            return ""
        return render_briefing(opps, top_k=self.config.briefing_top_k)

    def observe(self, vault, rag, turn_text: str) -> None:
        if not self.config.enabled or not (turn_text or "").strip():
            return
        try:
            states = build_project_states(vault)
            self.queue.ingest(detect_contextual_signals(turn_text, states, rag, self.config))
        except Exception:
            pass

    def next_opportunity(self) -> dict | None:
        if not self.config.enabled:
            return None
        try:
            opp = self.queue.top_opportunity()
        except Exception:
            return None
        if opp is None:
            return None
        self.queue.mark_offered(opp.id)
        self._last_offered_id = opp.id
        return opp.suggestion_struct

    def dismiss_last(self) -> None:
        if self._last_offered_id:
            try:
                self.queue.mark_dismissed(self._last_offered_id)
            except Exception:
                pass
            self._last_offered_id = None
