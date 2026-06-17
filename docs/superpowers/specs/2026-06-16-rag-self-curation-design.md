# KSI Fase 3 — RAG auto-curado (re-ranking por uso real)

Fecha: 2026-06-16
Estado: diseño aprobado
Fase previa: [[2026-06-15-knowledge-gap-detection-design]] (Fase 2, mergeada)

## Problema

El RAG de JARVIS (`memory/rag.py`, `VaultRAG` sobre FAISS + MiniLM-L6-v2) recupera
los top-k chunks por similitud coseno y los inyecta en el contexto del reasoner
(`memory/context_assembler.py` → `build_project_context`). La relevancia se mide
*una sola vez*, en el momento de indexar: un chunk que sale siempre en el top-k pero
que el reasoner nunca usa sigue ocupando presupuesto de contexto indefinidamente, y
no hay señal de cuáles fragmentos son realmente útiles.

Fase 3 cierra ese lazo: medir el **uso real** de cada chunk por el reasoner y
re-rankear las recuperaciones futuras en consecuencia. Es la primera fase KSI que
toca el path de razonamiento en vivo (F1/F2 corrían solo al cierre de sesión), porque
medir uso real lo exige. Por eso la regla dura es: **nunca degradar la respuesta**.

## Alcance

**Incluye:** medición de uso por chunk, re-ranking autónomo no destructivo de las
recuperaciones, housekeeping al cierre de sesión.

**Excluye (YAGNI):** re-indexar, borrar chunks, reescribir notas del vault, tocar la
`OpportunityQueue`, segundo modelo de embeddings, atribución en turnos de voz simples.

## Arquitectura

Nuevo módulo `memory/self_improvement/retrieval_curation.py` con la clase
`RetrievalCurator`, que mantiene estado de comportamiento en
`data/rag_usage.json` (escritura atómica tmp+`os.replace`).

El curator es un **decorador de recuperación**, no dueño de datos: la fuente de
verdad sigue siendo el índice FAISS + el vault. `rag_usage.json` es caché
desechable — borrarlo devuelve todo a neutral. Por eso el re-ranking puede ser
autónomo sin riesgo y encaja en el modelo evento->proyección->índice de F1.

### Clave estable de chunk

`chunk_id` de `VaultRAG` es incremental y **no sobrevive a un reindex**. El curator
usa una clave content-addressed:

```
chunk_key = sha1(rel_path + "|" + normalized_text).hexdigest()[:16]
```

donde `normalized_text` es el texto del chunk con whitespace colapsado. Mismo
`rel_path` + mismo texto -> misma key tras reindex. Si el texto del chunk cambia, es
una key nueva (legítimo: es contenido distinto).

### Estado persistido (`data/rag_usage.json`)

```json
{
  "chunks": {
    "a1b2c3d4e5f6a7b8": {"retrieved": 12, "used": 7, "last_used": "2026-06-16"}
  },
  "pending": {"<prompt_hash>": ["chunk_key1", "chunk_key2"]}
}
```

`pending` keyea por hash del prompt (no por turno): si el reasoner nunca responde
(timeout/cancelación async), el pending no se cuenta como "no usado" y caduca en el
housekeeping. Así no se ensucia la señal.

## Flujo de datos (turno de razonamiento profundo)

1. **Recuperación** — tras `searcher.search()` en `build_project_context`:
   - `curator.rerank(results)` -> multiplica cada `score` por `quality_factor(key)`,
     reordena, y aplica el resultado **antes** del filtro `MIN_RAG_SCORE`.
   - `curator.note_retrieval(prompt, results)` -> `retrieved++` por key y guarda los
     keys en `pending[prompt_hash]`.
2. **Respuesta** — tras `r = reasoner.ask(...)` en `ask_claude_deep`/`_async`:
   - `curator.attribute_usage(prompt, r.text)` -> recupera los keys pending, embebe en
     **un solo batch** `[r.text, chunk_text_1..k]`, y por cada chunk con
     `coseno(respuesta, chunk) >= use_threshold` hace `used++` + `last_used=hoy`.
     Borra el pending.

## Señal de calidad

