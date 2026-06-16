# Detección de lagunas de conocimiento (KSI Fase 2) — Diseño

- **Fecha:** 2026-06-15
- **Estado:** Diseño aprobado (brainstorming)
- **Autor:** Isaac + JARVIS
- **Fase:** 2 del roadmap de auto-mejora recursiva de conocimiento (KSI)
- **Depende de:** Fase 1 (`memory/self_improvement/`, mergeada a main 2026-06-14) y
  Fase 3 Proactividad (`proactivity/`, ya implementada)
- **Relacionado:** [[2026-06-14-knowledge-self-improvement-design]],
  [[2026-06-13-morning-briefing-design]], `proactivity/signals.py`,
  `memory/self_improvement/`

## 1. Problema y objetivo

Fase 1 dejó la memoria limpia y ponderada por confianza. Pero JARVIS no sabe **qué no
sabe**: hay proyectos con cards casi vacías, hechos que nadie reconfirma hace meses, y
contradicciones sin resolver. La Fase 2 hace que JARVIS **detecte esas lagunas y te
pregunte de forma natural y fluida** para llenarlas — convirtiendo cada respuesta tuya en
una mejora de la base de conocimiento.

Principio rector explícito de Isaac: **todo debe sentirse fluido y sin fricción**. Las
preguntas no pueden ser intrusivas, repetitivas ni "robóticas", y deben **auto-retirarse**
en cuanto las respondes (si JARVIS sigue preguntando algo ya resuelto, se siente roto).

"Recursivo": responder una pregunta mejora la card → la card mejorada retira la pregunta →
el detector apunta a la siguiente laguna. Cada ciclo deja menos huecos.

### No-objetivos (YAGNI)
- No es generación de conocimiento (JARVIS no inventa respuestas; solo pregunta).
- No es un cuestionario ni un formulario; es proactividad natural en conversación.
- No incluye "relaciones faltantes" entre proyectos (similitud cruzada de cards) — eso es
  la sub-fase 2b, fuera de alcance aquí.
- No llama al reasoner en un turno de voz: la formulación es offline al cierre.

## 2. Decisiones de diseño (acordadas en brainstorming)

| Decisión | Elección | Razón |
|----------|----------|-------|
| Tipos de laguna (MVP) | Card pobre, hecho obsoleto, contradicción abierta | Alto valor, reusan infra de F1. Relaciones faltantes → 2b |
| Enfoque | A: computar offline (KSI) + exponer en vivo (Proactividad) | Reasoner offline; turno de voz barato |
| Exposición | Pregunta proactiva en charla (+ briefing) | Elección de Isaac: más vivo que solo registro |
| Persistencia | Sección `## Preguntas abiertas` por card | Reusa cards; el detector de proactividad la lee |
| Autonomía | Aditivo autónomo (escribe preguntas); preguntar = vía cola gated | Consistente con F1; anti-spam gratis |
| Auto-retiro | Determinista por `gap_id`: gap ausente → `resolved` | Fluidez: deja de preguntar lo contestado |

## 3. Arquitectura (dos partes conectadas por la card)

```
[Al cierre — KSI]  memory/self_improvement/gaps.py   (NUEVO)
   detecta 3 lagunas (determinista) → reasoner formula preguntas (1 batch, presupuestado)
        → escribe "## Preguntas abiertas" en cada card (aditivo, dedup por gap_id)
        → marca resueltas las preguntas cuyo gap ya no existe
        → review log
   (cableado en KnowledgeImprover._run_inner, tras la consolidación de Fase 1)

            │  la card persiste las preguntas abiertas (fuente de verdad)
            ▼

[En vivo — Proactividad]  proactivity/  (EXTENSIÓN mínima)
   ProjectState gana `open_questions`  (lee "## Preguntas abiertas", excluye resueltas)
   _knowledge_gap (startup) + _ctx_knowledge_gap (contextual) → Signal(kind="knowledge_gap")
        → el motor ya existente las expone: briefing + charla proactiva (prompt-first)
```

**Cero cambios en `jarvis.py`**: el `KnowledgeImprover` (F1) y el `ProactivityEngine` (F3)
ya están cableados. Fase 2 solo añade un paso dentro del improver y detectores dentro del
engine.

## 4. Parte 1 — Detección y formulación (al cierre)

### Módulo nuevo: `memory/self_improvement/gaps.py`

**Tipo de dato:**
```python
@dataclass(frozen=True)
class KnowledgeGap:
    gap_id: str          # sha1(f"{kind}|{project}|{key}")[:16] — estable
    kind: str            # "poor_card" | "stale_fact" | "open_contradiction"
    project: str
    key: str             # firma legible: nombre de proyecto / texto del hecho / par
    question: str = ""   # la formula el reasoner; "" hasta entonces
```

