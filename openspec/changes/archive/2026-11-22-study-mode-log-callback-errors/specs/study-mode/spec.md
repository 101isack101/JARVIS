## ADDED Requirements

### Requirement: Flush callback failures are journaled
Study Mode SHALL record in the error journal, with source `study.flush_done` and severity `warning`, any exception raised by the `on_flush_done` callback, and MUST keep the flush result available in `_last_flush_result` and `_flush_status` regardless of the callback outcome.

#### Scenario: Callback que lanza conserva el resultado y queda registrado
- **GIVEN** un `StudyModeController` con un `on_flush_done` que lanza `RuntimeError("overlay down")` y `JARVIS_ERROR_JOURNAL_PATH` apuntando a un archivo temporal
- **WHEN** un flush en background termina y el worker invoca el callback
- **THEN** `_last_flush_result` contiene el resultado del flush (mismo `ok`/`flushed` que sin callback)
- **AND** el journal contiene exactamente una entrada con `source == "study.flush_done"`, `severity == "warning"` y `error_type == "RuntimeError"`

#### Scenario: Callback correcto no escribe en el journal
- **GIVEN** un `StudyModeController` con un `on_flush_done` que no lanza excepción
- **WHEN** un flush en background termina
- **THEN** el journal no contiene entradas con `source == "study.flush_done"`

### Requirement: Observer capture failures are journaled once per failure streak
Study Mode SHALL record in the error journal, with source `study.observer` and severity `warning`, an exception raised by `capture_page` inside the background observer loop, but only for the first failure of a consecutive-failure streak. A streak MUST end when `capture_page` returns without raising, after which the next exception SHALL be recorded again. The observer loop MUST keep running after a failure and SHALL NOT write to the journal from inside `self._lock`.

#### Scenario: Tres fallos seguidos producen una sola entrada
- **GIVEN** un observer activo cuyo `capture_page` lanza `RuntimeError` en cada intento y `JARVIS_ERROR_JOURNAL_PATH` en `tmp_path`
- **WHEN** el observer ejecuta 3 intentos consecutivos
- **THEN** el journal contiene exactamente una entrada con `source == "study.observer"`
- **AND** el loop sigue activo tras el tercer fallo

#### Scenario: Fallo, éxito, fallo registra dos entradas
- **GIVEN** un observer activo cuyo `capture_page` falla en el intento 1, retorna un dict sin lanzar en el intento 2 y falla en el intento 3
- **WHEN** el observer ejecuta los 3 intentos
- **THEN** el journal contiene exactamente dos entradas con `source == "study.observer"`
- **AND** la segunda entrada corresponde al intento 3

#### Scenario: Captura que retorna ok=False sin excepción no se registra
- **GIVEN** un observer activo cuyo `capture_page` retorna `{"ok": False, "captured": False, "error": "..."}` sin lanzar
- **WHEN** el observer ejecuta varios intentos
- **THEN** el journal no contiene entradas con `source == "study.observer"`
