# Proposal

## Why

`study/mode.py` traga dos fallos con `except Exception: pass`: el callback `on_flush_done` (dentro de `_start_flush_worker`, ~línea 355) y cualquier excepción de `capture_page` en `_observer_loop` (~línea 384, un intento cada `capture_interval_s`, ~45 s por defecto). Si el callback de UI/overlay se rompe o el observer falla de forma persistente, Isaac no tiene ninguna señal. v1.12 ya introdujo el patrón `record_error(...)` del error journal para este tipo de fallos silenciosos (`memory/tools.py`, `telemetry/tracker.py`); Study Mode quedó fuera.

## What Changes

- `_start_flush_worker`: el `except Exception` alrededor de `self.on_flush_done(dict(result))` pasa a `except Exception as exc` y llama `record_error("study.flush_done", exc=exc, severity="warning", message=..., context=...)`. `_last_flush_result` y `_flush_status` ya se escriben antes del callback, así que el resultado del flush sigue disponible.
- `_observer_loop`: el `except Exception` alrededor de `self.capture_page(intent="continuous_observer")` pasa a registrar `record_error("study.observer", ...)` con un límite anti-inundación: solo se registra el **primer** fallo de una racha; la racha termina cuando una captura vuelve sin lanzar excepción, y un fallo posterior vuelve a registrarse (detalle en `design.md`).
- Tests nuevos en `tests/test_study_mode.py` usando `JARVIS_ERROR_JOURNAL_PATH` en `tmp_path`.
- No hay cambios de API pública, de firmas ni de formato del journal.

## Capabilities

### New Capabilities
- `study-mode`: contrato de observabilidad de Study Mode ante fallos tragados (callback de flush y observer en background). `openspec/specs/` está vacío, así que es la primera capability del proyecto; futuros requisitos de Study Mode pueden vivir aquí.

### Modified Capabilities
<!-- Ninguna: no existen specs previos. -->

## Fuera de alcance

- Rediseñar el observer loop (estructura, hilos, condición de parada).
- Cambiar el intervalo de captura (`capture_interval_s`) o hacerlo adaptativo/backoff.
- Tocar `study_mode()` en `jarvis.py` ni `memory/tools.py`.
- Tocar el hilo de audio o cambiar el `RLock` existente (`self._lock`).
- Cambiar el módulo `telemetry/error_journal.py` (rotación, deduplicación global, formato).
- Los demás `except: pass` del repo (`skills/registry.py`, `proactivity/engine.py`, etc.).
- Cambios en `CHANGELOG.md`, `VERSION` o archivos de release.

## Impact

- Código: `study/mode.py` (~15 líneas), `tests/test_study_mode.py` (3 tests).
- Dependencias: `telemetry.error_journal.record_error` (ya existente, fail-safe y con redacción de secretos).
- Runtime: escritura a `data/error_journal.jsonl` solo en fallos; con el límite, como máximo una línea por racha del observer y una por flush fallido del callback.