**Detectores deterministas** (reusan F1 + ProjectState):

| `kind` | Función | Detección |
|--------|---------|-----------|
| `poor_card` | `detect_poor_cards(states, cfg)` | proyecto con `current_state` vacío **o** total de bullets < `min_card_bullets` (default 4), y `staleness_days` no None (proyecto con actividad) |
| `stale_fact` | `detect_stale_facts(events, cfg, today)` | evento cuya `decayed(confidence, learned_at, half_life)` < `stale_confidence` (default 0.3) y no `superseded_by` |
| `open_contradiction` | reusa `detectors.detect_contradictions(events)` | pares no superseded; `key` = `f"{a.id}|{b.id}"` |

**`gap_id`** = `sha1(f"{kind}|{project}|{key}")[:16]`. Estable entre corridas → base del dedup
y del auto-retiro.

**Formulación (reusa el patrón `judge` de F1):**
```python
def formulate_questions(reasoner, gaps, *, token_budget) -> list[KnowledgeGap]
```
- Si `reasoner is None` o `token_budget <= 0` o no hay gaps → devuelve los gaps sin
  `question` (no se escriben preguntas a medias).
- Una sola llamada batch al reasoner con todas las firmas → devuelve JSON `{gap_id: pregunta}`
  (con `_extract_json` self-heal de F1). Cada gap recibe su `question` natural y conversacional.
- Tono: pregunta directa y breve, en español, primera persona ("¿Sigue vigente X?"), sin
  jerga de "laguna detectada".

### Persistencia: `## Preguntas abiertas` en la card

`gaps.py` provee `apply_questions(vault, project, gaps_with_questions, active_gap_ids)`:
- Lee la card (si no existe, no hace nada — `poor_card` puede no tener card aún; en ese caso
  la pregunta se escribe igual creando la sección, ver abajo).
- Cada gap con `question` se serializa como bullet con tag ksi invisible:
  `- {fecha} {question} <!-- ksi-gap:{"gap_id":"...","kind":"...","status":"open"} -->`
- **Dedup**: si el `gap_id` ya está en la sección, no se reescribe.
- **Auto-retiro**: cualquier bullet de la sección cuyo `gap_id` **no** esté en
  `active_gap_ids` (los gaps detectados esta corrida) se reescribe con `"status":"resolved"`
  (aditivo: el bullet permanece como histórico, no se borra).
- Escritura vía `notes.write_note` (mismo helper que F1/triage).

### Integración en `KnowledgeImprover._run_inner`

Tras el bloque de consolidación de F1 (detección de duplicados/contradicciones ya ejecutada),
se añade:
```python
gaps = collect_gaps(states, events, self.config)            # determinista
gaps = formulate_questions(self.reasoner, gaps, token_budget=budget)
for project, project_gaps in group_by_project(gaps):
    apply_questions(vault, project, project_gaps, active_ids)
```
Todo dentro del `try/except` fail-safe existente. `states` se obtiene reusando
`proactivity.project_state.build_project_states(vault)`; `events` ya están cargados por F1.
El `budget` se comparte con el de F1 (se decrementa).

## 5. Parte 2 — Exposición en vivo (Proactividad)

### `ProjectState` gana `open_questions`

En `proactivity/project_state.py`, `ProjectState` añade el campo
`open_questions: list[str] = field(default_factory=list)` y `build_project_states` lo puebla
con `section_bullets(sections, "Preguntas abiertas")` **filtrando** las que tengan
`status":"resolved"` en su tag ksi-gap. (Las resueltas no se exponen.)

### Detectores nuevos en `proactivity/signals.py`

```python
def _knowledge_gap(states, cfg) -> list[Signal]:        # startup
    # una señal por pregunta abierta; prioridad base 0.5
def _ctx_knowledge_gap(active_project, states) -> list[Signal]:  # contextual
    # preguntas del proyecto activo; prioridad 0.72 (relevante al momento)
```
Cada `Signal`: `kind="knowledge_gap"`, `payload={"snippet": pregunta, "gap_id": id}`.
Se añaden a `detect_startup_signals` y `detect_contextual_signals` respectivamente.

Para extraer `gap_id` desde el bullet persistido, `project_state` guarda
`open_questions` como lista de `(texto, gap_id)` — o, más simple, `open_questions` lista de
dicts `{"text":..., "gap_id":...}`. **Decisión:** lista de dicts, para no romper el tipo
plano que ya consumen otros detectores (que usan `open_pendings: list[str]`). Los nuevos
detectores leen `q["text"]` y `q["gap_id"]`.

### Dedup estable por `gap_id`

