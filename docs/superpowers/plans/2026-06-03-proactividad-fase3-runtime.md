# Fase 3 (B) — Integración Runtime de Proactividad — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Conectar el motor determinista de proactividad (Plan A, paquete `proactivity/`) al runtime de JARVIS: una fachada `ProactivityEngine`, la tool `jarvis_proactive_check` para que Gemini emita en ventanas naturales, el briefing de arranque inyectado al system_prompt, la detección contextual en cada turno, las 7 vars de `.env.example` y las instrucciones de verbalización en el system_prompt.

**Architecture:** Una fachada `proactivity/engine.py::ProactivityEngine` encapsula config + `OpportunityQueue` + el pipeline (states → signals → ingest → render/peek). Es el único punto que `jarvis.py` toca, lo que mantiene la lógica testeable y el cableado mínimo. La tool `jarvis_proactive_check` devuelve datos estructurados (no una frase); Gemini decide la verbalización vía prompt. Fail-safe absoluto: nada de proactividad puede romper la conversación.

**Tech Stack:** Python 3.11, pytest, `google.genai.types` (declaraciones de tools). Sin dependencias nuevas. Depende del Plan A ya implementado (`proactivity/{config,project_state,signals,opportunity_queue,briefing}.py`).

**Convenciones del repo:**
- Tests: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest <ruta> -v`
- Patrón: vault temporal `ObsidianVault(tmp_path, read_all=True)` + `FakeRAG`.
- Firmas confirmadas (Plan A + repo):
  - `ProactivityConfig.from_env(env) -> ProactivityConfig` con `enabled, stale_pending_days, stale_project_days, max_per_session, cooldown_days, briefing_top_k, min_score`.
  - `build_project_states(vault, *, today=None) -> list[ProjectState]`.
  - `detect_startup_signals(states, cfg) -> list[Signal]`; `detect_contextual_signals(turn_text, states, rag, cfg) -> list[Signal]`.
  - `OpportunityQueue(state_path, *, config)` con `ingest`, `top_opportunity(*, now=None)`, `mark_offered(id)`, `mark_dismissed(id)`, atributo interno `_candidates: list[Opportunity]`.
  - `Opportunity(id, signal, score, suggestion_struct)`; `render_briefing(opportunities, *, top_k) -> str`.
  - `ToolContext` (dataclass en `memory/tools.py`): campos `vault, rag, reasoner, tracker, gate, screen, actions, modes, obsidian_mcp, obs_memory, approvals, set_listen_mode`. Todos opcionales menos `vault`/`rag`.
  - Tools: `FunctionDeclaration` → lista `all_function_declarations()` (líneas ~646-668) → handler `def tool(ctx, ...)` → registro en `ToolDispatcher._tools` (líneas ~1663-1687).
  - `jarvis.py`: `build()` arma `recall_block` (líneas ~235-271) y lo concatena al `system_prompt` (líneas ~353-358); construye `ToolContext(...)` (líneas ~278-291); `_on_turn_complete()` persiste `user_delta`/`jarvis_delta` al journal (líneas ~775-789).
  - `gemini/system_prompt.py`: constante `SYSTEM_PROMPT` (triple-quoted) con secciones `═══════════ TITULO ═══════════`.
  - `.env.example`: secciones `# --- Fase N: ... ---`.

---

## File Structure

| Archivo | Responsabilidad |
|---------|-----------------|
| `proactivity/opportunity_queue.py` | modificar — añadir `peek_top(k, *, now)` (top-K sin marcar ofrecidas, para el briefing) |
| `proactivity/engine.py` | **nuevo** — `ProactivityEngine` fachada (build_briefing / observe / next_opportunity / dismiss_last) |
| `memory/tools.py` | modificar — campo `proactivity` en `ToolContext`; `JARVIS_PROACTIVE_CHECK_DECL`; handler `jarvis_proactive_check`; registro en lista y dispatcher |
| `gemini/system_prompt.py` | modificar — sección de ventana natural + verbalización |
| `jarvis.py` | modificar — construir engine, briefing al system_prompt, `proactivity=` en ToolContext, `observe()` en `_on_turn_complete` |
| `.env.example` | modificar — 7 vars de la Fase 3 |
| `tests/test_proactivity_engine.py` | **nuevo** — unit tests de la fachada |
| `tests/test_proactive_check_tool.py` | **nuevo** — integración de la tool |

