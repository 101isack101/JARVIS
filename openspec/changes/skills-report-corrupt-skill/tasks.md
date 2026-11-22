# Tasks

## 1. Tests primero (spec `skill-registry`)

- [ ] 1.1 En `tests/test_skills_registry.py`, añadir helper local que fija `JARVIS_ERROR_JOURNAL_PATH` (en `tmp_path`) y `JARVIS_ERROR_JOURNAL=true` con `monkeypatch`, y lee el journal como JSONL (patrón de `tests/test_error_journal.py`); verificar que los tests existentes siguen pasando con `H:\Python311\python.exe -m pytest tests/test_skills_registry.py`
- [ ] 1.2 Añadir test "JSON roto + JSON válido": `broken.json` inválido y `good.json` válido; afirmar que `good_skill` está en `registry.list()` y que el journal tiene una entrada `source="skills.registry.load"`, `severity="warning"`, `context["file"] == "broken.json"` y `error_type == "JSONDecodeError"`; debe fallar antes de implementar
- [ ] 1.3 Añadir test "JSON sin `name`": `noname.json` válido sin `name`; afirmar que no se registra ninguna skill nueva (builtin intactas) y que el journal tiene una entrada para `noname.json` con mensaje de skill sin nombre; debe fallar antes de implementar
- [ ] 1.4 Añadir tests de borde: raíz JSON que es lista (`list.json`) queda reportada sin lanzar excepción; archivo roto ordenado antes (`a_broken.json`) de uno válido (`z_valid.json`) no corta la carga y genera exactamente una entrada; directorio solo con skills válidas no escribe entradas `skills.registry.load`

## 2. Implementación en `skills/registry.py`

- [ ] 2.1 Importar `record_error` desde `telemetry.error_journal` y reemplazar `except Exception: continue` en `SkillRegistry.reload()` por `except Exception as exc:` que llame `record_error("skills.registry.load", exc=exc, severity="warning", message=..., context={"file": path.name})` y continúe; verificar con `H:\Python311\python.exe -m pytest tests/test_skills_registry.py` (test 1.2 y 1.4 en verde)
- [ ] 2.2 Reportar el caso JSON válido sin `name` (rama `else` de `if skill.name:`) con `record_error(..., severity="warning", message=<skill sin nombre>, context={"file": path.name})` sin `exc`; verificar que el test 1.3 pasa
- [ ] 2.3 Confirmar el alcance: `git diff --stat` solo muestra `skills/registry.py` y `tests/test_skills_registry.py` (más los artefactos de este change), sin tocar `_parse_frontmatter`, `releases/`, `VERSION` ni `CHANGELOG.md`

## 3. Verificación de integración

- [ ] 3.1 Ejecutar `H:\Python311\python.exe -m pytest tests/test_skills_registry.py tests/test_error_journal.py` y verificar que todo pasa
- [ ] 3.2 Ejecutar `openspec validate skills-report-corrupt-skill --strict` y verificar que sigue válido tras la implementación

## Workflow follow-up

- Archivar el change (`openspec archive skills-report-corrupt-skill`) tras la revisión; esto promoverá el delta a `openspec/specs/skill-registry/spec.md`.
- El bump de versión y la entrada de CHANGELOG, si se desean, son un release aparte (fuera de alcance de este change).
