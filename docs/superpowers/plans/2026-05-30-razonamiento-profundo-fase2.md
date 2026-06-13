# Fase 2 — Razonamiento Profundo con Auto-Contexto — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que `ask_claude_deep` inyecte automáticamente contexto de proyecto (Project Memory Card + resumen de sesión previa + RAG top-3) cuando detecta un proyecto en el prompt, sin intervención del usuario.

**Architecture:** Módulo aislado `memory/context_assembler.py` con `build_project_context(vault, rag, prompt)` que reusa `triage.detect_project`, `triage.project_card_path`, `session_summary.load_last_summary` y `VaultRAG.search`. El handler `ask_claude_deep` lo llama y concatena el resultado al `context_extra` del modelo (auto-contexto al final). Fail-safe: nunca rompe el razonamiento.

**Tech Stack:** Python 3.11, pytest, dataclasses. Sin dependencias nuevas (estimación de tokens por heurística `len//4`).

**Convenciones del repo:**
- Tests se corren con: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest <ruta> -v`
- Patrón de test: vault temporal `ObsidianVault(tmp_path, read_all=True)` + `FakeRAG`.
- Firmas confirmadas: `SearchResult(chunk, score)`, `Chunk(chunk_id, rel_path, title, text, para_idx)`, `Note(path, frontmatter, body)` con `.title`/`.tags`, `read_note(vault, path)`, `vault.memory_path`, `triage.detect_project(text)`, `triage.project_card_path(vault, project)`, `session_summary.load_last_summary(vault, max_chars)`.

---

## File Structure

| Archivo | Responsabilidad |
|---------|-----------------|
| `memory/context_assembler.py` | **nuevo** — `ContextResult`, `estimate_tokens`, `build_project_context` y helpers de ensamblado |
| `memory/tools.py` | modificar `ask_claude_deep` y `ask_claude_deep_async` para llamar al assembler y mergear contexto |
| `tests/test_context_assembler.py` | **nuevo** — tests unitarios del assembler |
| `tests/test_ask_claude_deep_context.py` | **nuevo** — test de integración del merge en el handler |

---

## Task 1: Esqueleto del assembler — sin proyecto detectado devuelve vacío

**Files:**
- Create: `memory/context_assembler.py`
- Test: `tests/test_context_assembler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_assembler.py
from memory.context_assembler import ContextResult, build_project_context, estimate_tokens
from memory.obsidian_vault import ObsidianVault


class FakeRAG:
    def __init__(self, results=None):
        self.results = results or []
        self.queries = []

    def search(self, query, top_k=3):
        self.queries.append((query, top_k))
        return self.results[:top_k]


def test_estimate_tokens_uses_char_heuristic():
    assert estimate_tokens("a" * 40) == 10


