# Auto-mejora recursiva de conocimiento — Diseño

- **Fecha:** 2026-06-14
- **Estado:** Diseño aprobado (brainstorming)
- **Autor:** Isaac + JARVIS
- **Capa elegida:** Conocimiento/memoria (no comportamiento, no código)
- **Relacionado:** [[2026-06-13-morning-briefing-design]], roadmap KAG (Fases 1-3 ✅),
  `proactivity/`, `memory/triage.py`, `memory/session_journal.py`

## 1. Problema y objetivo

JARVIS acumula conocimiento (notas de sesión, Project Memory Cards, memorias) pero
nunca lo **revisa ni mejora**: los duplicados crecen, las contradicciones conviven, y
la confianza de cada hecho es estática. Queremos un loop de **auto-mejora recursiva del
saber** que, al cerrar cada sesión, deje la base de conocimiento más limpia y mejor
ponderada para la próxima — sin tocar código ni comportamiento, y sin destruir datos.

"Recursivo" significa: cada fase opera sobre el output mejorado de la anterior
(consolidar → detectar lagunas → curar retrieval → evitar basura nueva).

### No-objetivos (YAGNI)
- No es auto-modificación de código.
- No es un loop de auto-crítica en tiempo real por turno.
- No es un event store distribuido, base de datos, ni grafo formal (eso converge con KAG después).
- No es bitemporalidad completa (solo `learned_at` + `superseded_by`).

## 2. Decisiones de diseño (acordadas en brainstorming)

| Decisión | Elección | Razón |
|----------|----------|-------|
| Capa de mejora | Conocimiento/memoria | Máximo valor, mínimo riesgo, capitaliza infra existente |
| Enfoque | Híbrido C: detector determinista → reasoner enfocado | Costo acotado; mismo ADN que `triage`+`synthesize` |
| Disparador | Al cerrar sesión (`_save_session_memory`) | Trabaja sobre lo recién aprendido, cero latencia en vivo |
| Autonomía | Autónomo solo aditivo; destructivo vía HITL | Nunca se destruye un evento; reversible siempre |
| Modelo de datos | Evento append-only → proyección regenerable | Reversibilidad por reconstrucción, no por backup frágil |
| Alcance de este spec | **Solo Fase 1** | Cada fase tiene su propio spec→plan→implement |

## 3. Modelo mental: eventos → proyección → índice

```
EVENTOS (append-only, inmutable)          ← fuente de verdad
  session_journal.py (JSONL) + bullets de cards con id/provenance
        │  (consolidación = recomputar)
        ▼
CANÓNICO (proyección, regenerable)        ← Project Memory Cards
  creencia actual, deduplicada, con confianza ponderada
        │  (indexar)
        ▼
RAG / índice (artefacto derivado, descartable)  ← FAISS MiniLM
```

**Sustrato ya existente que formalizamos:**
- Eventos → `memory/session_journal.py` ya es JSONL append-only crash-safe.
- Proyección → Project Memory Cards (`memory/triage.py`).
- Índice → FAISS de `memory/rag.py` / `memory/semantic.py` (ya reconstruible).
- `SourceDocument` ya lleva `confidence`, `date`, `project`.

## 4. Roadmap por fases

| Fase | Capacidad | Detector determinista | Reasoner | Acción |
|------|-----------|------------------------|----------|--------|
| **1 (este spec)** | Modelo evento→proyección + **consolidación aditiva** | clusters de duplicados (coseno MiniLM) + contradicciones heurísticas | redacta fusión/supersesión propuesta | regenera proyección (aditivo) + encola destructivo (HITL) |
| 2 | Detección de lagunas | cards pobres / secciones vacías / contradicciones | formula preguntas abiertas | sección "Preguntas abiertas" + briefing |
| 3 | RAG auto-curado | hits/ruido por chunk (telemetría de recall) | — | re-pesa / poda / reindexa |
| 4 | Auto-crítica en escritura | hook en `triage_memory` | critica antes de persistir | reescribe/rechaza en el momento |

El orden es el motor recursivo: Fase 1 limpia los datos → Fase 2 detecta lagunas sobre
datos limpios → Fase 3 cura el retrieval de esa base → Fase 4 evita que entre basura nueva.

## 5. Modelo de datos (Fase 1)

