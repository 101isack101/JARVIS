# Tasks

## 1. Briefing y observe (fallos de lectura del vault)

- [ ] 1.1 Añadir en `tests/test_proactivity_engine.py` los tests `test_build_briefing_failure_is_journaled` y `test_observe_failure_is_journaled` (vault que lanza excepción como el `Boom` existente, `monkeypatch.setenv("JARVIS_ERROR_JOURNAL_PATH", str(tmp_path / "errors.jsonl"))` y `JARVIS_ERROR_JOURNAL=true`, igual que `tests/test_error_journal.py`); verificar que ambos fallan antes del cambio con `H:\Python311\python.exe -m pytest tests/test_proactivity_engine.py -k journaled`
- [ ] 1.2 En `proactivity/engine.py`, importar `record_error` de `telemetry.error_journal` y registrar los fallos de `build_briefing` (`proactivity.build_briefing`) y `observe` (`proactivity.observe`, `context={"turn_chars": len(turn_text)}`) con `severity="warning"`, mensaje en español y `exc=exc`, conservando los retornos `""` y `None`; verificar que los tests de 1.1 pasan y que cada test asserta `source`, `severity`, `error_type` y la ausencia de excepción en el llamador

## 2. next_opportunity y dismiss_last (fallos de la cola)

- [ ] 2.1 Añadir en `tests/test_proactivity_engine.py` los tests `test_next_opportunity_failure_is_journaled` (parametrizado: `queue.top_opportunity` lanza / `queue.mark_offered` lanza, vía `monkeypatch.setattr` sobre `eng.queue`) y `test_dismiss_last_failure_is_journaled` (asignar `eng._last_offered_id` y hacer que `queue.mark_dismissed` lance); verificar que fallan antes del cambio con `H:\Python311\python.exe -m pytest tests/test_proactivity_engine.py -k journaled`
- [ ] 2.2 En `proactivity/engine.py`, ampliar el `try` de `next_opportunity` para cubrir `mark_offered` y `_last_offered_id`, y registrar `proactivity.next_opportunity` y `proactivity.dismiss_last` (`context={"opportunity_id": ...}` en el segundo) con `severity="warning"`; mantener retorno `None` y que `_last_offered_id` se reinicie igual que hoy en `dismiss_last`; verificar que los tests de 2.1 pasan

## 3. Verificación de regresión

- [ ] 3.1 Ejecutar `H:\Python311\python.exe -m pytest tests/test_proactivity_engine.py tests/test_error_journal.py tests/test_proactive_check_tool.py` y verificar que todos pasan, incluidos los tests previos de `test_proactivity_engine.py` sin modificar
- [ ] 3.2 Verificar con `git diff --stat` que solo cambian `proactivity/engine.py` y `tests/test_proactivity_engine.py` (el `except` de `jarvis.py` ~línea 1459, CHANGELOG y versión quedan intactos)

## Workflow follow-up

- Archivar el change tras revisar el diff; la entrada de CHANGELOG y el bump de versión pertenecen al release posterior.
