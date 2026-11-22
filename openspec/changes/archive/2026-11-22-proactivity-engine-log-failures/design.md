# Design

## Context

`ProactivityEngine` (`proactivity/engine.py`) es la fachada entre `jarvis.py` y el motor determinista. Hoy tiene 4 `except Exception` que devuelven un valor neutro sin rastro (`build_briefing` → `""`, `observe` → `None`, `next_opportunity` → `None`, `dismiss_last` → `None`). Además `queue.mark_offered()` en `next_opportunity` está fuera del `try`. Motivación y alcance: ver proposal.md.

El patrón de referencia es el de v1.12: `except Exception as exc:` + `record_error(...)` de `telemetry/error_journal.py`. Ese módulo solo depende de stdlib, redacta secretos, respeta `JARVIS_ERROR_JOURNAL` y ya es fail-closed (los errores de escritura van a stderr y no interrumpen).

## Goals / Non-Goals

**Goals:**
- Cada fallo interno deja una fila en el journal con source `proactivity.<método>`.
- El contrato "ningún método público propaga" se mantiene y pasa a cumplirse también en `mark_offered`.

**Non-Goals:**
- Tocar el `except` de `jarvis.py`, la cola, la detección o el briefing (ver "Fuera de alcance" en proposal.md).
- Introducir un helper o decorador compartido para los 4 puntos.

## Decisions

- **Registrar con `record_error(..., severity="warning")`, un source por método.** El fallo degrada una función auxiliar (briefing/sugerencias), no tumba a JARVIS, por eso `warning` y no `error`. Un source por método (`proactivity.build_briefing`, `.observe`, `.next_opportunity`, `.dismiss_last`) permite agrupar en el journal sin parsear mensajes. Alternativa descartada: un solo source `proactivity` con el método en `context`; peor para filtrar y se aparta del patrón v1.12.
- **Cuatro `except` explícitos, sin helper.** Son 4 llamadas de 1-2 líneas; un decorador o `_fail_safe()` añadiría una abstracción con un solo uso y esconde el valor de retorno neutro de cada método. Se repite el patrón local, igual que en v1.12. Alternativa descartada: decorador `@fail_safe(source, default)`.
- **`context` mínimo y sin texto del usuario.** `observe` registra solo `{"turn_chars": len(turn_text)}`, no el turno (voz del usuario); `dismiss_last` registra el id de la oportunidad; los demás van sin contexto o con la ruta del estado. La redacción del journal no es excusa para volcar datos innecesarios. Mensajes en español, como el resto de los logs.
- **`mark_offered` entra en el `try` de `next_opportunity`.** Si falla, el método devuelve `None` y `_last_offered_id` no se actualiza. Mantener el orden actual (consultar, marcar, guardar id, devolver estructura) evita que se ofrezca algo que no quedó marcado como ofrecido. Alternativa descartada: `try` separado solo para `mark_offered`, que permitiría devolver una oportunidad sin registrarla y repetirla en el siguiente turno.
- **Import de `record_error` a nivel de módulo.** No hay riesgo de ciclo (solo stdlib) y los tests pueden parchear el journal con `JARVIS_ERROR_JOURNAL_PATH` sin importaciones perezosas.
- **El `except` de `jarvis.py:1459` no se toca.** Sigue siendo código muerto, pero borrarlo es una decisión aparte.

## Risks / Trade-offs

- [Un fallo persistente genera una fila por turno en el journal] → Aceptado: el journal es JSONL local con redacción y límite de texto, y es justo la señal que hoy falta. Si molesta, un rate-limit se propone en otro change.
- [`record_error` falla dentro del `except` y rompe el fail-safe] → `record_error` ya es fail-closed por diseño y los tests lo cubren indirectamente con el contrato "no propaga".
- [Ampliar el `try` en `next_opportunity` cambia el comportamiento de un caso hoy no cubierto] → Solo afecta a excepciones que antes se propagaban; el camino sin error queda idéntico y los tests existentes lo verifican.
