# Fase 4 — Auto-crítica en escritura (write-time self-critique)

**Fecha:** 2026-06-19
**Estado:** Diseño aprobado, pendiente de plan
**Roadmap:** KSI (Knowledge Self-Improvement) — cuarta fase
**Predecesoras:** Fase 1 (consolidación aditiva), Fase 2 (detección de lagunas), Fase 3 (RAG auto-curado)

## Resumen

JARVIS gana la capacidad de **criticar y refinar una memoria en el momento de
escribirla**. Cuando `jarvis_remember` recibe un `content` vago o sin precisión,
un módulo determinista lo detecta y el reasoner lo reescribe para que sea preciso
antes de persistirlo. Si tras el refinamiento queda duda objetiva, se marca el
texto inline para auditoría. Siempre escribe; nunca bloquea ni pregunta.

Es la fase más simple del roadmap: **stateless** (no mantiene archivos de
métricas como F3), **autónoma aditiva** (cero HITL, cero fricción) y con un
**único seam** de integración.

## Principio rector

"Todo debe sentirse fluido y sin fricción" (Isaac, Fase 2). F4 actúa en silencio
en el camino de escritura: el usuario nunca ve un prompt, solo nota que sus
memorias quedan mejor redactadas.

## Alcance

### Dentro

- Detección determinista de vaguedad/imprecisión en `content`.
- Refinamiento del bullet vía reasoner (presupuestado, 1 batch, JSON self-heal).
- Marcador inline `<!-- ksi-doubt:vague -->` cuando el reasoner no puede concretar.
- Seam síncrono en `jarvis_remember`, después de que `triage_memory` aprueba.
- Gating por config `JARVIS_KSI_WRITE_CRITIQUE` (default OFF).

### Fuera (decisiones explícitas)

- **Solo `jarvis_remember`** como hook point. NO `synthesize_and_save` (cierre de
  sesión) ni otras rutas de escritura.
- **Solo señal "vago/sin precisión".** NO detección de:
  - afirmaciones sin fuente,
  - contradicción con la card (lo cubre F1 al cierre),
  - duplicados (lo cubre F1 al cierre).
- NO bloquea la escritura. NO pregunta al usuario. NO usa `OpportunityQueue`.
- NO estado persistente propio (sin archivo de métricas, sin housekeeping).

## Arquitectura

Módulo nuevo: `memory/self_improvement/write_critique.py`. Espeja el patrón
determinista-barato + reasoner-solo-en-candidatos de F1–F3.

### API pública

```python
@dataclass(frozen=True)
class CritiqueResult:
    text: str       # content refinado (o el original si no se tocó / fail-safe)
    doubt: bool     # True si el reasoner no pudo concretar tras refinar

def detect_vague(text: str) -> bool:
    """Determinista, sin red, sin reasoner. Marca candidatos a refinar."""

def refine(reasoner, text: str, *, budget) -> CritiqueResult:
    """SOLO se llama sobre texto marcado vago. Una llamada presupuestada."""

def critique(reasoner, text: str, *, enabled: bool, budget) -> CritiqueResult:
    """FACADE fail-safe — único punto de entrada que usa jarvis_remember."""
```

### Responsabilidades

- **`detect_vague`** — screening léxico puro, **bilingüe ES/EN** (las memorias de
  Isaac mezclan ambos idiomas). Marca vago un texto con muletillas de imprecisión
  **Y** ausencia de concreción (sin números, sin nombres propios / rutas /
  identificadores, por debajo de un umbral de longitud). Cero coste. La mayoría de
  los `jarvis_remember` ya son precisos → pasan sin tocar al reasoner.
  - Léxico ES: *"algo", "varios", "creo", "más o menos", "etc.", "no estoy
    seguro", "como que", "tal vez", "supongo", "cosas"*.
  - Léxico EN: *"some", "a few", "several", "I think", "kind of", "sort of",
    "maybe", "I guess", "stuff", "things", "not sure", "etc."*.
  - El matching es por palabra/límite (no substring) y case-insensitive, en una
    constante de módulo única que une ambos idiomas; se afina la lista exacta en
    el plan/TDD.

- **`refine`** — solo se invoca si `detect_vague` dio `True`. Una llamada
  presupuestada al reasoner que reescribe el bullet para que sea preciso,
  reusando `_extract_json` para self-heal del JSON. Devuelve texto refinado y
  flag `doubt`.

- **`critique`** — la fachada fail-safe, el **único** que llama `jarvis_remember`.
  Orquesta:
  - `enabled == False` → `CritiqueResult(text, doubt=False)` intacto.
  - `not detect_vague(text)` → `CritiqueResult(text, doubt=False)` sin tocar reasoner.
  - vago → `refine`.
  - **cualquier excepción** → `CritiqueResult(text_original, doubt=False)`.

### Aislamiento

El módulo no conoce vault, ni Obsidian, ni FAISS — solo recibe texto y un
reasoner, devuelve texto. Testeable en aislamiento total con un reasoner fake.
Todo el acoplamiento con JARVIS vive en el seam de `tools.py`.

## Flujo de datos

