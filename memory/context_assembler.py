"""Ensamblado de contexto de proyecto para el reasoner (Fase 2).

Reúne Project Memory Card + resumen de sesión previa + memorias RAG y los
entrega como un bloque de texto que ask_claude_deep concatena al context_extra.

Determinista y fail-safe: corre antes de cada llamada al reasoner, así que
debe ser barato y nunca propagar excepciones que rompan el razonamiento.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import notes as notes_mod
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


def _load_card_body(vault: ObsidianVault, project: str) -> str:
    try:
        path = triage_mod.project_card_path(vault, project)
    except Exception:
        return ""
    if not path.exists():
        return ""
    try:
        note = notes_mod.read_note(vault, path)
    except Exception:
        return ""
    return (note.body or "").strip()


def _wrap(project: str, sections: list[tuple[str, str]]) -> str:
    parts = [f"{BAR} CONTEXTO DE PROYECTO: {project} {BAR}"]
    for header, content in sections:
        parts.append(f"## {header}\n{content.strip()}")
    parts.append(f"{BAR}{BAR}")
    parts.append("(Contexto recuperado automáticamente por JARVIS. Úsalo solo si viene al caso.)")
    return "\n\n".join(parts)


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

    sections: list[tuple[str, str]] = []
    sources: list[str] = []

    card = _load_card_body(vault, project)
    if card:
        sections.append(("Memory Card", card))
        sources.append("card")

    if not sections:
        return ContextResult(text="", project=project, sources=[])

    return ContextResult(text=_wrap(project, sections), project=project, sources=sources)
