# Spec — JARVIS Fase 1: Continuidad entre sesiones

**Fecha:** 2026-05-28
**Estado:** Aprobado para planificación
**Autor:** Isaac + Claude (brainstorming)
**Parte de:** Roadmap de evolución de JARVIS (Fase 1 de 3)

---

## Context

JARVIS hoy es **stateless entre sesiones**: cada arranque empieza en frío. El
RAG del vault Obsidian existe (`memory/rag.py`) pero solo se consulta cuando el
modelo decide llamar `jarvis_recall` — no hay continuidad proactiva. Cuando Isaac
reabre JARVIS, este no recuerda en qué se trabajó la sesión anterior ni qué quedó
pendiente.

Esto choca con la queja original que originó el roadmap: querer "conversaciones
más prolongadas y mejores". La continuidad es el cimiento sobre el que se apoyan
las fases siguientes (razonamiento profundo con auto-contexto, y proactividad).

**Resultado buscado:** que al arrancar, JARVIS sepa automáticamente dónde se quedó
("la última vez trabajamos en X, quedó pendiente Y"), y que al cerrar destile la
conversación en una nota-diario fechada en Obsidian.

Esta es la **Fase 1** de un roadmap de 3 fases. Fases 2 (razonamiento profundo)
y 3 (proactividad) quedan documentadas como roadmap, fuera de alcance de este spec.

## Decisiones de diseño (tomadas en brainstorming)

| Decisión | Elección |
|----------|----------|
| Alcance del auto-recall | Último resumen de sesión (briefing generado por IA), inyectado al arranque |
| Trigger del resumen | Checkpoint incremental + síntesis en cierre limpio |
| Robustez ante kill-switch | Síntesis diferida: si el journal queda huérfano, se sintetiza en el próximo arranque |
| Quién sintetiza | Claude reasoner (reutiliza `claude/reasoner.py`, Sonnet 4.6 + caching) |
| Arquitectura | Enfoque A: journal JSONL + síntesis diferida + notas fechadas en Obsidian |

## Goals

- Al arrancar, JARVIS inyecta automáticamente el resumen de la última sesión a
  su contexto (sin que Isaac lo pida).
- Al cerrar (limpio), genera una nota-diario fechada en el vault con resumen,
  pendientes y proyectos tocados (con wikilinks al grafo).
- Garantía de durabilidad: el resumen **siempre** se genera — en cierre limpio,
  o en el próximo arranque si la sesión terminó con kill-switch/crash.
- Nunca romper la conversación por un fallo de journaling/síntesis (fail-safe).
- Respetar la postura de seguridad: redacción de secretos antes de persistir o
  enviar a Claude.

## Non-Goals (roadmap futuro)

- Memoria episódica multi-sesión con timestamps cruzados.
- Briefing que combine N días/sesiones (aquí: solo la última).
- Inyección de estado de proyectos activos (solo el resumen de sesión).
- Fase 2 (razonamiento profundo) y Fase 3 (proactividad).

---

## Arquitectura

### Componentes nuevos

#### `memory/session_journal.py` — persistencia cruda
Responsabilidad única: almacenar turnos de forma durable.

```
class SessionJournal:
    path: Path                       # data/session_journal.jsonl
    lock: threading.RLock            # se llama desde callbacks (thread-safe)

    append_turn(user: str, jarvis: str, ts: str | None = None) -> None
    has_pending() -> bool            # journal existe y tiene >= 1 turno
    read_turns() -> list[dict]       # [{ts, user, jarvis}, ...]
    turn_count() -> int
    clear() -> None                  # borra/rota el journal
```

- **Formato:** JSONL, un objeto por turno: `{"ts": ISO8601, "user": str, "jarvis": str}`.
- Append-only ⇒ a prueba de crash (cada turno se persiste inmediatamente).
- `redact_secrets()` (de `security.secret_filter`) se aplica al texto antes de escribir.
- Fallos de escritura: log + continuar (nunca propagar excepción a la conversación).

#### `memory/session_summary.py` — síntesis + lectura para recall
Responsabilidad única: convertir journal → nota, y leer la última nota.

```
SESSIONS_SUBDIR = "05-CLAUDE/context/sessions"

synthesize_and_save(
    journal: SessionJournal,
    reasoner: ClaudeReasoner,
    vault: ObsidianVault,
    min_turns: int,
) -> Path | None
    # 1. Si turn_count < min_turns → return None (sesión trivial, sin nota)
    # 2. Lee turnos, arma prompt, llama reasoner.ask(max_tokens≈600)
    # 3. Escribe nota fechada con frontmatter + secciones
    # 4. journal.clear()  (solo si la escritura tuvo éxito)
    # 5. return path

load_last_summary(vault: ObsidianVault, max_chars: int) -> str | None
    # Busca la nota más reciente en SESSIONS_SUBDIR (por nombre/fecha),
    # devuelve secciones Resumen + Pendientes recortadas a max_chars.
```

**Formato de la nota generada** (`05-CLAUDE/context/sessions/YYYY-MM-DD_HHMM_sesion.md`):
```markdown
---
type: session-journal
project: "[[03-PROJECTS/jarvis]]"
date: YYYY-MM-DD
session_id: <8hex>
generated_by: claude-sonnet-4-6
---

# Sesión YYYY-MM-DD HH:MM

## Resumen
- 3-5 bullets de lo conversado/hecho.

## Pendientes
- Items accionables que quedaron abiertos.

## Proyectos tocados
- [[03-PROJECTS/...]] wikilinks a proyectos mencionados.
```