```
jarvis_remember(ctx, title, content, tags)
  │
  ├─ triage = triage_memory(title, content, tags)
  │     └─ if not triage.should_save: return  (F4 no entra)
  │
  ├─ [SEAM F4]  result = write_critique.critique(
  │                 ctx.reasoner, content,
  │                 enabled=<gate>, budget=<1 batch>)
  │     ├─ enabled False          → CritiqueResult(content, False)
  │     ├─ detect_vague False      → CritiqueResult(content, False)  (0 reasoner)
  │     ├─ detect_vague True       → refine(reasoner, content)
  │     │     ├─ JSON ok           → CritiqueResult(text, doubt)
  │     │     └─ JSON corrupto     → _extract_json → si falla → original
  │     └─ excepción               → CritiqueResult(content, False)
  │
  ├─ content = result.text
  │  if result.doubt: content += "\n<!-- ksi-doubt:vague -->"
  │
  ├─ note = write standalone note  (body = content refinado)
  ├─ index note
  └─ update_project_memory_card(... content=content)   (bullet = content refinado)
```

### Invariantes

1. **Un solo punto de refinamiento.** `content` se reescribe una vez tras el seam;
   nota standalone y card heredan el texto refinado sin lógica duplicada.
2. **Presupuesto.** `refine` consume 1 batch del presupuesto del reasoner (igual
   que F1/F2). `jarvis_remember` es una tool de fondo, no el turno de voz, así que
   el +1-3s no corta conversación. Presupuesto agotado → original (no espera).
3. **Contrato del reasoner.** Prompt pide JSON estricto:
   `{"text": "<bullet preciso>", "doubt": <bool>}`. `doubt=true` cuando el reasoner
   no pudo concretar por falta de información objetiva en el input (no inventa
   datos — solo reescribe lo que hay). Self-heal vía `_extract_json` existente.
4. **Marcador de duda.** `doubt=True` → se anexa `<!-- ksi-doubt:vague -->` al
   final del content. Espeja el formato de comentario HTML de los `ksi-gap` de F2:
   invisible en el render de Obsidian, grep-able para auditoría/futura cosecha.
5. **Stateless.** F4 no guarda nada fuera de lo que ya escribe `jarvis_remember`.
   Sin housekeeping, sin archivo de métricas.

## Autonomía y fail-safe

- **100% aditivo autónomo, cero HITL, cero fricción.** No propone a la
  `OpportunityQueue`, no pregunta, no bloquea.
- **Fail-safe (contrato duro, idéntico a F1–F3).** Ningún método propaga
  excepción. La fachada `critique` envuelve todo en try/except y ante cualquier
  fallo devuelve `CritiqueResult(original_content, doubt=False)`.
- **Garantía:** `jarvis_remember` **siempre** escribe, con o sin F4, con texto
  refinado o con el original. F4 jamás puede causar que una memoria no se guarde.

## Configuración

Nuevo campo en `KnowledgeImproverConfig` (`config.py`), default OFF como F3:

```python
write_critique_enabled: bool = False   # gate JARVIS_KSI_WRITE_CRITIQUE
```

`from_env` lee `JARVIS_KSI_WRITE_CRITIQUE`. Con flag OFF, `content` pasa intacto
(comportamiento actual idéntico, regresión cero). Documentado en `.env.example`
(bloque "KSI Fase 4") y `CHANGELOG.md`.

## Wiring

Mínimo, porque F4 no necesita estado:

- `ToolContext` ya expone `ctx.reasoner` → el seam lo usa directo. **No** se
  inyecta objeto nuevo en `ToolContext` (a diferencia de F3 con `retrieval_curator`).
- El gate se lee del `_ksi_cfg` que `jarvis.py` ya construye; se expone a
  `jarvis_remember` como un bool en `ToolContext` (`write_critique_enabled`).
- **No hay constructor nuevo en `jarvis.py`** — solo lectura de config existente.

## Testing (TDD, pytest)

Módulo testeable en aislamiento total con reasoner fake:

- **`detect_vague`**: tabla de casos **ES y EN** — vago real (muletillas +
  sin concreción) → True en ambos idiomas; preciso (con número/nombre/ruta) →
  False; borde (corto pero concreto) → False. Sin red.
- **`refine`**: reasoner fake → JSON válido → texto + doubt correcto; JSON
  corrupto → self-heal; self-heal falla → original.
- **`critique`** (fachada): enabled False → original sin llamar al fake; no-vago →
  original sin llamar al fake (assert reasoner no invocado); vago → refinado;
  reasoner que lanza excepción → original, doubt False.
- **Seam en `jarvis_remember`**: flag OFF → content intacto en nota y card; flag
  ON + vago → ambos destinos reciben el refinado; flag ON + doubt → marcador
  `<!-- ksi-doubt:vague -->` presente. Reusa fakes de vault/reasoner existentes.

Runner: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest tests/test_write_critique.py -q`
(+ el archivo de tests de tools que ya exista; se confirma en el plan).

## Riesgos y mitigaciones

- **Refinamiento agresivo que inventa datos.** Mitigación: el contrato del prompt
  prohíbe inventar — `doubt=true` en vez de fabricar. Solo reescribe lo presente.
- **Latencia en `jarvis_remember`.** Aceptada: es tool de fondo, no turno de voz;
  +1-3s solo cuando hay vaguedad real (camino minoritario).
- **Falsos positivos de `detect_vague`.** Si refina algo que no era vago, el
  reasoner lo deja casi igual (no es destructivo); coste = 1 batch ocasional.
  Default OFF permite observar antes de activar.

## Plan de salida

Tras aprobación del spec → `writing-plans` → implementación subagent-driven
(tarea por unidad: módulo + tests, seam + tests, config + docs) → review →
`finishing-a-development-branch` (FF merge a main, espejo de F1–F3).
