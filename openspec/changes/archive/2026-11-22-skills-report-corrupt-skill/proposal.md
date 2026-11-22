# Proposal

## Why

`SkillRegistry.reload()` en `skills/registry.py` (~línea 261) envuelve la carga de cada `skills/*.json` en `except Exception: continue`. Un JSON roto (sintaxis inválida, raíz que no es objeto, campos con tipos erróneos) hace desaparecer la skill sin dejar rastro: ni log ni error journal. Además, un JSON válido sin `name` se descarta en silencio por `if skill.name:`. El usuario solo nota que la skill "no existe" y no tiene forma de saber por qué. La v1.12 ya introdujo el patrón `record_error(...)` para estos casos; este change lo aplica a la carga de skills.

## What Changes

- Al cargar `skills/*.json`, cada archivo que se salte (JSON inválido, error de lectura, estructura inválida o sin `name`) se registra en el error journal con `source="skills.registry.load"`, `severity="warning"`, el nombre del archivo y el error.
- La carga continúa con el resto de archivos: un archivo malo no afecta a los demás ni a las skills builtin.
- Un JSON válido pero sin `name` (o con `name` vacío) se sigue saltando, pero ahora con un warning en el journal (decisión: se reporta, no se inventa un nombre a partir del archivo).
- Sin cambios de API pública: `reload()` sigue sin lanzar excepciones.

## Capabilities

### New Capabilities
- `skill-registry`: carga de skills locales (`skills/*.json`) en `SkillRegistry`, incluyendo el reporte observable de archivos inválidos saltados.

### Modified Capabilities

(ninguna: no existen specs previos en `openspec/specs/`)

## Fuera de alcance

- El fallback de parseo YAML en `_parse_frontmatter` (`skills/registry.py` ~línea 188): ya cae a un parser manual de `clave: valor` y no pierde la skill.
- La carga de skills importadas `*/SKILL.md` (`_skill_from_markdown`, incluido su `return None` por falta de `name`/`description`).
- Mensajes de usuario/HUD o voz sobre skills rotas; solo se escribe en el error journal.
- Cambios de `releases/`, `VERSION`, `CHANGELOG.md` y demás archivos de release.
- Otros `except Exception` silenciosos del repo (`proactivity/`, `study/`, etc.).

## Impact

- Código: `skills/registry.py` (método `SkillRegistry.reload`, import de `record_error`), ~15-25 líneas.
- Tests: `tests/test_skills_registry.py` (casos nuevos con `JARVIS_ERROR_JOURNAL_PATH` en `tmp_path`, patrón de `tests/test_error_journal.py`).
- Dependencias: ninguna nueva; reutiliza `telemetry.error_journal.record_error` (fail-safe y con redacción de secretos).
- Riesgo: bajo. Si el journal falla o está deshabilitado, `record_error` no interrumpe la carga.
