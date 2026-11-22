## Context

`StudyModeController` (`study/mode.py`) tiene dos `except Exception: pass`:

1. `_start_flush_worker._worker`: tras guardar `_last_flush_result` y `_flush_status` bajo `self._lock`, llama `self.on_flush_done(dict(result))` fuera del lock; si lanza, se descarta.
2. `_observer_loop`: cada `capture_interval_s` (~45 s) llama `capture_page(intent="continuous_observer")`; cualquier excepción se descarta y el loop sigue.

`self._lock` es un `threading.RLock` (reentrante). `capture_page` toma el lock solo en tramos cortos y lee Chrome (`ChromeReader`) fuera de él. Nota: `capture_page` ya devuelve `{"ok": False, ...}` para "Chrome sin texto / pausado / inactivo"; eso **no** es una excepción y hoy tampoco se reporta (se mantiene así).

Patrón de v1.12 a reutilizar: `record_error(source, exc=exc, severity="warning", message=..., context=...)` de `telemetry/error_journal.py`. Es fail-closed (nunca lanza), redacta secretos y respeta `JARVIS_ERROR_JOURNAL_PATH` / `JARVIS_ERROR_JOURNAL`.

## Goals / Non-Goals

**Goals:**
- Ambos fallos dejan rastro en el journal (`study.flush_done`, `study.observer`).
- `_last_flush_result` sigue disponible aunque el callback falle.
- Un fallo persistente del observer no inunda el journal (~80 líneas/hora a 45 s).
- Límite simple y testeable sin relojes ni sleeps largos.

**Non-Goals:**
- Rediseñar el loop, cambiar el intervalo, tocar `study_mode()` en `jarvis.py`, el hilo de audio o el `RLock`. Ver "Fuera de alcance" en `proposal.md`.

## Decisions

### 1. Límite del observer: "una entrada por racha, re-armar tras retorno sin excepción"

Estado: una variable local `failing = False` dentro de `_observer_loop`.

```python
failing = False
while not self._stop_event.wait(self.config.capture_interval_s):
    ...
    try:
        self.capture_page(intent="continuous_observer")
        failing = False
    except Exception as exc:
        if not failing:
            failing = True
            record_error("study.observer", exc=exc, severity="warning",
                         message="...primer fallo de una racha; los siguientes se omiten...",
                         context={"intent": "continuous_observer",
                                  "capture_interval_s": self.config.capture_interval_s})
```

Por qué esta opción:
- **Local al hilo**: solo `_observer_loop` lee/escribe `failing`, así que no hace falta Lock nuevo ni tocar `self._lock`. `record_error` se llama fuera de cualquier `with self._lock`, evitando que una E/S de disco bloquee a quien usa el lock (flush, `status()`, comandos de voz).
- **Testeable**: determinista contando intentos (fallo×3 → 1 entrada; fallo/éxito/fallo → 2). No depende de ventanas de tiempo.
- **Cubre el riesgo real**: el peor caso (Chrome/UIA roto permanentemente) produce 1 línea por racha en vez de 1 cada 45 s; la recuperación y recaída se ven como entradas separadas, que es información útil.

Alternativas descartadas:
- *Deduplicar por tipo de excepción*: requiere un set creciente o una clave más; una racha con causas alternas (`TimeoutError`/`OSError`) aún escribiría varias veces, y tras recuperarse el mismo tipo nunca se volvería a registrar (pierde la recaída). Más estado y menos señal.
- *Rate limit por tiempo (p. ej. 1 cada 10 min)*: necesita reloj inyectable en tests y sigue escribiendo líneas periódicas durante un fallo permanente.
- *Contador con log cada N fallos*: más superficie (qué N, cómo reportar) sin ganancia clara; YAGNI.

Limitación asumida: dentro de una racha no se registra el número total de fallos ni si cambió la causa. Si hiciera falta, un `record_error` al cerrar la racha (con el conteo) se puede añadir después sin romper este contrato.

**Definición de "éxito"**: cualquier retorno de `capture_page` sin excepción (incluye `ok: False`, `duplicate`, página sin texto). Razón: la excepción es la señal de bug/entorno roto que este change quiere exponer; `ok: False` es un resultado controlado ya modelado.

### 2. Callback `on_flush_done`: una entrada por fallo, sin límite

Los flush ocurren por acción explícita (comando, stop) o un guardado en curso a la vez (`_flush_thread.is_alive()` ya serializa), no en un timer, así que no hay riesgo de inundación y no se añade estado. El orden actual ya es el correcto: `_last_flush_result`/`_flush_status` se asignan bajo lock **antes** del callback, por lo que el resultado se conserva aunque el callback lance. El código nuevo solo cambia el `except` (no reordena nada).

`context` incluye `ok`, `flushed` y `evidence_count` del resultado (valores escalares, no el contenido de las notas) para correlacionar sin copiar texto del usuario.

### 3. Import y fuente de `record_error`

Import a nivel de módulo `from telemetry.error_journal import record_error` en `study/mode.py` (mismo estilo que el resto de imports absolutos del archivo). Los tests no parchean `record_error`; usan `JARVIS_ERROR_JOURNAL_PATH` + `JARVIS_ERROR_JOURNAL=true` en `tmp_path` y leen el JSONL, como `tests/test_error_journal.py`. Esto valida también el wiring real y la fuente exacta.

## Risks / Trade-offs

- [Racha larga oculta: tras el primer fallo no hay más líneas] → Mitigación: es el comportamiento buscado; la entrada incluye en el mensaje que los siguientes se omiten hasta una captura sin excepción. Ampliar con conteo al cerrar racha queda como mejora futura.
- [Test del observer con hilos/tiempos] → Mitigación: `capture_interval_s` muy pequeño (p. ej. 0.01), `capture_page` reemplazado por un fake con contador que activa `_stop_event` al llegar al intento N, y `join(timeout=2)` del hilo; sin `sleep` fijos.
- [`record_error` dentro del `except` del observer podría lanzar] → Mitigación: la función es fail-closed por contrato; aun así, el `except` solo cubre `capture_page`, y el código nuevo no añade nuevas rutas de salida del loop.
- [El bloque del callback también queda protegido] → un fallo de `record_error` no puede propagarse al worker (fail-closed), por lo que el hilo de flush no cambia de comportamiento.

## Migration Plan

Sin migración de datos ni de configuración. Rollback: revertir el commit; el journal es solo-append y tolerante a entradas con las nuevas fuentes.

## Open Questions

Ninguna bloqueante. Suposición registrada: el nivel `warning` (igual que v1.12) es adecuado porque Study Mode sigue funcionando tras ambos fallos.