Cada evento de memoria gana metadatos de procedencia. En las cards viven como una etiqueta
estructurada al final de la línea del bullet (HTML comment Obsidian-safe, p.ej.
`<!-- ksi:{id,learned_at,confidence,...} -->`), de modo que no alteran el render visible de
la card ni rompen las cards legadas. El `id` es content-addressed sobre el texto normalizado.

```yaml
id: <sha1(content_normalizado)[:16]>   # estable e idempotente
learned_at: 2026-06-14                  # cuándo se supo (ISO date)
source: "session:2026-06-14_2230" | "turn" | "manual"
confidence: 0.0–1.0                      # numérico; mapeo desde high/med/low actual
reinforced: <int>                        # nº de reconfirmaciones
superseded_by: <id|null>                 # supersesión, NUNCA borrado
```

Mapeo de confianza legado: `high→0.85`, `medium→0.6`, `low→0.35` (constantes en `confidence.py`).

## 6. Componentes

```
memory/self_improvement/
  __init__.py
  config.py      # KnowledgeImproverConfig (frozen, from_env)
  improver.py    # KnowledgeImprover — fachada fail-safe, orquesta el pipeline
  events.py      # carga eventos desde journal+cards; asigna id/provenance
  projection.py  # regenera la card desde eventos (card = OUTPUT, no se parchea)
  detectors.py   # detect_duplicate_clusters() + detect_contradictions()
  judge.py       # judge_merge/judge_supersede(reasoner, candidato) -> Verdict
  confidence.py  # decay temporal + refuerzo por reconfirmación + mapeo legado
  proposer.py    # verdicts destructivos -> list[Signal] (kind="memory_merge"/"memory_supersede")
  metrics.py     # salud de la memoria -> health.md
  review_log.py  # traza auditable fechada
```

### Responsabilidad de cada módulo
- **config.py** — `KnowledgeImproverConfig`: `enabled`, `token_budget`, `sim_threshold`
  (default 0.86), `decay_half_life_days`, `min_cluster_size`. `from_env()` con prefijo
  `JARVIS_KSI_`.
- **events.py** — `load_events(vault, journal)` → `list[MemoryEvent]` con `id`/`provenance`.
  `MemoryEvent` es `@dataclass(frozen=True)`. Idempotente: mismo contenido → mismo `id`.
- **detectors.py** — determinista, barato. `detect_duplicate_clusters(events, embedder, threshold)`
  reusa el embedder MiniLM del RAG (no carga uno nuevo). `detect_contradictions(events)` por
  heurísticas (negación, valores opuestos en misma clave).
- **judge.py** — reasoner SOLO sobre candidatos. Devuelve `MergeVerdict(is_true_duplicate,
  canonical_text, member_ids)` / `SupersedeVerdict(...)`. JSON con self-heal. Presupuestado.
- **confidence.py** — `decayed(confidence, learned_at, half_life)` y `reinforce(event)`.
- **projection.py** — `rebuild_card(project, events)` → markdown determinista. Antes de
  escribir, `snapshot_previous(card_path)` guarda la versión previa en
  `self-improvement/snapshots/`.
- **proposer.py** — `to_signals(verdicts)` mapea al shape de `suggestion_struct` de la
  `OpportunityQueue` con nuevos `kind`: `memory_merge`, `memory_supersede`.
- **metrics.py** — `compute_health(events)` → dict; `write_health(vault, health)`.
- **review_log.py** — `append_review_log(vault, actions)` a `self-improvement/review-log.md`.

## 7. Flujo de datos (un ciclo al cierre de sesión)

1. **Enganche** — al final de `JARVIS/jarvis.py:_save_session_memory()`, tras
   `synthesize_and_save`, se llama `KnowledgeImprover.run(vault, rag, reasoner, journal)`.
   Idempotente por proceso (misma guarda que `_session_saved`).
2. **Recolectar** — `events.load_events()` lee journal + bullets de cards; asigna `id`/provenance.
3. **Detectar (determinista)** — `detectors` embebe bullets con el MiniLM del RAG, agrupa por
   coseno ≥ `sim_threshold` → clusters; marca contradicciones. Acota el trabajo del reasoner.
4. **Recalcular confianza (autónomo)** — `confidence.py` aplica decay por antigüedad y refuerzo
   a eventos reconfirmados (mismo `id` visto otra vez).
5. **Juzgar (reasoner, solo candidatos)** — por cluster, `judge.py` decide duplicado real y
   redacta texto canónico. Si no hay budget → se salta, solo corre lo determinista (degradación elegante).
