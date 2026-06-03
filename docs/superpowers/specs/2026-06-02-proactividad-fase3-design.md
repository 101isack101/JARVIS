# Diseño: Fase 3 — Proactividad y Agencia (KAG)

**Fecha:** 2026-06-02
**Proyecto:** JARVIS — Asistente Integral en Obsidian
**Parte de:** Roadmap de evolución de JARVIS (Fase 3 de 3 — cierra el roadmap)
**Estado:** Aprobado en brainstorming

---

## Contexto y motivación

JARVIS hoy es **reactivo**: responde bien, recuerda (Fase 1) y razona con auto-contexto
(Fase 2), pero solo actúa cuando Isaac habla primero. Todo el conocimiento acumulado
en el vault —Project Memory Cards, notas de sesión, índice RAG— se consulta *on-demand*
cuando el modelo decide llamar una tool. Falta la pieza que convierte un sistema RAG en
un verdadero **KAG (Knowledge Augmented Generation)**: que el conocimiento **dispare
razonamiento por sí mismo** y JARVIS muestre intuición, anticipe necesidades y planifique.

Las dos fases previas dejaron la materia prima:

- **Fase 1 (2026-05-28):** `session_journal.py` + `session_summary.py` → continuidad
  entre sesiones, notas-diario fechadas, `jarvis_session_recall`.
- **Fase 2 (2026-05-30):** `context_assembler.py` → auto-contexto (Card + sesión + RAG)
  inyectado al razonamiento profundo de Claude.

Esta es la **Fase 3** y **cierra** el roadmap. Ambos specs previos la dejaron
explícitamente como "fuera de alcance, roadmap futuro". No se diseña nada desde cero:
se construye un motor de proactividad que **lee** de las fuentes que ya existen.

## Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección |
|----------|----------|
| Canal de salida | **Briefing al arranque + ventanas naturales.** JARVIS detecta en tiempo real pero verbaliza solo en huecos del turno; **nunca interrumpe a media frase** (coherente con el trabajo de AEC/barge-in) |
| Disparo del razonamiento proactivo | **Arranque + durante la conversación** (briefing al encender + detección de oportunidades en tiempo real) |
| Autonomía de consolidación (`jarvis_remember`) | **Auto-guardar silencioso** (se mantiene el flujo actual + un *consolidation checkpoint*); sin avisos ni confirmaciones |
| Arquitectura | **Enfoque B: motor determinista** en módulo aislado `proactivity/` |
| Verbalización | El backend produce la oportunidad **estructurada**; **Gemini la verbaliza** (prompt-first para la voz, según `feedback_jarvis_prompt_first_modulacion`) |
| Entrega en runtime | Tool `jarvis_proactive_check()` que Gemini llama en ventanas naturales |

## Goals

- Al arrancar, JARVIS genera un **briefing proactivo** (1-3 sugerencias accionables)
  derivado del estado real de los proyectos, además del recall de sesión de la Fase 1.
- Durante la conversación, un motor determinista **detecta oportunidades** (pendientes
  stale, proyectos sin tocar, conexiones cross-proyecto) y las encola **sin emitir nada**.
- JARVIS verbaliza la oportunidad top **solo en una ventana natural** (cierre de tema,
  Isaac pregunta "¿algo más?", cierre de sesión), vía `jarvis_proactive_check()`.
- **Anti-spam real y testeable:** cooldown, dedup y memoria de qué sugerencias ignora Isaac.
- Resolución de **consultas ambiguas** con desambiguación explícita (preguntar, no adivinar).
- Fail-safe absoluto: un fallo del motor de proactividad **nunca** rompe la conversación.
- Respetar la postura de seguridad: el motor lee de notas **ya redactadas** por el triage;
  no reintroduce contenido sensible.

## Non-Goals (fuera de alcance)

- Proactividad **push por voz** en tiempo real (interrumpir a Isaac) — descartado por diseño.
- Análisis **programado en background** (cron) cuando JARVIS está cerrado — posible futuro,
  fuera de esta fase.
