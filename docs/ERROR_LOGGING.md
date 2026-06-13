# Error Logging y Trazabilidad

Jarvis mantiene tres capas locales de observabilidad:

- `data/jarvis.log`: stream humano principal, rotado por loguru.
- `data/jarvis_crash.log`: tracebacks de crashes no capturados, threads y stack watchdog.
- `data/error_journal.jsonl`: journal estructurado para bug hunt y mejora continua.

## Journal estructurado

Cada línea de `data/error_journal.jsonl` es un objeto JSON independiente:

```json
{"ts":"2026-06-13T12:00:00.000+00:00","severity":"error","source":"jarvis.session","error_type":"RuntimeError","error_message":"...","context":{"handler":"_on_error"}}
```

Campos esperados:

- `ts`: timestamp UTC ISO-8601.
- `severity`: `error` o `critical`.
- `source`: subsistema que reportó el fallo.
- `message`: mensaje redactado cuando no hay excepción.
- `error_type`, `error_message`, `traceback`: presentes cuando hay excepción.
- `context`: metadatos pequeños para reproducir o agrupar el fallo.

El journal redacta secretos y trunca textos largos usando el mismo filtro de seguridad del logger principal.

## Configuración

```env
JARVIS_ERROR_JOURNAL=true
JARVIS_ERROR_JOURNAL_PATH=
```

Si `JARVIS_ERROR_JOURNAL_PATH` está vacío, se usa `data/error_journal.jsonl`.

## Regla de trabajo

Cuando aparezca un bug nuevo:

1. Registrar el síntoma en `jarvis.log` o `error_journal.jsonl`.
2. Si se corrige, añadir una prueba que reproduzca el fallo.
3. Si el fallo revela una clase nueva de problema, añadir contexto estructurado al journal.
4. Evitar guardar prompts completos, secretos, tokens, rutas sensibles o dumps enormes.