`proactivity/opportunity_queue.opportunity_id(signal)` se extiende (cambio quirúrgico) para
preferir `signal.payload.get("gap_id")` antes que `pending/decision/snippet`. Así, aunque el
reasoner refrasee, la misma laguna no se ofrece dos veces y el cooldown funciona por laguna,
no por texto.

### `_WHAT_BY_KIND`

Se añade: `"knowledge_gap": "Preguntar algo que falta saber del proyecto"`.

### Verbalización

Prompt-first: el bloque de PROACTIVIDAD en `gemini/system_prompt.py` ya instruye a Gemini a
mencionar oportunidades con naturalidad. `knowledge_gap` entra por el mismo canal; no se
añade código de verbalización. (Consistente con la preferencia de modulación por prompt.)

## 6. Fluidez y anti-spam

- **Cooldown / dedup / tope por sesión**: heredados de `OpportunityQueue` sin código nuevo.
  JARVIS pregunta como mucho `max_per_session` cosas; una pregunta descartada no vuelve en
  `cooldown_days`.
- **Auto-retiro**: el gap resuelto deja de emitir señal (la card ya no lo lista como open).
- **Relevancia contextual**: `_ctx_knowledge_gap` prioriza lo del proyecto que estás
  tocando → la pregunta llega cuando encaja, no al azar.
- **Degradación elegante**: sin budget, no se formulan preguntas nuevas (no hay bullets a
  medias); las ya escritas siguen exponiéndose.

## 7. Manejo de errores

`gaps.py` corre dentro de `KnowledgeImprover._run_inner`, ya envuelto en `try/except`
fail-safe absoluto (F1). Los detectores de proactividad corren dentro de la fachada
fail-safe de `ProactivityEngine` (ningún método propaga). Cero superficie de crash nueva en
el runtime de voz.

## 8. Testing (TDD)

`PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/ -k "gap or ksi or proactiv"`

- `test_ksi_gaps.py`:
  - `detect_poor_cards`: card con `current_state` vacío → gap; card rica → no.
  - `detect_stale_facts`: evento con confianza decaída < umbral → gap; fresco → no.
  - `open_contradiction`: par contradictorio no superseded → gap.
  - `gap_id` estable e idempotente.
  - `formulate_questions`: sin budget → gaps sin `question`; con fake reasoner → preguntas
    asignadas por `gap_id`; JSON malo → sin questions, sin crash.
  - `apply_questions`: escribe bullet con tag; dedup (no reescribe gap_id existente);
    auto-retiro (gap ausente → `status:resolved`).
- `test_proactivity_knowledge_gap.py`:
  - `ProjectState.open_questions` excluye resueltas.
  - `_knowledge_gap` emite una señal por pregunta abierta con `gap_id` en payload.
  - `_ctx_knowledge_gap` prioriza el proyecto activo.
  - `opportunity_id` keyea por `gap_id` (dos señales mismo gap_id, distinto texto → mismo id).
  - `_WHAT_BY_KIND["knowledge_gap"]` presente y renderiza vía `_suggestion_struct`.

## 9. Archivos nuevos / tocados

**Nuevos:** `memory/self_improvement/gaps.py`, `tests/test_ksi_gaps.py`,
`tests/test_proactivity_knowledge_gap.py`.
**Tocados:**
- `memory/self_improvement/improver.py` — llamar al pipeline de gaps en `_run_inner`.
- `proactivity/project_state.py` — campo `open_questions` + poblarlo (filtrando resueltas).
- `proactivity/signals.py` — `_knowledge_gap` + `_ctx_knowledge_gap` + añadirlos a los
  composers.
- `proactivity/opportunity_queue.py` — `opportunity_id` prefiere `gap_id`;
  `_WHAT_BY_KIND["knowledge_gap"]`.
- `.env.example` — `JARVIS_KSI_MIN_CARD_BULLETS`, `JARVIS_KSI_STALE_CONFIDENCE`.
- `CHANGELOG.md`.

## 10. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Preguntas repetitivas/molestas | Cooldown + dedup por `gap_id` + tope por sesión + auto-retiro |
| Preguntar algo ya contestado | Auto-retiro determinista; responder llena la card y retira el gap |
| Card pobre sin card existente | `apply_questions` crea la sección/card mínima si hace falta |
| Reasoner refrasea → dedup roto | `opportunity_id` keyea por `gap_id`, no por texto |
| Falsos "hecho obsoleto" por decay agresivo | Umbral `stale_confidence` configurable; half-life de F1 ya conservador (45d) |
| Costo de tokens al cierre | Una sola llamada batch, presupuestada y compartida con F1 |
| Ruido de contradicciones heurísticas | Reusa el mismo detector de F1 (ya validado); el reasoner formula, el humano decide |