- Cambiar el flujo de `jarvis_remember` o el `triage` (la consolidación sigue silenciosa).
- Embeddings nuevos o reranking sofisticado del RAG (se usa el índice existente).
- ML para predecir qué ignora Isaac: el "aprendizaje" es un contador determinista simple.

---

## Arquitectura

Módulo nuevo y aislado `proactivity/`. No conoce la API de Anthropic ni el dispatcher;
solo lee del vault, del RAG y del estado persistido. Cada submódulo tiene una
responsabilidad única y es testeable de forma independiente.

```
                 ┌──────────────────── FUENTES YA EXISTENTES ────────────────────┐
                 │  Project Memory Cards   Notas de sesión      FAISS RAG         │
                 │  (triage.py)            (session_summary)    (rag.py)          │
                 └───────┬──────────────────────┬───────────────────┬────────────┘
                         ▼                       ▼                   ▼
        ┌────────────────────────────────────────────────────────────────────┐
        │  proactivity/project_state.py  →  ProjectStateModel                  │
        └───────────────────────────────┬────────────────────────────────────┘
                                         ▼
        ┌────────────────────────────────────────────────────────────────────┐
        │  proactivity/signals.py  →  detectores deterministas (reglas puras)  │
        └───────────────────────────────┬────────────────────────────────────┘
                                         ▼
        ┌────────────────────────────────────────────────────────────────────┐
        │  proactivity/opportunity_queue.py  →  scoring + dedup + cooldown     │
        │  persistencia: data/proactivity_state.json                          │
        └───────┬──────────────────────────────────────────────┬─────────────┘
                ▼                                                ▼
   ┌──────────────────────────┐                  ┌──────────────────────────────┐
   │ proactivity/briefing.py  │                  │ tool: jarvis_proactive_check  │
   │ (ARRANQUE → system_prompt│                  │ (VENTANA NATURAL, runtime)    │
   └──────────────────────────┘                  └──────────────────────────────┘
```

### `proactivity/project_state.py` — modelo de estado

Responsabilidad única: derivar, por proyecto, un snapshot del estado actual a partir de
las Project Memory Cards (sección `Pending`, `Decisions`, `Current State`) y las notas de
sesión (última fecha que mencionó el proyecto, vía wikilinks `[[03-PROJECTS/...]]`).

```python
@dataclass(frozen=True)
class ProjectState:
    project: str
    last_touched: date | None      # fecha de la sesión más reciente que lo mencionó
    staleness_days: int | None     # días desde last_touched
    open_pendings: list[str]       # bullets de la sección Pending de la card
    open_decisions: list[str]      # decisiones registradas (para detectar open loops)
    importance: str                # del frontmatter de la card
    confidence: str

def build_project_states(vault, *, today: date | None = None) -> list[ProjectState]:
    ...
```

- Reusa `triage.PROJECT_ALIASES`, `triage.project_card_path` y `notes.read_note`.
- 100% determinista y barato (solo lee archivos; sin LLM, sin embeddings).

### `proactivity/signals.py` — detectores deterministas

Reglas puras que convierten `ProjectState` + el contexto conversacional en `Signal`s.

```python
@dataclass(frozen=True)
class Signal:
    kind: str          # stale_pending | stale_project | open_loop | cross_project | ctx_pending
    project: str
    payload: dict      # {pending, days, ...} según kind
    base_priority: float
    evidence: list[str] # rel_paths / wikilinks que respaldan la sugerencia
```

Detectores:

| Detector | Disparo | Fuente |
|----------|---------|--------|
| `StalePendingSignal` | pendiente abierto > `STALE_PENDING_DAYS` | ProjectState.open_pendings |
| `StaleProjectSignal` | proyecto con importance≥normal sin tocar > `STALE_PROJECT_DAYS` | ProjectState.staleness_days |
| `OpenLoopSignal` | decisión registrada sin avance / "Next Steps" sin cerrar | ProjectState.open_decisions |
| `CrossProjectSignal` | la conversación toca un tema/tech que aparece en **otro** proyecto | `jarvis_recall` (RAG cross-vault) |
| `ContextualPendingSignal` | Isaac menciona un proyecto con pendientes abiertos relevantes | `triage.detect_project` + ProjectState |

