"""Ensamblado de contexto de proyecto para el reasoner (Fase 2).

Reúne Project Memory Card + resumen de sesión previa + memorias RAG y los
entrega como un bloque de texto que ask_claude_deep concatena al context_extra.

Determinista y fail-safe: corre antes de cada llamada al reasoner, así que
debe ser barato y nunca propagar excepciones que rompan el razonamiento.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import triage as triage_mod
from .obsidian_vault import ObsidianVault
from .rag import VaultRAG

DEFAULT_TOKEN_BUDGET = 2500
RECALL_MAX_CHARS = 1200
MIN_RAG_SCORE = 0.25
RAG_TOP_K = 3
BAR = "═" * 11


@dataclass(frozen=True)
class ContextResult:
    text: str = ""
    project: str | None = None
    sources: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Heurística barata (~4 chars/token). Evita dependencia de tiktoken."""
    return len(text) // 4


def build_project_context(
    vault: ObsidianVault,
    rag: VaultRAG,
    prompt: str,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> ContextResult:
    project = triage_mod.detect_project(prompt or "")
    if not project:
        return ContextResult()
    return ContextResult(text="", project=project, sources=[])
