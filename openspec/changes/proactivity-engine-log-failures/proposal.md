# Proposal

## Why

`proactivity/engine.py` (`ProactivityEngine`) promete en su docstring "fail-safe absoluto: ningún método propaga excepciones", pero cumple esa promesa tragando las excepciones sin dejar ningún rastro en 4 puntos: `build_briefing`, `observe`, `next_opportunity` (al llamar `queue.top_opportunity()`; además `queue.mark_offered()` está hoy fuera del `try` y sí puede propagar) y `dismiss_last` (al llamar `queue.mark_dismissed()`). Si el motor deja de funcionar (vault roto, estado corrupto, bug en detección), el usuario no recibe briefing ni sugerencias y nadie puede saber por qué. Como `observe` nunca propaga, el `except` de `jarvis.py` (~línea 1459, `[WARN] proactividad (observe) falló`) es código muerto y tampoco informa nada.

La v1.12 ya estableció el patrón para este tipo de fallos silenciosos: `record_error(source, exc=..., severity=..., message=..., context=...)` del error journal (`telemetry/error_journal.py`). Este change lo aplica al motor de proactividad.

## What Changes

- En `next_opportunity`, ampliar el `try` existente para cubrir también `mark_offered`, de modo que el contrato "no propaga" se cumpla de verdad.
- En cada uno de los 4 `except Exception` de `ProactivityEngine`, capturar la excepción (`as exc`) y llamar a `record_error("proactivity.<método>", exc=exc, severity="warning", message=<texto en español>, context={...})` antes de devolver el valor neutro actual (`""`, `None` o retorno sin valor).
- Sources a usar: `proactivity.build_briefing`, `proactivity.observe`, `proactivity.next_opportunity`, `proactivity.dismiss_last`.
- Mantener intacto el contrato: ningún método público propaga la excepción al llamador, y los valores de retorno en caso de fallo no cambian.
- Añadir tests en `tests/test_proactivity_engine.py` que, con `JARVIS_ERROR_JOURNAL_PATH` apuntando a `tmp_path`, verifiquen que cada fallo queda registrado en el journal y que no se propaga.

## Capabilities

### New Capabilities
- `proactivity-engine`: contrato de fail-safe observable de la fachada `ProactivityEngine`: ante cualquier fallo interno, el método registra el error en el error journal y no propaga la excepción al llamador.

### Modified Capabilities

(ninguna; `openspec/specs/` no contiene aún una capability previa para el motor de proactividad)

## Impact

- Código afectado: `proactivity/engine.py` (4 bloques `except` + 1 import de `telemetry.error_journal.record_error`).
- Tests afectados: `tests/test_proactivity_engine.py` (tests nuevos; los existentes no cambian).
- Sin cambios de API pública, dependencias nuevas, formato del journal ni de configuración.
- Efecto operativo: aparecen nuevas filas `severity="warning"` con source `proactivity.*` en `data/error_journal.jsonl` cuando el motor falla. El journal ya redacta secretos y respeta `JARVIS_ERROR_JOURNAL=false`.

## Fuera de alcance

- Borrar, modificar o reubicar el `except Exception` de `jarvis.py` (~línea 1459); queda como está (hoy es código muerto, se decide en otro change).
- Cambios en la lógica de detección de señales, `OpportunityQueue`, scoring, cooldowns o renderizado del briefing.
- Los `except: pass` internos de `OpportunityQueue._load` / `_save` (fallos de persistencia del estado).
- Cualquier cambio de severidad, formato o ubicación del error journal, y otros subsistemas con fallos silenciosos (`skills/registry.py`, `study/mode.py`, etc.).
- Entrada de CHANGELOG, bump de versión y release.