- Los detectores de **arranque** (`StalePending`, `StaleProject`, `OpenLoop`) no necesitan
  contexto conversacional. Los **en tiempo real** (`CrossProject`, `ContextualPending`)
  reciben el texto del turno.
- `CrossProjectSignal` es el corazón de la "intuición": usa el RAG existente para encontrar
  conexiones no obvias entre lo que Isaac dice ahora y lo que ya resolvió antes.

### `proactivity/opportunity_queue.py` — priorización y anti-spam

Convierte `Signal`s en `Opportunity`s puntuadas, deduplica, aplica cooldown y persiste qué
se sugirió / qué se ignoró.

```python
@dataclass(frozen=True)
class Opportunity:
    id: str                 # hash estable (kind+project+payload) para dedup/cooldown
    signal: Signal
    score: float
    suggestion_struct: dict # {what, project, why_now, evidence, action_hint}

class OpportunityQueue:
    def __init__(self, state_path: Path, *, config: ProactivityConfig): ...
    def ingest(self, signals: list[Signal]) -> None: ...
    def top_opportunity(self, *, now: datetime | None = None) -> Opportunity | None: ...
    def mark_offered(self, opp_id: str) -> None: ...
    def mark_dismissed(self, opp_id: str) -> None: ...
```

- **Scoring:** `score = base_priority × importance_weight × recency_weight × ctx_relevance`.
- **Dedup:** una `id` no se ofrece dos veces en la misma sesión.
- **Cooldown:** una oportunidad `dismissed` no reaparece en `COOLDOWN_DAYS`.
- **Tope por sesión:** máximo `MAX_PER_SESSION` ofertas.
- **Persistencia:** `data/proactivity_state.json` → `{id: {offered_at, dismissed_at, count}}`.
  Este es el "aprendizaje" determinista de qué ignora Isaac (sin ML).

### `proactivity/briefing.py` — briefing de arranque

Toma las top-K oportunidades de arranque + el recall de sesión (Fase 1) y produce un bloque
**estructurado** para el system_prompt. La narración la hace Gemini (prompt-first); el bloque
da los datos, no la frase.

```
═══════════ BRIEFING PROACTIVO ═══════════
- [Upwork Agent] pendiente hace 13 días: setup .env + RSS + Discord webhook
- [MTurk HITL] listo salvo cuenta MTurk para el smoke test (49/49 tests)
- [cross] FAISS aparece en Interview Copilot y en la skill faiss-rag
══════════════════════════════════════════
(Menciónalo solo si encaja al abrir; no recites la lista. Una sugerencia, no un informe.)
```

---

## Data flow

### Camino A — Arranque (briefing)

```
build()  →  build_project_states(vault)  →  signals (stale_pending/stale_project/open_loop)
        →  queue.ingest()  →  top-K  →  briefing.render()  →  bloque al system_prompt
```

### Camino B — Tiempo real (detección sin emisión)

```
_on_turn_complete(user, jarvis)
   →  detect_project(user) + jarvis_recall(user)            (barato, determinista)
   →  signals contextuales (cross_project / ctx_pending)
   →  queue.ingest()       # SOLO encola; no verbaliza nada aquí
```

### Camino C — Ventana natural (emisión)

```
Gemini detecta hueco (cierre de tema / "¿algo más?" / cierre de sesión)
   →  llama tool jarvis_proactive_check()
   →  queue.top_opportunity()  →  Opportunity estructurada (o vacío)
   →  Gemini decide si encaja y la VERBALIZA con voz natural
   →  queue.mark_offered(id)   (si Isaac la descarta → mark_dismissed en el siguiente turno)
```

### Camino D — Resolución de consulta ambigua (tu punto #2.b)