---

## Task 1: `peek_top` + fachada `ProactivityEngine.build_briefing`

**Files:**
- Modify: `proactivity/opportunity_queue.py`
- Create: `proactivity/engine.py`
- Test: `tests/test_proactivity_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_engine.py
from datetime import date

from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine


def _write_card(vault, project, body, frontmatter=None):
    path = triage_mod.project_card_path(vault, project)
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = {"type": "project-memory-card", "importance": "high", "confidence": "high"}
    fm.update(frontmatter or {})
    notes_mod.write_note(vault, path, body=body, frontmatter=fm)


def _write_session(vault, name, body):
    base = vault.memory_path / "sessions"
    base.mkdir(parents=True, exist_ok=True)
    (base / name).write_text(body, encoding="utf-8")


class FakeRAG:
    def search(self, query, top_k=3):
        return []


def test_build_briefing_mentions_stale_pending(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    _write_session(
        vault, "2026-05-10_2100_sesion.md",
        "# S\n\n## Resumen\n- x\n\n## Pendientes\n- (ninguno)\n\n## Proyectos tocados\n- [[03-PROJECTS/polymath]]\n",
    )
    cfg = ProactivityConfig(min_score=0.0, stale_pending_days=7)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    block = eng.build_briefing(vault, today=date(2026, 5, 30))
    assert "BRIEFING PROACTIVO" in block
    assert "Polymath IDE" in block


def test_build_briefing_empty_when_disabled(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    cfg = ProactivityConfig(enabled=False)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    assert eng.build_briefing(vault, today=date(2026, 5, 30)) == ""


def test_build_briefing_failsafe_on_broken_vault(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    class Boom:
        memory_path = tmp_path / "nope"

        def __getattr__(self, name):
            raise RuntimeError("boom")

    # no debe propagar
    assert eng.build_briefing(Boom(), today=date(2026, 5, 30)) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_engine.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'proactivity.engine'`

- [ ] **Step 3: Write minimal implementation**

Primero añade `peek_top` a `OpportunityQueue` (en `proactivity/opportunity_queue.py`, tras `top_opportunity`):

```python
# proactivity/opportunity_queue.py  (añadir método a OpportunityQueue)
    def peek_top(self, k: int, *, now: datetime | None = None) -> list[Opportunity]:
        """Top-k candidatos por score (respeta cooldown), SIN marcarlos ofrecidos.
        Deduplica por id. Para el briefing de arranque."""
        now = now or datetime.now()
        ranked = sorted(self._candidates, key=lambda o: o.score, reverse=True)
        out: list[Opportunity] = []
        seen: set[str] = set()
        for opp in ranked:
            if opp.id in seen or self._in_cooldown(opp.id, now):
                continue
            seen.add(opp.id)
            out.append(opp)
            if len(out) >= max(0, k):
                break
        return out
```

Luego crea la fachada:

```python
# proactivity/engine.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add proactivity/opportunity_queue.py proactivity/engine.py tests/test_proactivity_engine.py
git commit -m "feat(proactivity): peek_top + fachada ProactivityEngine.build_briefing"
```

---

## Task 2: `observe`, `next_opportunity` y `dismiss_last`

**Files:**
- Test: `tests/test_proactivity_engine.py`

