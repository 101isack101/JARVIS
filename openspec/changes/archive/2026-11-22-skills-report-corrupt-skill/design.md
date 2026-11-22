# Design

## Context

`SkillRegistry.reload()` (`skills/registry.py`, ~líneas 255-262) itera `skill_dir.glob("*.json")` y por cada archivo hace `json.loads` + `SkillDefinition.from_dict`; cualquier excepción termina en `except Exception: continue`, y un `name` vacío se ignora con `if skill.name:`. `from_dict` es tolerante a campos ausentes (todo con `.get`), pero falla con `AttributeError`/`TypeError` si la raíz no es un dict o `triggers`/`tools` no son iterables. Ver proposal.md - Why para la motivación.

El error journal (`telemetry/error_journal.py`) ya ofrece `record_error(source, *, exc, message, severity, context, path)`: fail-safe (nunca lanza), redacta secretos y respeta `JARVIS_ERROR_JOURNAL` / `JARVIS_ERROR_JOURNAL_PATH`. `skills/registry.py` aún no lo importa.

## Goals / Non-Goals

**Goals:**
- Dejar rastro en el journal de cada `*.json` saltado, con archivo y error, sin cambiar qué skills se cargan.
- Mantener `reload()` sin excepciones y con el mismo orden/semántica de override (los JSON locales pisan a builtin e importadas).

**Non-Goals:**
- No introducir un nuevo tipo de excepción, validador de esquema ni cuarentena de archivos.
- No exponer el aviso en UI/voz ni deduplicar entradas entre `reload()` repetidos.

## Decisions

1. **Reutilizar `record_error` con `source="skills.registry.load"`, `severity="warning"`.** Es el patrón de v1.12; la severidad es `warning` porque el sistema sigue operando (degradado). Alternativa descartada: solo `logger.warning`, porque no es agrupable/consultable y el HUD lee del journal (`tail_errors`).

2. **Nombre de archivo en `context={"file": path.name}`** (no la ruta completa) y `message` en español, p. ej. `"skill JSON omitida: <archivo>"`. El nombre basta para localizar el archivo (el directorio es fijo) y evita volcar rutas del usuario; la ruta completa queda disponible vía traceback del journal si hiciera falta. El `message` incluye el nombre para que sea legible en `tail_errors`, que solo muestra `message`.

3. **JSON válido sin `name` → se salta y se reporta (warning), no se infiere el nombre del archivo.** Inferirlo cambiaría qué skills existen (comportamiento observable nuevo, posibles colisiones con builtin) y quedaría fuera del alcance "solo hacer visible el fallo". Se registra con `message` "skill sin nombre" y sin `exc` (no hubo excepción).

4. **Estructura:** un único `try/except Exception as exc` por archivo, con la comprobación de nombre dentro del `try`. Con `exc` se pasa la excepción; para el caso sin `name` se llama a `record_error` sin `exc`. Se mantiene `except Exception` (no se estrecha a tipos concretos) para conservar la garantía de que ningún archivo malo interrumpe la carga.

## Risks / Trade-offs

- [Ruido: `reload()` se llama varias veces y repite la misma entrada por archivo roto] → Aceptado; es una condición persistente y poco frecuente. Deduplicar queda fuera de alcance.
- [`context`/`message` podrían contener datos sensibles] → `record_error` redacta con `security.secret_filter`; solo se registra el nombre de archivo, no su contenido.
- [Si el journal está deshabilitado o falla la escritura, no hay rastro] → Es el contrato fail-safe del journal; la carga no se interrumpe.
- [Cambio de import en `skills/registry.py`: riesgo de import circular] → `telemetry.error_journal` solo importa stdlib (y `security.secret_filter` de forma diferida), por lo que el riesgo es bajo; verificar con la suite de `tests/test_skills_registry.py`.