```
consulta ambigua ("¿cómo seguimos con eso?", "retomemos lo de ayer")
   1. ¿referencia temporal? → jarvis_session_recall(when)
   2. ¿referencia a proyecto? → triage.detect_project()
        └ si no hay → proyecto activo de la sesión / más reciente del ProjectStateModel
   3. ¿sigue ambiguo? → jarvis_recall top-k para desambiguar por contenido
   4. ¿múltiples candidatos con score similar? → NO adivinar; preguntar UNA cosa
        ("¿lo de Agentics o lo de Polymath?")
   5. contexto resuelto → responder / escalar a ask_claude_deep (context_assembler, Fase 2)
```

### Consolidación autónoma (tu punto #2.a) — silenciosa

Se mantiene el flujo actual (`jarvis_remember` + `triage`). La Fase 3 añade un
**consolidation checkpoint**: al cierre de tema/sesión, el motor marca candidatos durables
anclados en lo que `triage` ya clasifica como `decision | preference | todo | project_fact`,
y Gemini ejecuta `remember` sin avisar. Cero strings nuevos; el `triage` es el árbitro.

---

## Casos de uso (anclados en proyectos reales del vault)

1. **Intuición — conexión cross-proyecto.** Isaac describe búsqueda semántica con FAISS en
   un proyecto nuevo. `CrossProjectSignal` detecta (vía `jarvis_recall`) que ya lo resolvió
   en **Interview Copilot** y existe la skill `faiss-rag`. Ventana natural:
   *"Eso de FAISS ya lo resolviste en Interview Copilot — ¿reuso ese patrón?"*

2. **Anticipación — pendiente + staleness.** Briefing de arranque: `StalePendingSignal`
   detecta que el **Upwork Agent** tiene un pendiente abierto hace ~2 semanas (.env + RSS +
   Discord webhook). *"Pendiente desde hace 13 días: el Upwork Agent quedó sin el .env ni el
   webhook. ¿Lo cerramos hoy?"*

3. **Planificación autónoma — open loop → plan.** Isaac dice *"tengo media hora libre"*. El
   motor toma la oportunidad top accionable y dispara `ask_claude_deep` con el
   `context_assembler` (Card+sesión+RAG) para un mini-plan acotado: *"Con 30 min, lo más
   rentable es el smoke test del MTurk agent; te falta la cuenta MTurk pero puedo dejarte los
   3 pasos y el script listo."*

---

## Integración

| Punto | Cambio |
|-------|--------|
| `jarvis.py` `build()` | tras el recall de sesión (Fase 1), construir estados + briefing y concatenarlo al system_prompt |
| `jarvis.py` `_on_turn_complete()` | tras `journal.append_turn` (Fase 1), correr detección contextual y `queue.ingest()` (no emite) |
| `memory/tools.py` | nueva tool `jarvis_proactive_check` (decl + handler) que devuelve la `Opportunity` top estructurada o vacío |
| `gemini/system_prompt.py` | instrucciones de **ventana natural**: cuándo llamar `jarvis_proactive_check`, cómo verbalizar (1 sugerencia, no informe), cuándo callar |

La tool devuelve **datos estructurados**, no una frase. La verbalización vive en el prompt.

---

## Configuración (.env)

| Var | Default | Propósito |
|-----|---------|-----------|
| `JARVIS_PROACTIVITY_ENABLED` | `true` | Master switch de toda la fase |
| `JARVIS_PROACTIVITY_STALE_PENDING_DAYS` | `7` | Umbral para `StalePendingSignal` |
| `JARVIS_PROACTIVITY_STALE_PROJECT_DAYS` | `14` | Umbral para `StaleProjectSignal` |
| `JARVIS_PROACTIVITY_MAX_PER_SESSION` | `3` | Tope de ofertas por sesión (anti-spam) |
| `JARVIS_PROACTIVITY_COOLDOWN_DAYS` | `7` | No repetir una sugerencia descartada |
| `JARVIS_PROACTIVITY_BRIEFING_TOP_K` | `3` | Oportunidades en el briefing de arranque |
| `JARVIS_PROACTIVITY_MIN_SCORE` | `0.35` | Score mínimo para considerar una oportunidad |