def test_no_project_detected_returns_empty(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()

    result = build_project_context(vault, rag, "¿qué hora es en Tokio?")

    assert isinstance(result, ContextResult)
    assert result.text == ""
    assert result.project is None
    assert result.sources == []
    assert rag.queries == []  # sin proyecto, ni siquiera consulta RAG
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'memory.context_assembler'`

- [ ] **Step 3: Write minimal implementation**

```python
# memory/context_assembler.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_context_assembler.py
git commit -m "feat(context): esqueleto build_project_context + estimate_tokens"
```

---

## Task 2: Incluir la Project Memory Card

**Files:**
- Modify: `memory/context_assembler.py`
- Test: `tests/test_context_assembler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_assembler.py  (añadir)
from memory import notes as notes_mod
from memory import triage as triage_mod


def _write_card(vault, project, body):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    notes_mod.write_note(vault, path, body=body, frontmatter={"type": "project-memory-card"})
    return path


def test_includes_project_card_when_present(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- revisar server TS\n")

    result = build_project_context(vault, rag, "ayúdame con Polymath IDE y el server")

    assert result.project == "Polymath IDE"
    assert "card" in result.sources
    assert "revisar server TS" in result.text
    assert "CONTEXTO DE PROYECTO: Polymath IDE" in result.text


def test_missing_card_does_not_crash(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()

    result = build_project_context(vault, rag, "ayúdame con Polymath IDE")

    assert result.project == "Polymath IDE"
    assert "card" not in result.sources
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: FAIL — `test_includes_project_card_when_present` falla en `"revisar server TS" in result.text` (text vacío)

- [ ] **Step 3: Write minimal implementation**

Reemplaza `build_project_context` y añade helpers:

```python
# memory/context_assembler.py
from . import notes as notes_mod


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_context_assembler.py
git commit -m "feat(context): incluir Project Memory Card en el auto-contexto"
```

---

## Task 3: Incluir el resumen de la sesión previa (recall Fase 1)

**Files:**
- Modify: `memory/context_assembler.py`
- Test: `tests/test_context_assembler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_assembler.py  (añadir)
from memory import session_summary


def _write_session(vault, name, text):
    base = vault.memory_path / "Sesiones"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(text, encoding="utf-8")


def test_includes_session_recall_when_present(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG()
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")
    _write_session(
        vault,
        "2026-05-29_2100_sesion.md",
        "# Sesión\n\n## Resumen\nTrabajamos en el editor Monaco.\n\n## Pendientes\n- conectar el agente\n",
    )

    result = build_project_context(vault, rag, "sigamos con Polymath IDE")

    assert "session" in result.sources
    assert "Monaco" in result.text or "conectar el agente" in result.text
    assert "Sesión anterior" in result.text
```

Nota: el directorio de sesiones lo determina `session_summary._sessions_dir`. Antes de escribir el test, el implementador DEBE confirmar el nombre real de la subcarpeta leyendo `memory/session_summary.py` (`_sessions_dir` y el glob `*_sesion.md`) y ajustar `_write_session` para que coincida exactamente.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py::test_includes_session_recall_when_present -v`
Expected: FAIL — `"session" in result.sources` es False

- [ ] **Step 3: Write minimal implementation**

Añade el bloque de recall en `build_project_context`, después del bloque de la card y antes del `if not sections`:

```python
# memory/context_assembler.py  — dentro de build_project_context, tras añadir la card:
    from . import session_summary  # import local: evita ciclo en import time

    try:
        recall = session_summary.load_last_summary(vault, RECALL_MAX_CHARS)
    except Exception:
        recall = None
    if recall and recall.strip():
        sections.append(("Sesión anterior", recall))
        sources.append("session")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_context_assembler.py
git commit -m "feat(context): incluir recall de sesión previa en el auto-contexto"
```

---

## Task 4: Incluir RAG top-3 con filtro de score

**Files:**
- Modify: `memory/context_assembler.py`
- Test: `tests/test_context_assembler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_assembler.py  (añadir)
from types import SimpleNamespace


def _result(score, text, title="Nota", rel_path="Jarvis Memory/Nota.md"):
    return SimpleNamespace(
        score=score,
        chunk=SimpleNamespace(title=title, rel_path=rel_path, text=text),
    )


def test_includes_rag_chunks_above_threshold(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG(results=[
        _result(0.80, "Decidimos usar DynamoDB para estado."),
        _result(0.60, "Optimistic locking con version field."),
        _result(0.10, "Ruido irrelevante por debajo del umbral."),
    ])
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")

    result = build_project_context(vault, rag, "Polymath IDE estado y locking")

    assert any(s.startswith("rag:") for s in result.sources)
    assert "DynamoDB" in result.text
    assert "Optimistic locking" in result.text
    assert "Ruido irrelevante" not in result.text  # filtrado por MIN_RAG_SCORE
    assert rag.queries == [("Polymath IDE estado y locking", 3)]


def test_rag_only_no_card_no_session_still_returns_context(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG(results=[_result(0.90, "Algo muy relevante sobre Polymath.")])

    result = build_project_context(vault, rag, "Polymath IDE dudas")

    assert result.project == "Polymath IDE"
    assert any(s.startswith("rag:") for s in result.sources)
    assert "Algo muy relevante" in result.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py::test_includes_rag_chunks_above_threshold -v`
Expected: FAIL — `any(s.startswith("rag:") ...)` es False

- [ ] **Step 3: Write minimal implementation**

Añade el helper de formato RAG y el bloque en `build_project_context` después del recall:

```python
# memory/context_assembler.py
def _format_rag(results: list) -> tuple[str, int]:
    kept = [r for r in results if getattr(r, "score", 0.0) >= MIN_RAG_SCORE]
    if not kept:
        return "", 0
    lines = []
    for r in kept:
        snippet = " ".join((r.chunk.text or "").split())
        if len(snippet) > 220:
            snippet = snippet[:217].rstrip() + "..."
        lines.append(f"- [score {r.score:.2f}] {snippet}")
    return "\n".join(lines), len(kept)
```

```python
# memory/context_assembler.py  — dentro de build_project_context, tras el recall:
    try:
        rag_results = rag.search(prompt, top_k=RAG_TOP_K)
    except Exception:
        rag_results = []
    rag_text, rag_count = _format_rag(rag_results)
    if rag_text:
        sections.append(("Memorias relacionadas", rag_text))
        sources.append(f"rag:{rag_count}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_context_assembler.py
git commit -m "feat(context): incluir RAG top-3 con filtro de score en el auto-contexto"
```

---

## Task 5: Presupuesto de tokens — truncar RAG primero, conservar la card

**Files:**
- Modify: `memory/context_assembler.py`
- Test: `tests/test_context_assembler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_assembler.py  (añadir)
def test_budget_drops_rag_before_card(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    big_chunk = "X" * 4000  # ~1000 tokens, muy por encima de un budget chico
    rag = FakeRAG(results=[_result(0.90, big_chunk)])
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- conservar esto\n")

    result = build_project_context(vault, rag, "Polymath IDE", token_budget=120)

    assert "conservar esto" in result.text          # la card sobrevive
    assert "card" in result.sources
    assert not any(s.startswith("rag:") for s in result.sources)  # RAG se descartó
    assert estimate_tokens(result.text) <= 120 + estimate_tokens(_wrap("Polymath IDE", []))


def test_budget_generous_keeps_everything(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = FakeRAG(results=[_result(0.90, "memoria relevante")])
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")

    result = build_project_context(vault, rag, "Polymath IDE", token_budget=5000)

    assert "card" in result.sources
    assert any(s.startswith("rag:") for s in result.sources)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py::test_budget_drops_rag_before_card -v`
Expected: FAIL — el RAG grande no se descarta, `rag:` sigue en sources

- [ ] **Step 3: Write minimal implementation**

Refactoriza el ensamblado para que las secciones se añadan respetando presupuesto, en orden de prioridad (card → session → rag). Reemplaza el cuerpo de `build_project_context` que añade secciones por esta versión con control de presupuesto:

```python
# memory/context_assembler.py
def _section_cost(header: str, content: str) -> int:
    return estimate_tokens(f"## {header}\n{content.strip()}\n\n")


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

    # Candidatos en orden de prioridad: (label, header, content)
    candidates: list[tuple[str, str, str]] = []

    card = _load_card_body(vault, project)
    if card:
        candidates.append(("card", "Memory Card", card))

    from . import session_summary
    try:
        recall = session_summary.load_last_summary(vault, RECALL_MAX_CHARS)
    except Exception:
        recall = None
    if recall and recall.strip():
        candidates.append(("session", "Sesión anterior", recall))

    try:
        rag_results = rag.search(prompt, top_k=RAG_TOP_K)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_context_assembler.py
git commit -m "feat(context): presupuesto de tokens, descarta RAG antes que la card"
```

---

## Task 6: Fail-safe — una fuente que lanza no rompe el ensamblado

**Files:**
- Test: `tests/test_context_assembler.py`

Nota: la implementación ya envuelve cada fuente en try/except (Tasks 2-5). Esta tarea
añade un test que **prueba la garantía** con un RAG que lanza.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_context_assembler.py  (añadir)
class ExplodingRAG:
    def search(self, query, top_k=3):
        raise RuntimeError("boom")


def test_rag_failure_does_not_break_assembly(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(vault, "Polymath IDE", "# Polymath IDE - Memory Card\n\n## Pending\n- algo\n")

    result = build_project_context(vault, ExplodingRAG(), "Polymath IDE")

    assert "card" in result.sources          # la card sobrevive
    assert not any(s.startswith("rag:") for s in result.sources)
    assert result.text != ""
```

- [ ] **Step 2: Run test to verify it passes (ya debería pasar)**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_context_assembler.py::test_rag_failure_does_not_break_assembly -v`
Expected: PASS (el try/except de Task 5 ya cubre esto). Si FALLA, envolver `rag.search` en try/except como indica Task 4/5.

- [ ] **Step 3: Commit**

```bash
git add tests/test_context_assembler.py
git commit -m "test(context): garantía fail-safe ante RAG que lanza"
```

---

## Task 7: Integración en el handler — merge con context_extra del modelo

**Files:**
- Modify: `memory/tools.py` (`ask_claude_deep` ~líneas 729-756)
- Test: `tests/test_ask_claude_deep_context.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ask_claude_deep_context.py
from memory.obsidian_vault import ObsidianVault
from memory.tools import ToolContext, ask_claude_deep
from memory import notes as notes_mod
from memory import triage as triage_mod


class FakeRAG:
    def search(self, query, top_k=3):
        return []
    def index_file(self, path):
        return 1
    def save(self):
        pass


class CapturingReasoner:
    model = "claude-sonnet-4-6"
    def __init__(self):
        self.captured = None
    def ask(self, prompt, context_extra=None, max_tokens=1024):
        self.captured = context_extra
        from claude.reasoner import ReasonerResponse
        return ReasonerResponse(
            text="ok", input_tokens=1, output_tokens=1,
            cache_creation_tokens=0, cache_read_tokens=0,
            cost_usd=0.0, latency_ms=1.0,
        )


def test_ask_claude_deep_appends_auto_context_after_model_context(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    card_path = triage_mod.project_card_path(vault, "Polymath IDE")
    card_path.parent.mkdir(parents=True, exist_ok=True)
    notes_mod.write_note(
        vault, card_path,
        body="# Polymath IDE - Memory Card\n\n## Pending\n- conectar agente\n",
        frontmatter={"type": "project-memory-card"},
    )
    reasoner = CapturingReasoner()
    ctx = ToolContext(vault=vault, rag=FakeRAG(), reasoner=reasoner)

    ask_claude_deep(ctx, prompt="sigamos con Polymath IDE", context_extra="nota del modelo")

    captured = reasoner.captured
    assert captured is not None
    assert "nota del modelo" in captured
    assert "conectar agente" in captured
    # auto-contexto va DESPUÉS del context_extra del modelo
    assert captured.index("nota del modelo") < captured.index("conectar agente")
```

Nota: el implementador DEBE confirmar la firma real de `ToolContext` en `memory/tools.py` (campos `vault`, `rag`, `reasoner` y cuáles son opcionales) y ajustar la construcción del `ctx` en el test si difiere. Si `_claude_preflight` exige algo más (p.ej. `ctx.reasoner` truthy), el `CapturingReasoner` ya lo satisface.

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ask_claude_deep_context.py -v`
Expected: FAIL — `"conectar agente" in captured` es False (hoy no se inyecta auto-contexto)

- [ ] **Step 3: Write minimal implementation**

En `memory/tools.py`, añade el import del assembler (junto a `from .triage import ...`):

```python
from .context_assembler import build_project_context
```

Añade el helper de merge (cerca de `ask_claude_deep`):

```python
def _merge_context(model_ctx: str | None, auto_ctx: str) -> str | None:
    parts = [p for p in (model_ctx, auto_ctx) if p and p.strip()]
    return "\n\n".join(parts) if parts else None


def _augmented_context(ctx: ToolContext, prompt: str, context_extra: str | None) -> str | None:
    try:
        auto = build_project_context(ctx.vault, ctx.rag, prompt)
    except Exception:
        return context_extra  # fail-safe: nunca rompe el razonamiento
    return _merge_context(context_extra, auto.text)
```

Modifica `ask_claude_deep` para usarlo (reemplaza la línea `r = ctx.reasoner.ask(...)`):

```python
    max_tokens = max(128, min(int(max_tokens or 1024), 2048))
    merged = _augmented_context(ctx, prompt, context_extra)
    r = ctx.reasoner.ask(prompt, context_extra=merged, max_tokens=max_tokens)
    return _format_claude_response(ctx.reasoner.model, r)
```

Haz el cambio equivalente en `ask_claude_deep_async` (usa `await ctx.reasoner.ask_async(prompt, context_extra=merged, ...)`).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_ask_claude_deep_context.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py tests/test_ask_claude_deep_context.py
git commit -m "feat(context): ask_claude_deep inyecta auto-contexto de proyecto (Fase 2)"
```

---

## Task 8: Verificación de regresión — suite completa verde

**Files:** ninguno (verificación)

- [ ] **Step 1: Correr toda la suite**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest -q`
Expected: PASS — los 82 tests previos + los nuevos del assembler y del handler, todo verde.

- [ ] **Step 2: Si algo rompió, arreglar antes de continuar**

Si algún test previo falla, investigar con `superpowers:systematic-debugging`. No marcar la fase como completa con tests rojos.

- [ ] **Step 3: Commit final (si hubo arreglos)**

```bash
git add -A
git commit -m "test(context): suite completa verde tras Fase 2"
```

---

## Self-Review (completado por el autor del plan)

**Cobertura del spec:**
- Fuentes (card + sesión + RAG) → Tasks 2, 3, 4. ✓
- Trigger solo si hay proyecto → Task 1. ✓
- RAG todo el vault → Task 4 (no se filtra por proyecto en `rag.search`). ✓
- Presupuesto ~2500, trunca RAG primero → Task 5. ✓
- Merge: modelo primero, auto al final → Task 7. ✓
- Fail-safe → Tasks 5 (try/except por fuente) + 6 (test) + 7 (try/except en handler). ✓
- Sin cambios en la declaración del tool → ninguna task la toca. ✓

**Consistencia de tipos:** `ContextResult(text, project, sources)`, `build_project_context(vault, rag, prompt, *, token_budget)`, `estimate_tokens`, `_wrap`, `_format_rag`, `_load_card_body`, `_merge_context`, `_augmented_context` — nombres usados de forma consistente entre tasks.

**Dependencias a confirmar en ejecución (señaladas inline):** nombre real de la subcarpeta de sesiones (`session_summary._sessions_dir`, Task 3) y firma de `ToolContext` (Task 7).
