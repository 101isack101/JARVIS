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


def _format_rag(results: list) -> tuple[str, int]:
    kept = [r for r in results if getattr(r, "score", 0.0) >= MIN_RAG_SCORE]
    if not kept:
        return "", 0
    lines = []
    for r in kept:
        snippet = " ".join((r.chunk.text or "").split())
        if len(snippet) > 220:
            snippet = snippet[:217].rstrip() + "..."
        source = getattr(r.chunk, "source_type", "")
        title = getattr(r.chunk, "title", "")
        prefix = f"{source}:{title}" if source and title else title or source
        label = f" {prefix}" if prefix else ""
        lines.append(f"- [score {r.score:.2f}{label}] {snippet}")
    return "\n".join(lines), len(kept)


def _section_cost(header: str, content: str) -> int:
    return estimate_tokens(f"## {header}\n{content.strip()}\n\n")


def build_project_context(
    vault: ObsidianVault,
    rag: VaultRAG,
    prompt: str,
    *,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    semantic_memory=None,
    curator=None,
) -> ContextResult:
    project = triage_mod.detect_project(prompt or "")
    if not project:
        return ContextResult()

    # Candidatos en orden de prioridad: (label, header, content)
    candidates: list[tuple[str, str, str]] = []

    card = _load_card_body(vault, project)
    if card:
        candidates.append(("card", "Memory Card", card))

    from . import session_summary  # import local: evita ciclo en import time

    try:
        recall = session_summary.load_last_summary(vault, RECALL_MAX_CHARS)
    except Exception:
        recall = None
    if recall and recall.strip():
        candidates.append(("session", "Sesión anterior", recall))

    try:
        searcher = semantic_memory or rag
        rag_results = searcher.search(prompt, top_k=RAG_TOP_K)
        if curator is not None:
            rag_results = curator.rerank(rag_results)
            curator.note_retrieval(prompt, rag_results)
    except Exception:
        rag_results = []
    rag_text, rag_count = _format_rag(rag_results)
    if rag_text:
        candidates.append((f"rag:{rag_count}", "Memorias relacionadas", rag_text))

    sections: list[tuple[str, str]] = []
    sources: list[str] = []
    used = 0
    for label, header, content in candidates:
        cost = _section_cost(header, content)
        if not sections:
            # Primera sección siempre entra; si excede, se trunca al presupuesto.
            if cost > token_budget:
                content = content[: token_budget * 4]
            sections.append((header, content))
            sources.append(label)
            used += _section_cost(header, content)
            continue
        if used + cost > token_budget:
            continue  # descarta esta sección (RAG cae primero por ir al final)
        sections.append((header, content))
        sources.append(label)
        used += cost

    if not sections:
        return ContextResult(text="", project=project, sources=[])
    return ContextResult(text=_wrap(project, sections), project=project, sources=sources)