6. **Aplicar aditivo (autónomo)** — `projection.rebuild_card` regenera la card con confianza
   ponderada y `last_reviewed`, tras snapshot de la versión previa. No se parchea in-place;
   se regenera desde eventos. Eventos intactos siempre.
7. **Proponer destructivo (HITL)** — fusiones/supersesiones → `Signal` → `OpportunityQueue`.
   El morning briefing del próximo arranque las ofrece como "PR de memoria"; al aprobar, se
   marca `superseded_by` (nunca se borra).
8. **Registrar + medir** — `review_log` y `metrics` escriben traza y salud.

## 8. Autonomía y seguridad

- **Autónomo (aditivo):** recalcular confianza, marcar candidatos, regenerar proyección tras
  snapshot. Reversible siempre porque los eventos son inmutables.
- **HITL (destructivo):** fusión y supersesión se proponen; el usuario aprueba en el briefing.
  Nunca se borra un evento — solo `superseded_by`.
- **Reversibilidad:** si una regeneración sale mal, se reconstruye desde el log de eventos; los
  snapshots son red secundaria, no la primaria.

## 9. Manejo de errores

Wrapper fail-safe total al estilo `ProactivityEngine`: ningún método de `KnowledgeImprover`
propaga excepción. Cualquier fallo → no-op silencioso + log de warning; el shutdown nunca se
rompe. Si la síntesis de sesión falla antes, el improver no corre (no hay eventos nuevos fiables).
Presupuesto de tokens: sin budget disponible, se salta el reasoner (paso 5) y se ejecuta solo el
camino determinista.

## 10. Testing (TDD)

Patrón del repo: `PYTHONUTF8=1 /h/Python311/python.exe -m pytest`.

- `test_ksi_events.py` — `id` estable/idempotente; provenance correcta; eventos malformados ignorados.
- `test_ksi_detectors.py` — clusters de duplicados conocidos; no agrupa no-duplicados; contradicciones.
- `test_ksi_confidence.py` — decay monótono con antigüedad; refuerzo incrementa; mapeo legado.
- `test_ksi_projection.py` — `rebuild_card` determinista == esperado; crea snapshot; eventos intactos.
- `test_ksi_proposer.py` — verdicts → Signal con `kind` correcto y shape de `suggestion_struct`.
- `test_ksi_improver.py` — fail-safe: excepción inyectada en cualquier paso → run no rompe;
  idempotencia por proceso; sin budget → solo determinista.

## 11. Métricas de salud

`metrics.py` escribe `Jarvis Memory/self-improvement/health.md` por corrida:
- nº de eventos totales y por proyecto
- nº de clusters de duplicados detectados / fusiones propuestas
- nº de contradicciones abiertas
- staleness (días desde `last_reviewed` por card)
- confianza media ponderada

Permite verificar que el loop **mejora** la base con el tiempo en vez de degradarla.

## 12. Archivos nuevos / tocados

**Nuevos:** todo `memory/self_improvement/` + sus tests en `tests/`.
**Tocados:**
- `memory/triage.py` — emitir bullets con metadatos de procedencia (id/learned_at/source/confidence).
- `proactivity/signals.py` + `opportunity_queue.py` — nuevos `kind` `memory_merge`/`memory_supersede`.
- `proactivity/briefing.py` — renderizar el "PR de memoria".
- `jarvis.py` — instanciar `KnowledgeImprover` en `build()` y llamarlo en `_save_session_memory()`.
- `.env.example` — variables `JARVIS_KSI_*`.
- `CHANGELOG.md` — entrada de la feature.

## 13. Riesgos y mitigaciones

| Riesgo | Mitigación |
|--------|------------|
| Falsos positivos de duplicado fusionan info distinta | Fusión es HITL; nunca autónoma. Umbral coseno alto (0.86) + juicio del reasoner |
| Costo de tokens al cierre | Determinista filtra candidatos; reasoner solo sobre clusters; presupuestado |
| Regeneración corrompe una card | Snapshot previo + eventos inmutables = reconstrucción total |
| Crash al cierre por el improver | Wrapper fail-safe absoluto; el shutdown nunca depende del improver |
| Metadatos de procedencia rompen cards legadas | Migración perezosa: eventos sin `id` se hashean al primer encuentro |