Nota: la implementación de los tres métodos ya entró en Task 1. Esta tarea **prueba el
comportamiento de emisión** (detección contextual → encolar → emitir top → descartar).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactivity_engine.py  (añadir)
def test_observe_then_next_opportunity_emits_struct(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")

    eng.observe(vault, FakeRAG(), "sigamos con Polymath IDE")
    struct = eng.next_opportunity()
    assert struct is not None
    assert struct["project"] == "Polymath IDE"
    assert "what" in struct and "why_now" in struct


def test_next_opportunity_none_when_no_candidates(tmp_path):
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    assert eng.next_opportunity() is None


def test_dismiss_last_marks_cooldown(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    _write_card(
        vault, "Polymath IDE",
        "# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
    )
    cfg = ProactivityConfig(min_score=0.0, cooldown_days=7)
    path = tmp_path / "state.json"
    eng = ProactivityEngine(config=cfg, state_path=path)
    eng.observe(vault, FakeRAG(), "Polymath IDE")
    assert eng.next_opportunity() is not None
    eng.dismiss_last()

    # nueva sesión (misma persistencia): dentro del cooldown → no reaparece
    eng2 = ProactivityEngine(config=cfg, state_path=path)
    eng2.observe(vault, FakeRAG(), "Polymath IDE")
    assert eng2.next_opportunity() is None
```

- [ ] **Step 2: Run test to verify it fails (o pasa directo)**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactivity_engine.py -v`
Expected: PASS (los métodos ya existen de Task 1). Si algún assert falla, corregir el método correspondiente en `engine.py` antes de continuar.

- [ ] **Step 3: Commit**

```bash
git add tests/test_proactivity_engine.py
git commit -m "test(proactivity): emisión observe→next_opportunity→dismiss_last"
```

---

## Task 3: Tool `jarvis_proactive_check`

**Files:**
- Modify: `memory/tools.py`
- Test: `tests/test_proactive_check_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_proactive_check_tool.py
from memory.obsidian_vault import ObsidianVault
from memory import notes as notes_mod
from memory import triage as triage_mod
from memory.tools import ToolContext, jarvis_proactive_check
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine


class FakeRAG:
    def search(self, query, top_k=3):
        return []


def _engine_with_pending(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    card = triage_mod.project_card_path(vault, "Polymath IDE")
    card.parent.mkdir(parents=True, exist_ok=True)
    notes_mod.write_note(
        vault, card,
        body="# Polymath IDE - Memory Card\n\n## Pending\n\n- 2026-05-03 [high/high] conectar el agente\n",
        frontmatter={"type": "project-memory-card", "importance": "high", "confidence": "high"},
    )
    cfg = ProactivityConfig(min_score=0.0)
    eng = ProactivityEngine(config=cfg, state_path=tmp_path / "state.json")
    eng.observe(vault, FakeRAG(), "sigamos con Polymath IDE")
    return vault, eng


def test_proactive_check_returns_opportunity(tmp_path):
    vault, eng = _engine_with_pending(tmp_path)
    ctx = ToolContext(vault=vault, rag=FakeRAG(), proactivity=eng)

    out = jarvis_proactive_check(ctx)
    assert out["has_opportunity"] is True
    assert out["opportunity"]["project"] == "Polymath IDE"


def test_proactive_check_empty_when_no_engine(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    ctx = ToolContext(vault=vault, rag=FakeRAG())  # proactivity=None
    out = jarvis_proactive_check(ctx)
    assert out["has_opportunity"] is False


def test_proactive_check_dismiss_flag_suppresses_next(tmp_path):
    vault, eng = _engine_with_pending(tmp_path)
    ctx = ToolContext(vault=vault, rag=FakeRAG(), proactivity=eng)

    first = jarvis_proactive_check(ctx)
    assert first["has_opportunity"] is True
    # Isaac descartó la sugerencia previa → marcar dismissed; ya no hay más
    out = jarvis_proactive_check(ctx, dismissed_last=True)
    assert out["has_opportunity"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactive_check_tool.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'proactivity'` (ToolContext aún no tiene el campo) y/o `ImportError` de `jarvis_proactive_check`.

- [ ] **Step 3: Write minimal implementation**

En `memory/tools.py`:

(a) Añade el campo a `ToolContext` (tras `set_listen_mode`):

```python
    # Motor de proactividad (Fase 3). None si la feature está deshabilitada.
    proactivity: Any | None = None
```

(b) Añade la declaración (junto a las demás `*_DECL`, antes de `all_function_declarations`):

```python
JARVIS_PROACTIVE_CHECK_DECL = types.FunctionDeclaration(
    name="jarvis_proactive_check",
    description=(
        "Consulta si JARVIS tiene una sugerencia proactiva pertinente para ofrecer "
        "AHORA. Llámala SOLO en ventanas naturales: cuando un tema se cierra, cuando "
        "Isaac pregunta '¿algo más?', o al cerrar la sesión. NUNCA a media explicación. "
        "Devuelve una sola oportunidad estructurada (o ninguna). Si Isaac descartó la "
        "sugerencia anterior, vuelve a llamarla con dismissed_last=true para no repetirla."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "dismissed_last": types.Schema(
                type=types.Type.BOOLEAN,
                description="true si Isaac ignoró/rechazó la sugerencia ofrecida antes.",
            ),
        },
    ),
)
```

(c) Regístrala en `all_function_declarations()` (añade al final de la lista, antes del `]`):

```python
        JARVIS_PROACTIVE_CHECK_DECL,
```

(d) Añade el handler (cerca de `jarvis_session_recall`):

```python
def jarvis_proactive_check(ctx: ToolContext, dismissed_last: bool = False) -> dict:
    """Devuelve la oportunidad proactiva top estructurada (o ninguna). Fail-safe."""
    engine = ctx.proactivity
    if engine is None:
        return {"has_opportunity": False, "opportunity": None}
    try:
        if dismissed_last:
            engine.dismiss_last()
        struct = engine.next_opportunity()
    except Exception:
        return {"has_opportunity": False, "opportunity": None}
    return {"has_opportunity": struct is not None, "opportunity": struct}
```

(e) Regístrala en `ToolDispatcher._tools` (dentro del dict):

```python
            "jarvis_proactive_check": lambda **kw: jarvis_proactive_check(ctx, **kw),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_proactive_check_tool.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py tests/test_proactive_check_tool.py
git commit -m "feat(proactivity): tool jarvis_proactive_check (decl + handler + dispatcher)"
```

---

## Task 4: 7 variables en `.env.example`

**Files:**
- Modify: `.env.example`
- Test: verificación con grep

- [ ] **Step 1: Añadir la sección al final de `.env.example`**

```bash
# .env.example  (añadir al final)

# --- Fase 3: Proactividad y Agencia (KAG) ---
# Master switch de todo el motor de proactividad (briefing + deteccion + tool).
JARVIS_PROACTIVITY_ENABLED=true
# Dias para considerar "stale" un pendiente abierto de una card.
JARVIS_PROACTIVITY_STALE_PENDING_DAYS=7
# Dias para considerar "stale" un proyecto importante sin tocar.
JARVIS_PROACTIVITY_STALE_PROJECT_DAYS=14
# Tope de sugerencias ofrecidas por sesion (anti-spam).
JARVIS_PROACTIVITY_MAX_PER_SESSION=3
# No repetir una sugerencia descartada en N dias.
JARVIS_PROACTIVITY_COOLDOWN_DAYS=7
# Cuantas oportunidades entran en el briefing de arranque.
JARVIS_PROACTIVITY_BRIEFING_TOP_K=3
# Score minimo para considerar una oportunidad.
JARVIS_PROACTIVITY_MIN_SCORE=0.35
```

- [ ] **Step 2: Verificar**

Run: `grep -c "JARVIS_PROACTIVITY_" .env.example`
Expected: `7`

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(env): 7 vars de proactividad Fase 3 en .env.example"
```

---

## Task 5: Instrucciones de ventana natural en el system_prompt

**Files:**
- Modify: `gemini/system_prompt.py`
- Test: verificación con grep + smoke import

- [ ] **Step 1: Insertar la sección antes del cierre `"""` de `SYSTEM_PROMPT`**

Añade este bloque dentro del string `SYSTEM_PROMPT`, justo antes de las comillas triples de cierre:

```
═══════════ PROACTIVIDAD (VENTANAS NATURALES) ═══════════

JARVIS puede tener una sugerencia proactiva pertinente (un pendiente que quedó
stale, un proyecto importante sin tocar, o algo que ya resolviste en otro proyecto
y aplica ahora). Para ofrecerla, usa la tool jarvis_proactive_check, con criterio:

- Llámala SOLO en una ventana natural: cuando un tema se cierra, cuando Isaac
  pregunta "¿algo más?", o al cerrar la sesión. NUNCA interrumpas a media frase
  ni a mitad de una explicación.
- Si devuelve una oportunidad, verbalízala como UNA sugerencia breve y natural,
  no como un informe. No recites listas. Si no encaja en el momento, cállatela.
- Si Isaac ignora o rechaza la sugerencia, en tu próxima llamada pasa
  dismissed_last=true para no insistir.
- El briefing de arranque (si aparece al inicio del contexto) es material para
  UNA mención oportuna al abrir, no para recitar.

Regla de oro: la proactividad acompaña, no invade. Ante la duda, calla.
```

- [ ] **Step 2: Verificar**

Run: `grep -c "jarvis_proactive_check" gemini/system_prompt.py`
Expected: `>= 1`
Run: `PYTHONUTF8=1 /h/Python311/python.exe -c "from gemini.system_prompt import SYSTEM_PROMPT; print('PROACTIVIDAD' in SYSTEM_PROMPT)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add gemini/system_prompt.py
git commit -m "feat(proactivity): instrucciones de ventana natural en system_prompt"
```

---

## Task 6: Cableado en `jarvis.py`

**Files:**
- Modify: `jarvis.py`
- Test: smoke import (jarvis.py no es unit-testeable por su acoplamiento con audio/overlay/Gemini)

- [ ] **Step 1: Importar config y engine**

Cerca de los imports de `proactivity`/memoria al inicio de `jarvis.py`, añade:

```python
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine
```

- [ ] **Step 2: Construir el engine y el briefing en `build()`**

En `build()`, **después** del bloque que arma `recall_block` (tras la línea
`log.warning(f"[WARN] recall de sesión previa falló: {exc}")` / el cierre de ese try),
y **antes** de construir `self.tool_ctx`, añade:

```python
        # Fase 3 — Proactividad: motor + briefing de arranque.
        self.proactivity = None
        briefing_block = ""
        try:
            pcfg = ProactivityConfig.from_env()
            if pcfg.enabled:
                self.proactivity = ProactivityEngine(
                    config=pcfg,
                    state_path=Path("data") / "proactivity_state.json",
                )
                briefing_block = self.proactivity.build_briefing(self.vault)
                if briefing_block:
                    log.info("Briefing proactivo inyectado al system_prompt.")
        except Exception as exc:
            log.warning(f"[WARN] proactividad (arranque) falló: {exc}")
```

> `Path` ya está importado en `jarvis.py` (usado para rutas de datos). Si no lo estuviera,
> añade `from pathlib import Path`.

- [ ] **Step 3: Pasar el engine al `ToolContext`**

En la construcción de `self.tool_ctx = ToolContext(...)`, añade el argumento:

```python
            proactivity=self.proactivity,
```

- [ ] **Step 4: Concatenar el briefing al system_prompt**

En la construcción de `SessionConfig(... system_prompt=( ... ))`, añade el briefing
tras el `recall_block`:

```python
                system_prompt=(
                    SYSTEM_PROMPT
                    + "\n\n"
                    + preferences_prompt_block(self.preferences)
                    + (("\n\n" + recall_block) if recall_block else "")
                    + (("\n\n" + briefing_block) if briefing_block else "")
                ),
```

- [ ] **Step 5: Detección contextual en `_on_turn_complete`**

En `_on_turn_complete`, **después** del bloque `if user_delta or jarvis_delta: self.session_journal.append_turn(...)` (dentro del mismo `if self.session_continuity_enabled:` o justo después), añade:

```python
        # Fase 3 — Proactividad: observar el turno (encola, NO emite).
        if self.proactivity is not None:
            try:
                if user_delta:
                    self.proactivity.observe(self.vault, self.rag, user_delta)
            except Exception as exc:
                self._log(f"[WARN] proactividad (observe) falló: {exc}")
```

> Si `user_delta` está fuera de alcance en ese punto (definido dentro del try del journal),
> recalcula el texto del turno desde `self._input_transcript` o mueve el bloque dentro del
> try del journal, tras el `append_turn`. Confirma el alcance real al implementar.

- [ ] **Step 6: Verificar (smoke import)**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -c "import ast; ast.parse(open('jarvis.py', encoding='utf-8').read()); print('syntax ok')"`
Expected: `syntax ok`

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest -q`
Expected: la suite completa sigue verde (jarvis.py no se importa en los tests, pero sí
los módulos que toca; ninguno debe romper).

- [ ] **Step 7: Commit**

```bash
git add jarvis.py
git commit -m "feat(proactivity): cableado en jarvis.py (briefing + observe + ToolContext)"
```

---

## Task 7: Verificación de regresión + done

**Files:** ninguno (verificación)

- [ ] **Step 1: Smoke import del paquete y la fachada**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -c "from proactivity.engine import ProactivityEngine; from memory.tools import jarvis_proactive_check, ToolContext; print('ok')"`
Expected: `ok`

- [ ] **Step 2: Suite completa**

Run: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest -q`
Expected: PASS — suite previa (158 tras Plan A) + nuevos tests del engine y la tool, todo verde.

- [ ] **Step 3: Si algo rompió, arreglar antes de cerrar**

Investigar con `superpowers:systematic-debugging`. No marcar la fase como completa con tests rojos.

- [ ] **Step 4: Commit final (si hubo arreglos)**

```bash
git add -A
git commit -m "test(proactivity): Fase 3 runtime completa, suite verde"
```

---

## Self-Review (completado por el autor del plan)

**Cobertura del spec (sección "Integración"):**
- `jarvis.py build()` → briefing al system_prompt → Task 6 (steps 2,4). ✓
- `jarvis.py _on_turn_complete()` → `observe()` (no emite) → Task 6 (step 5). ✓
- `memory/tools.py` tool `jarvis_proactive_check` (decl+handler+dispatcher) → Task 3. ✓
- `gemini/system_prompt.py` instrucciones de ventana natural → Task 5. ✓
- `.env` 7 vars → Task 4. ✓
- Fachada que aísla el motor del runtime → Tasks 1, 2 (`ProactivityEngine`). ✓
- Tool devuelve datos estructurados, no frase → Task 3 (handler retorna `suggestion_struct`). ✓
- Fail-safe (nada rompe la conversación) → engine try/except (Tasks 1,2) + handler try/except (Task 3) + cableado try/except (Task 6). ✓
- Caso de uso 3 (planificación autónoma con `context_assembler` de Fase 2): el `action_hint`
  de la oportunidad lo habilita; la verbalización/escalado a `ask_claude_deep` la decide Gemini
  por prompt (Task 5). No requiere código nuevo aquí porque Fase 2 ya inyecta el auto-contexto.

**Consolidación silenciosa y resolución de ambigüedad (puntos 2.a / 2.b del spec):** son
comportamientos guiados por prompt sobre tools ya existentes (`jarvis_remember`,
`jarvis_session_recall`, `triage`, `ask_claude_deep`). Se cubren con las instrucciones del
system_prompt (Task 5) sin módulos nuevos; si tras E2E se ve que hace falta lógica
determinista extra, se planifica aparte. No se fuerza código especulativo (YAGNI).

**Placeholder scan:** sin TBD/TODO; cada step de código trae el código real. Los dos puntos
marcados "confirma al implementar" (alcance de `user_delta`, presencia de `Path` import en
jarvis.py) son verificaciones de integración inevitables contra un archivo grande, no placeholders.

**Consistencia de tipos:** `ProactivityEngine(config=..., state_path=...)` con
`build_briefing(vault, *, today)`, `observe(vault, rag, turn_text)`, `next_opportunity() -> dict|None`,
`dismiss_last()`; `OpportunityQueue.peek_top(k, *, now)`; `jarvis_proactive_check(ctx, dismissed_last=False) -> {has_opportunity, opportunity}`; `ToolContext.proactivity`. Nombres consistentes con el Plan A.

**Verificación de done (del spec):**
- `pytest` verde (Task 7). ✓
- Smoke import de los módulos nuevos (Task 7). ✓
- E2E manual (arranque con pendiente stale → briefing; cross-proyecto → sugerencia en ventana;
  descarte → no reaparece; ambigüedad → pregunta): queda como checklist manual post-merge,
  no automatizable sin Gemini Live real.
```

