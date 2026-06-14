"""Fachada fail-safe del motor de auto-mejora de conocimiento.

Único punto de contacto con jarvis.py. Ningún método propaga excepción: un fallo
aquí jamás puede tumbar el cierre de sesión. Presupuestado: sin budget, solo corre
el camino determinista.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import confidence as conf_mod
from . import metrics as metrics_mod
from . import review_log as log_mod
from .config import KnowledgeImproverConfig
from .detectors import detect_contradictions, detect_duplicate_clusters
from .events import MemoryEvent
from .judge import judge_merge
from .proposer import to_signals

EventLoader = Callable[[object], list[MemoryEvent]]


class KnowledgeImprover:
    def __init__(
        self,
        *,
        config: KnowledgeImproverConfig,
        embed_fn,
        reasoner=None,
        proactivity_engine=None,
        event_loader: EventLoader | None = None,
    ) -> None:
        self.config = config
        self.embed_fn = embed_fn
        self.reasoner = reasoner
        self.proactivity_engine = proactivity_engine
        self._event_loader = event_loader or _default_event_loader

    def run(self, vault) -> None:
        if not self.config.enabled:
            return
        try:
            self._run_inner(vault)
        except Exception:
            pass

    def _run_inner(self, vault) -> None:
        try:
            events = self._event_loader(vault)
        except Exception:
            return
        if not events:
            return

        events = [self._reweigh(e) for e in events]

        clusters = detect_duplicate_clusters(
            events, self.embed_fn,
            threshold=self.config.sim_threshold, min_size=self.config.min_cluster_size,
        )
        contradictions = detect_contradictions(events)

        budget = self.config.token_budget
        verdicts = []
        for cluster in clusters:
            v = judge_merge(self.reasoner, cluster, token_budget=budget)
            if v is not None:
                verdicts.append(v)
                budget = max(0, budget - 300)

        project_by_members = {e.id: e.project for e in events}
        signals = to_signals(verdicts, contradictions, project_by_members=project_by_members)
        if signals and self.proactivity_engine is not None:
            try:
                self.proactivity_engine.queue.ingest(signals)
            except Exception:
                pass

        memory_path = Path(vault.memory_path)
        actions = [
            f"eventos={len(events)}",
            f"clusters={len(clusters)}",
            f"contradicciones={len(contradictions)}",
            f"propuestas={len(signals)}",
        ]
        try:
            health = metrics_mod.compute_health(events, clusters, contradictions)
            metrics_mod.write_health(memory_path, health)
        except Exception:
            pass
        try:
            log_mod.append_review_log(memory_path, actions)
        except Exception:
            pass

    def _reweigh(self, ev: MemoryEvent) -> MemoryEvent:
        conf = conf_mod.decayed(
            conf_mod.reinforce(ev.confidence, times=ev.reinforced),
            ev.learned_at, half_life_days=self.config.decay_half_life_days,
        )
        return MemoryEvent(
            id=ev.id, text=ev.text, section=ev.section, project=ev.project,
            source=ev.source, learned_at=ev.learned_at, confidence=conf,
            reinforced=ev.reinforced, superseded_by=ev.superseded_by,
        )


def _default_event_loader(vault) -> list[MemoryEvent]:
    """Lee las Project Memory Cards del vault y devuelve sus eventos.

    Importa perezosamente para no acoplar el módulo a Obsidian en los tests.
    """
    from memory import notes as notes_mod
    from memory.triage import PROJECT_CARD_FOLDER

    from .events import parse_bullet

    out: list[MemoryEvent] = []
    folder = Path(vault.memory_path) / PROJECT_CARD_FOLDER
    if not folder.exists():
        return out
    for card_path in sorted(folder.glob("*.md")):
        try:
            note = notes_mod.read_note(vault, card_path)
        except Exception:
            continue
        project = note.frontmatter.get("project") or card_path.stem
        section = "Notes"
        for line in note.body.splitlines():
            stripped = line.strip()
            if stripped.startswith("## "):
                section = stripped[3:].strip()
                continue
            ev = parse_bullet(line, section=section, project=str(project))
            if ev is not None:
                out.append(ev)
    return out