**Bloque de inyección al system_prompt** (en arranque):
```
═══════════ CONTEXTO DE SESIÓN ANTERIOR ═══════════
<Resumen + Pendientes de la última nota, recortado a JARVIS_SESSION_RECALL_MAX_CHARS>
```

### Cableado en `jarvis.py` (3 puntos existentes)

1. **Arranque** — en `build()`, antes de construir el system_prompt
   ([jarvis.py:218](../../../jarvis.py)):
   - `if journal.has_pending(): synthesize_and_save(...)` (síntesis diferida del huérfano)
   - `prev = load_last_summary(...)`; si existe, concatenar bloque
     `CONTEXTO DE SESIÓN ANTERIOR` al `system_prompt` (junto al `preferences_prompt_block`).

2. **Durante** — en `_on_turn_complete()` ([jarvis.py:475](../../../jarvis.py)):
   - `journal.append_turn(user=último input_transcript, jarvis=último output_transcript)`.

3. **Cierre limpio** — en `stop()` ([jarvis.py:248](../../../jarvis.py)):
   - `synthesize_and_save(...)` con guard idempotente (corre una sola vez).

### Flujo de datos

```
turn_complete  →  journal.append_turn()  →  data/session_journal.jsonl   (cada turno)

cierre limpio (stop)  →  synthesize_and_save()  →  Claude  →
        05-CLAUDE/context/sessions/<fecha>.md  →  journal.clear()

próximo arranque  →  si journal huérfano → synthesize_and_save() (diferida)  →
        load_last_summary()  →  inyectar al system_prompt
```

### Patrón clave: durabilidad por reconciliación

No se confía en que el apagado sea limpio (el kill-switch `Ctrl+Alt+Q` hace
`os._exit(130)` y se salta `stop()`). En cambio, se reconcilia el estado al
**encender**: si hay un journal huérfano, se sintetiza ahí. Así la garantía
"el resumen siempre se genera" no depende del cierre.

---

## Configuración (.env)

| Var | Default | Propósito |
|-----|---------|-----------|
| `JARVIS_SESSION_JOURNAL_ENABLED` | `true` | Master switch de toda la feature |
| `JARVIS_SESSION_MIN_TURNS` | `3` | Mínimo de turnos para generar nota (evita ruido) |
| `JARVIS_SESSION_RECALL_MAX_CHARS` | `1000` | Cap del bloque inyectado al system_prompt |

Documentar las 3 en `.env.example`.

---

## Manejo de errores (fail-safe)

| Escenario | Comportamiento |
|-----------|----------------|
| Fallo al escribir journal | Log + continuar (conversación nunca se interrumpe) |
| Síntesis falla (Claude caído) | **No** limpiar journal → reintenta como huérfano al próximo arranque |
| Carpeta `sessions/` no existe | Crearla |
| Fallo al leer recall | Arranca sin inyección (degradación elegante) |
| Journal corrupto (línea JSON inválida) | Saltar línea, log, seguir con el resto |

---

## Testing

### Unit
- `session_journal`: append/read/clear roundtrip; redacción aplicada al escribir;
  `has_pending`/`turn_count` correctos; línea corrupta se salta sin romper.
- `session_summary`: con un `ClaudeReasoner` stub (inyectado) → nota escrita con
  frontmatter correcto + wikilinks; skip cuando `turn_count < min_turns`; journal
  NO se limpia si la escritura falla.
- `load_last_summary`: elige la nota más reciente; respeta `max_chars`; devuelve
  `None` si no hay notas.

### Integración (ligera)
- Simular arranque con journal huérfano → `synthesize_and_save` corre → string de
  inyección construido correctamente.

### Manual E2E
1. Correr JARVIS, conversación corta (≥ min_turns), cerrar limpio → verificar nota
   en `sessions/`.
2. Reabrir → verificar que JARVIS referencia la sesión anterior.
3. Probar kill-switch (Ctrl+Alt+Q) a mitad de sesión → reabrir → verificar que el
   huérfano se sintetizó al arrancar.

---

## Archivos afectados

| Archivo | Cambio |
|---------|--------|
| `memory/session_journal.py` | **Nuevo** — persistencia JSONL |
| `memory/session_summary.py` | **Nuevo** — síntesis Claude + lectura recall |
| `jarvis.py` | Cableado en 3 puntos (build/arranque, `_on_turn_complete`, `stop`) |
| `.env.example` | 3 vars nuevas documentadas |
| `tests/test_session_journal.py` | **Nuevo** |
| `tests/test_session_summary.py` | **Nuevo** |

## Reutilización (no reinventar)

- `claude/reasoner.py` → `ClaudeReasoner.ask()` para la síntesis (ya tiene caching + retry).
- `security/secret_filter.py` → `redact_secrets()` para sanitizar antes de persistir/enviar.
- `memory/obsidian_vault.py` → `ObsidianVault` para resolver paths del vault.
- `self._input_transcript` / `self._output_transcript` ([jarvis.py:109](../../../jarvis.py)) → fuente de los turnos crudos (ya se acumulan).

## Verificación de done

- `pytest` verde (suite existente + los 2 nuevos archivos de test).
- Smoke import de los módulos nuevos.
- E2E manual: los 3 escenarios de arriba pasan.
- Nota de sesión aparece en el grafo de Obsidian conectada a `[[03-PROJECTS/jarvis]]`.
