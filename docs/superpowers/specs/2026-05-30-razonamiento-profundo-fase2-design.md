# Diseño: Fase 2 — Razonamiento Profundo con Auto-Contexto

**Fecha:** 2026-05-30
**Proyecto:** JARVIS — Asistente Integral en Obsidian
**Parte de:** Roadmap de evolución de JARVIS (Fase 2 de 3)
**Estado:** Aprobado en brainstorming

---

## Contexto y motivación

Hoy `ask_claude_deep` (el tool que delega razonamiento profundo a Claude Sonnet 4.6)
recibe `context_extra` **solo si el modelo Gemini Live decide pasarlo manualmente**.
En la práctica casi nunca lo hace bien: Claude razona "a ciegas" sobre los proyectos
de Isaac, sin saber qué se decidió antes ni dónde quedaron la última sesión.

La Fase 1 (Continuidad entre sesiones) ya dejó la materia prima: notas-diario de
sesión con recall, y el commit de Memory Triage + Project Memory Cards dejó tarjetas
vivas por proyecto. Falta **conectar esa memoria al razonamiento**: que cuando JARVIS
delegue a Claude sobre un proyecto, automáticamente le inyecte el contexto relevante.

Esta es la **Fase 2** de un roadmap de 3 fases. La Fase 3 (proactividad) queda fuera
de alcance de este spec.

## Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección |
|----------|----------|
| Fuentes del auto-contexto | Project Memory Card + resumen de sesión previa + RAG top-3 |
| Trigger | Solo cuando se detecta un proyecto conocido en el prompt |
| Alcance del RAG | Todo el vault (permite conexiones cross-proyecto) |
| Presupuesto de tokens | Equilibrado (~2500 tokens; trunca RAG primero, conserva la card) |
| Merge con `context_extra` del modelo | Concatenar: lo del modelo primero, el auto-contexto al final |
| Arquitectura | Módulo aislado `memory/context_assembler.py` (Enfoque 2) |

## Goals

- Cuando `ask_claude_deep` recibe un prompt que menciona un proyecto conocido, JARVIS
  ensambla automáticamente un bloque de contexto y lo pasa a Claude sin intervención
  del usuario ni del modelo Gemini.
- El contexto combina tres fuentes: la Project Memory Card del proyecto, el resumen
  de la última sesión (Fase 1) y las top-3 memorias relevantes por RAG (todo el vault).
- Respetar un presupuesto de tokens (~2500) truncando por prioridad: Card > Recall > RAG.
- Conservar cualquier `context_extra` que el modelo Gemini pase manualmente,
  concatenándolo **antes** del auto-contexto.
- Fail-safe absoluto: un fallo al armar contexto nunca rompe el razonamiento; Claude
  responde igual, con o sin contexto.
- Respetar la postura de seguridad: el contexto sale de notas ya redactadas por el
  triage; no se reintroduce contenido sensible.

## Non-Goals (fuera de alcance)

- Fase 3 (proactividad): que JARVIS sugiera acciones por su cuenta.
- Reranking sofisticado del RAG o embeddings nuevos (se usa el índice existente).
- Inyección de contexto en el camino de Gemini Live (solo en el de Claude/reasoner).
- Tokenización exacta (tiktoken): se usa la heurística barata `len//4`.

---

## Arquitectura

### Componente nuevo: `memory/context_assembler.py`

Módulo aislado con responsabilidad única. No conoce la API de Anthropic ni el
dispatcher de tools — solo lee del vault y del RAG.

```python
@dataclass(frozen=True)
class ContextResult:
    text: str                 # bloque ensamblado (o "" si no hay nada)
    project: str | None       # proyecto detectado (o None)
    sources: list[str]        # etiquetas de qué se incluyó: ["card", "session", "rag:3"]

def build_project_context(
    vault: ObsidianVault,
    rag: VaultRAG,
    prompt: str,
    *,
    token_budget: int = 2500,
) -> ContextResult:
    ...
```

### Interfaz y dependencias

- **Qué hace:** convierte un prompt en un bloque de contexto de proyecto en texto.
- **Cómo se usa:** lo llama el handler `ask_claude_deep`; el resultado se concatena
  al `context_extra` y se pasa al reasoner.
- **De qué depende:** `triage` (detección de proyecto y ruta de la card),
  `session_summary.load_last_summary` (Fase 1), `notes` (lectura de la card),
  `VaultRAG.search`. Todas dependencias internas ya existentes.

---

## Data flow