Mapeo **lineal centrado en 0.5** de la tasa `used/retrieved` al factor en
`[factor_floor, factor_ceil]` = `[0.6, 1.4]` (tasa 0.5 -> 1.0 neutral; 0% -> 0.6;
100% -> 1.4):

```
factor = clamp(factor_floor + rate * (factor_ceil - factor_floor),
               factor_floor, factor_ceil)
```

- **Arranque en frío:** `retrieved < cold_start_min` (≈5) -> factor 1.0. No castigo lo
  que no he medido lo suficiente.
- **Clamp [0.6, 1.4]:** hace el re-ranking suave y reversible. Ningún chunk se
  silencia del todo (un cambio de tema en los proyectos puede rehabilitarlo) ni
  domina artificialmente.

## Autonomía y fail-safe

- Re-ranking **100% autónomo**: solo reordena/filtra en memoria, nunca toca FAISS ni
  el vault. Reversible borrando `rag_usage.json`. No entra a la `OpportunityQueue`.
- **Path caliente protegido:** cada seam se envuelve en try/except (igual que el
  `_augmented_context` actual). Si el curator lanza, se devuelven los resultados sin
  reordenar y el razonamiento sigue. El curator **nunca** degrada la respuesta de voz.
- `attribute_usage` solo corre en `ask_claude_deep`/`_async` (razonamiento profundo),
  no en cada turno. Reusa el MiniLM ya cargado por el RAG — sin segundo modelo.

## Wiring (mínimo)

- `ToolContext` gana `retrieval_curator`, instanciado en `build()` solo si
  `JARVIS_RAG_CURATION=true`.
- `build_project_context(..., curator=None)` — param opcional; si viene, `rerank` +
  `note_retrieval` tras el search.
- `ask_claude_deep` / `ask_claude_deep_async` — `attribute_usage(prompt, r.text)` tras
  la respuesta, en try/except.
- **Housekeeping** en `improver._run_inner` (cierre KSI): decae cuentas viejas
  (`usage_decay_days`) y purga keys de chunks ausentes del manifest actual. Reusa el
  trigger de sesión existente — **cero cambios en `jarvis.py`**.

## Config (`KnowledgeImproverConfig`, patrón `from_env`)

| Campo | Default | Significado |
|-------|---------|-------------|
| `rag_curation_enabled` | `false` | Activa el curator (también gated por `JARVIS_RAG_CURATION`) |
| `use_threshold` | `0.55` | Coseno mínimo respuesta<->chunk para contar como "usado" |
| `cold_start_min` | `5` | Recuperaciones mínimas antes de salir de factor neutral |
| `factor_floor` | `0.6` | Multiplicador mínimo del score |
| `factor_ceil` | `1.4` | Multiplicador máximo del score |
| `usage_decay_days` | `45` | Vida media para decaer cuentas en housekeeping |

## Testing (TDD, sin tocar en rojo el path de voz)

- `chunk_key` estable: mismo `rel_path`+texto -> misma key tras reindex; texto distinto
  -> key distinta.
- `quality_factor`: tabla lineal (5 puntos) + cold-start neutral por debajo de 5
  muestras.
- `rerank`: reordena por `score*factor`, respeta el clamp, no muta el orden con
  curator vacío (todo neutral).
- `attribute_usage`: con `embed_fn` fake, coseno alto -> `used++`; bajo -> no
  incrementa; limpia el pending.
- **fail-safe:** `embed_fn` que lanza -> `rerank` devuelve la lista intacta;
  `attribute_usage` no propaga.
- persistencia atómica round-trip; housekeeping purga keys huérfanas y decae cuentas.

## Relación con otras fases

- [[2026-06-14-knowledge-self-improvement-design]] (F1): reusa MiniLM, trigger de
  cierre, patrones fail-safe/`from_env`, modelo evento->proyección->índice.
- [[2026-06-15-knowledge-gap-detection-design]] (F2): F3 es la primera que engancha en
  el seam caliente que F1/F2 evitaban a propósito.
- Pendiente F4 (auto-crítica en escritura), 2b (relaciones faltantes).
