## 1. Callback de flush (`study.flush_done`)

- [x] 1.1 En `study/mode.py` añadir `from telemetry.error_journal import record_error` junto a los imports existentes.
- [x] 1.2 En `_start_flush_worker._worker`, cambiar `except Exception: pass` por `except Exception as exc` y llamar `record_error("study.flush_done", exc=exc, severity="warning", message=..., context={"ok", "flushed", "evidence_count"})`. No reordenar la asignación de `_last_flush_result` / `_flush_status` (debe seguir antes del callback).

## 2. Observer (`study.observer`)

- [x] 2.1 En `_observer_loop`, añadir la variable local `failing = False` antes del `while`.
- [x] 2.2 Tras `self.capture_page(intent="continuous_observer")` sin excepción, poner `failing = False`; en `except Exception as exc`, si `not failing`: poner `failing = True` y llamar `record_error("study.observer", exc=exc, severity="warning", message=..., context={"intent", "capture_interval_s"})`.
- [x] 2.3 Verificar que `record_error` no se invoca dentro de ningún `with self._lock` y que el loop sigue tras el fallo.

## 3. Tests (`tests/test_study_mode.py`)

- [x] 3.1 Test `on_flush_done` que lanza: `monkeypatch.setenv` de `JARVIS_ERROR_JOURNAL_PATH` (en `tmp_path`) y `JARVIS_ERROR_JOURNAL=true`; flush en background con `FakeReasoner`; esperar fin del worker (`join` del `_flush_thread`); afirmar `_last_flush_result["ok"] is True` y una única entrada `study.flush_done` con `error_type == "RuntimeError"`.
- [x] 3.2 Test callback correcto: sin entradas `study.flush_done` en el journal.
- [x] 3.3 Test observer fallo×3: `capture_interval_s=0.01`, `capture_page` falso que lanza y detiene el loop (`_stop_event.set()`) en el 3.er intento; `join(timeout=2)`; afirmar exactamente 1 entrada `study.observer`.
- [x] 3.4 Test fallo → éxito → fallo: afirmar 2 entradas `study.observer`.
- [x] 3.5 Test `capture_page` que retorna `{"ok": False, ...}` sin lanzar: 0 entradas `study.observer`.
- [x] 3.6 Ejecutar `H:\Python311\python.exe -m pytest tests/test_study_mode.py` (todos los tests, nuevos y existentes, en verde).
- [x] 3.7 Ejecutar `H:\Python311\python.exe -m pytest tests/test_error_journal.py` para confirmar que el journal no regresiona.

## 4. Cierre

- [x] 4.1 Confirmar con `git diff --stat` que solo cambian `study/mode.py` y `tests/test_study_mode.py` (más este change); sin tocar `jarvis.py`, `memory/tools.py`, `CHANGELOG.md` ni `VERSION`.