```
prompt
  │
  ├─▶ triage.detect_project(prompt)
  │       │
  │       ├─ None  ──▶ ContextResult("", None, [])   (trigger: solo si hay proyecto)
  │       │
  │       └─ "Polymath IDE"
  │
  ├─▶ 1. Project Memory Card
  │       triage.project_card_path(vault, proyecto) → notes.read_note → cuerpo
  │       (si no existe la card: se omite, no rompe)
  │
  ├─▶ 2. Recall de sesión previa
  │       session_summary.load_last_summary(vault, max_chars) → Resumen + Pendientes
  │       (si no hay sesión previa: se omite)
  │
  ├─▶ 3. RAG top-3
  │       rag.search(prompt, top_k=3) sobre todo el vault
  │       filtra por score mínimo (descarta chunks irrelevantes)
  │
  └─▶ ensambla en secciones con headers + aplica presupuesto de tokens
          prioridad al truncar: Card > Recall > RAG (RAG se recorta primero)
          → ContextResult(text, "Polymath IDE", ["card", "session", "rag:2"])
```

### Formato del bloque ensamblado

```
═══════════ CONTEXTO DE PROYECTO: Polymath IDE ═══════════

## Memory Card
<cuerpo de la Project Memory Card>

## Sesión anterior
<recall de la última sesión, vía load_last_summary>

## Memorias relacionadas
- [score 0.78] <snippet del chunk RAG 1>
- [score 0.71] <snippet del chunk RAG 2>

══════════════════════════════════════════════════════════
(Contexto recuperado automáticamente por JARVIS. Úsalo solo si viene al caso.)
```

Las secciones cuya fuente esté vacía se omiten por completo (sin header huérfano).
Si no se incluye ninguna fuente, `text` es `""` y el handler no añade nada.

### Presupuesto de tokens

- Estimación: `estimate_tokens(s) = len(s) // 4` (heurística barata, sin dependencia
  nueva, coherente con el principio "barato en el camino crítico" del triage).
- Algoritmo: se incluye la Card completa primero; luego el recall; luego se van
  añadiendo chunks RAG mientras quepan. Si la Card sola excede el presupuesto, se
  trunca la Card a `token_budget` (caso extremo) y se omiten recall y RAG.

---

## Integración con el handler

En `memory/tools.py`, tanto `ask_claude_deep` como `ask_claude_deep_async`:

```python
def _augmented_context(ctx, prompt, context_extra):
    try:
        auto = build_project_context(ctx.vault, ctx.rag, prompt)
    except Exception as exc:
        logger.warning("auto-contexto falló: %s", exc)
        return context_extra  # fail-safe: sigue con lo que haya
    return _merge_context(context_extra, auto.text)

def _merge_context(model_ctx: str | None, auto_ctx: str) -> str | None:
    parts = [p for p in (model_ctx, auto_ctx) if p and p.strip()]
    return "\n\n".join(parts) if parts else None
```

- El `context_extra` del modelo Gemini va **primero**; el auto-contexto, **al final**.
- El reasoner ya coloca `context_extra` en un segundo bloque `cache_control: ephemeral`,
  así que el costo se amortiza en preguntas seguidas del mismo proyecto.

---

## Manejo de errores (fail-safe)

Mismo principio que Fase 1: **nunca romper el razonamiento por un fallo de contexto.**

- `build_project_context` envuelve cada fuente en try/except interno: si falla la
  lectura de la card, el RAG, o el recall, captura, loguea por `logger` y devuelve
  lo que haya logrado armar.
- El handler vuelve a envolver la llamada completa: si `build_project_context` lanza,
  degrada a `context_extra` original.
- Claude **siempre** responde, con o sin contexto enriquecido.

---

## Testing (TDD)

Tests nuevos en `tests/test_context_assembler.py`, con vault temporal y `FakeRAG`
(mismo patrón que `tests/test_memory_remember.py`):

1. **Sin proyecto detectado** → `text == ""`, `project is None`, `sources == []`.
2. **Proyecto detectado con card** → incluye el cuerpo de la card; `"card" in sources`.
3. **Card + recall de sesión** → incluye ambos con sus headers; `sources` los lista.
4. **RAG con chunks sobre umbral** → top-3 incluidos; bajo el umbral → excluidos.
5. **Presupuesto excedido** → trunca RAG primero, conserva la card completa.
6. **Card ausente pero proyecto detectado** → degrada con recall + RAG, sin romper.
7. **Fail-safe**: una fuente que lanza excepción → devuelve el resto, no propaga.
8. **Integración** en `tests/test_memory_remember.py` o nuevo: `ask_claude_deep` con
   reasoner fake → verifica que el `context_extra` que recibe el reasoner contiene el
   bloque auto-ensamblado **después** del context_extra del modelo.

La suite completa (actualmente 82 tests) debe seguir verde.

---

## Archivos afectados

| Archivo | Cambio |
|---------|--------|
| `memory/context_assembler.py` | **nuevo** — `build_project_context` + `ContextResult` + helpers |
| `memory/tools.py` | `ask_claude_deep` y `ask_claude_deep_async` llaman al assembler y mergean |
| `tests/test_context_assembler.py` | **nuevo** — 7 tests unitarios |
| `tests/test_memory_remember.py` | (opcional) test de integración del merge en el handler |

Sin cambios en la declaración del tool (`ASK_CLAUDE_DEEP_DECL`): el auto-contexto es
transparente al modelo Gemini, que sigue viendo los mismos parámetros.