Documentar todas en `.env.example`.

---

## Manejo de errores (fail-safe)

Mismo principio que Fases 1 y 2: **nunca romper la conversación por un fallo de proactividad.**

| Escenario | Comportamiento |
|-----------|----------------|
| Fallo al leer cards/sesiones | El estado de ese proyecto se omite; el resto sigue |
| `proactivity_state.json` corrupto | Se ignora y se reinicia (log); cero ofertas no es un error |
| `jarvis_proactive_check` lanza | Devuelve vacío; Gemini sigue como si no hubiera sugerencia |
| Briefing falla en `build()` | Arranque sin briefing (degradación elegante) |
| RAG caído (cross_project) | Se omite ese detector; los de arranque siguen |

---

## Testing (TDD)

Tests nuevos con vault temporal y `FakeRAG` (patrón de `tests/test_context_assembler.py` y
`tests/test_memory_remember.py`). Todo el motor es determinista ⇒ testeable sin LLM.

- `tests/test_proactivity_project_state.py`: deriva estados de cards/sesiones de fixture;
  `staleness_days` correcto; pendientes parseados; degrada si falta una card.
- `tests/test_proactivity_signals.py`: cada detector dispara/no-dispara según umbral;
  `cross_project` usa `FakeRAG`; señales de arranque vs contextuales.
- `tests/test_proactivity_queue.py`: scoring ordena bien; **dedup** (no repite en sesión);
  **cooldown** (descartada no reaparece antes de `COOLDOWN_DAYS`); **tope por sesión**;
  persistencia roundtrip; archivo corrupto se reinicia sin romper.
- `tests/test_proactivity_briefing.py`: top-K respetado; bloque vacío si no hay oportunidades;
  formato estructurado correcto.
- Integración en `tests/test_memory_tools_extended.py`: `jarvis_proactive_check` devuelve la
  top opportunity estructurada / vacío; fail-safe ante excepción.

La suite completa existente debe seguir verde.

---

## Archivos afectados

| Archivo | Cambio |
|---------|--------|
| `proactivity/__init__.py` | **nuevo** |
| `proactivity/project_state.py` | **nuevo** — `ProjectState` + `build_project_states` |
| `proactivity/signals.py` | **nuevo** — `Signal` + detectores |
| `proactivity/opportunity_queue.py` | **nuevo** — scoring/dedup/cooldown + persistencia |
| `proactivity/briefing.py` | **nuevo** — render del briefing de arranque |
| `memory/tools.py` | tool `jarvis_proactive_check` (decl + handler) |
| `gemini/system_prompt.py` | instrucciones de ventana natural y verbalización |
| `jarvis.py` | cableado en `build()` y `_on_turn_complete()` |
| `.env.example` | 7 vars nuevas |
| `tests/test_proactivity_*.py` | **nuevos** (4 archivos) |

## Reutilización (no reinventar)

- `memory/triage.py` → `PROJECT_ALIASES`, `detect_project`, `project_card_path`.
- `memory/session_summary.py` → recall de sesiones (Fase 1).
- `memory/context_assembler.py` → auto-contexto para escalado a Claude (Fase 2).
- `memory/rag.py` → `VaultRAG.search` para `cross_project`.
- `claude/reasoner.py` → `ask_claude_deep` para planificación profunda.
- `security/secret_filter.py` → el contenido ya viene redactado de las cards/sesiones.

## Verificación de done

- `pytest` verde (suite existente + 4 archivos de test nuevos).
- Smoke import de los módulos nuevos de `proactivity/`.
- E2E manual:
  1. Arrancar con un pendiente stale en una card → el briefing lo menciona al abrir.
  2. Conversación que toca un tema cross-proyecto → en una ventana natural JARVIS lo sugiere.
  3. Descartar una sugerencia → no reaparece en la misma sesión (dedup) ni en `COOLDOWN_DAYS`.
  4. Consulta ambigua con dos proyectos candidatos → JARVIS pregunta, no adivina.
- Ninguna ruta de proactividad puede romper la conversación (fail-safe verificado).
