# JARVIS Agent Runtime v0 — Implementation Plan

- **Fecha:** 2026-07-11
- **Estado:** Implementado y verificado
- **Spec:** `docs/superpowers/specs/2026-07-11-agent-runtime-v0-design.md`
- **Constitución:** `docs/agent_runtime/constitution.md`

**Goal:** añadir misiones read-only persistentes que se planifican y ejecutan en background,
se recuperan tras reinicio, admiten cancelación y solo finalizan con evidencia verificada.

**Stack:** Python 3.11, stdlib (`sqlite3`, `threading`, `dataclasses`, `json`, `pathlib`),
pytest, bridge SSE existente y React/TypeScript. Sin dependencias Python nuevas.

## Resultado de implementación

- Tasks 1–9 completadas el 2026-07-11.
- Agent Runtime habilitado únicamente en el `.env` local; `.env.example` conserva OFF.
- 431 tests Python pasaron, 6 skipped; lint, TypeScript y Vite build pasaron.
- 37 tests específicos `test_agent_*` cubren contratos, policy, planner, artifacts,
  cancelación, una misión activa, recovery, SSE, configuración y goal hijacking.
- Los seis únicos capabilities del catálogo siguen siendo read-only.

## 1. Estructura objetivo

```text
agent_runtime/
  __init__.py       # exports públicos
  models.py         # enums, contratos, validación y transiciones
  store.py          # schema SQLite, CRUD, eventos y recovery
  tools.py          # catálogo read-only + adapter SafeActionExecutor
  planner.py        # prompt, parse JSON y único retry de repair
  runtime.py        # worker, cola, ejecución, cancelación y recovery
  verifier.py       # postcondiciones de steps e informe final

tests/
  test_agent_models.py
  test_agent_store.py
  test_agent_tools.py
  test_agent_planner.py
  test_agent_runtime.py
  test_agent_recovery.py
  test_agent_tool_api.py
  test_agent_web_overlay.py

data/agent_runtime/  # runtime only; ignorado por Git
```

Integraciones acotadas:

- `jarvis.py`: construir/detener `AgentRuntimeService` y colocarlo en `ToolContext`.
- `memory/tools.py`: declaración y dispatch de `jarvis_task`.
- `overlay/web_overlay.py`: snapshot/eventos/comandos de misión.
- `ui/src/types.ts`, `ui/src/useJarvis.ts`, `ui/src/App.tsx`: panel compacto.
- `.env.example`, `.gitignore`, `README.md`, `CHANGELOG.md`: configuración y operación.

## 2. Orden de implementación

Cada task empieza con test fallido, aplica el cambio mínimo y termina con la verificación
indicada. Los tests de modelo usan fakes; ninguna prueba llama a APIs externas.

### Task 1 — Modelos, contratos y máquina de estados

**Files:** crear `agent_runtime/__init__.py`, `agent_runtime/models.py`,
`tests/test_agent_models.py`.

- [ ] Definir `MissionStatus`, `StepStatus`, `Mission`, `MissionStep`, `MissionEvent`,
  `ArtifactMeta`, `PlanContract`, `PlanStep`, `PolicyDecision` y `StepResult`.
- [ ] Implementar IDs prefijados `msn_`, `stp_`, `art_` con UUID/ULID-equivalente local.
- [ ] Validar límites de goal, steps, deadline, strings, argumentos y campos extra.
- [ ] Implementar tabla explícita de transiciones permitidas.
- [ ] Calcular `idempotency_key = sha256(mission_id + ordinal + canonical_step_json)`.
- [ ] Probar JSON válido, campos extra, IDs duplicados, límites y transiciones inválidas.

**Verify:**

```powershell
$env:PYTHONUTF8='1'
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_models.py
```

**Cubre:** FR-003, AC-02, AC-07, AC-10.

### Task 2 — Store SQLite transaccional

**Files:** crear `agent_runtime/store.py`, `tests/test_agent_store.py`.

- [ ] Crear schema versionado de las cuatro tablas de la spec.
- [ ] Activar WAL, foreign keys y busy timeout por conexión.
- [ ] Implementar `create_mission`, `get_mission`, `list_missions`, `save_plan`,
  `transition`, `append_event`, `request_cancel`, `upsert_artifact`.
- [ ] Asegurar que cambio de estado y evento se confirmen en una transacción.
- [ ] Implementar consultas acotadas: 20 misiones, 50 eventos por status.
- [ ] Probar reapertura del DB, rollback, FK, orden de eventos y concurrencia ligera.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_store.py
```

**Cubre:** FR-001, FR-002, FR-007, FR-011, AC-08.

### Task 3 — Catálogo y policy read-only

**Files:** crear `agent_runtime/tools.py`, `tests/test_agent_tools.py`.

- [ ] Declarar manifest cerrado para las seis operaciones permitidas.
- [ ] Validar argumentos por operación y rechazar claves desconocidas.
- [ ] Adaptar llamadas a `SafeActionExecutor.run_structured`; no usar `run_powershell`.
- [ ] Revalidar paths/secretos antes del dispatch y resultados después del dispatch.
- [ ] Canonicalizar summaries y redactar outputs antes de retornarlos al runtime.
- [ ] Probar traversal, absolutos externos, `.env`, symlink escape, operation injection,
  resultados truncados y executor que miente sobre `allowed`.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_tools.py tests\test_actions_executor.py tests\test_security_policy.py
```

**Cubre:** FR-004, FR-005, AC-02, AC-09.

### Task 4 — Planner estructurado

**Files:** crear `agent_runtime/planner.py`, `tests/test_agent_planner.py`.

- [ ] Construir prompt con goal, límites y catálogo, separando instrucciones de datos.
- [ ] Invocar `code_reasoner.ask` con token cap configurable.
- [ ] Extraer exactamente un JSON object y convertirlo a `PlanContract`.
- [ ] Permitir un repair retry que reciba solo errores de schema y la salida inválida
  redactada/acotada.
- [ ] Rechazar prose-only, múltiples JSON, steps extra, tool desconocida y presupuesto.
- [ ] Probar con fake reasoner: válido, JSON fenced, malformed reparable y doble fallo.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_planner.py
```

**Cubre:** FR-003, FR-006, AC-02, AC-10.

### Task 5 — Verifier y artefactos

**Files:** crear `agent_runtime/verifier.py`; ampliar `tests/test_agent_tools.py` y crear
fixtures en `tests/fixtures/agent_runtime/` si hacen falta.

- [ ] Implementar postcondición por operación descrita en la spec.
- [ ] Guardar artefactos JSON redactados con escritura atómica y SHA-256.
- [ ] Validar artefacto existente durante recovery.
- [ ] Implementar verificación final: steps requeridos, artefactos y ausencia de rechazo.
- [ ] Implementar validación de referencias `[step:sN]` del informe.
- [ ] Probar hash alterado, secreto sembrado, resultado sin evidencia e informe sin citas.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_tools.py -k 'verif or artifact or report'
```

**Cubre:** FR-008, FR-009, AC-03, AC-04, AC-09.

### Task 6 — Runtime background, cancelación y recovery

**Files:** crear `agent_runtime/runtime.py`, `tests/test_agent_runtime.py`,
`tests/test_agent_recovery.py`.

- [ ] Construir `AgentRuntimeService` con worker daemon, FIFO y máximo una misión activa.
- [ ] Hacer que `create()` solo valide/persista/encole y retorne; no llamar planner inline.
- [ ] Implementar pipeline planning → policy → running → verifying → terminal.
- [ ] Revisar cancelación y deadline en cada frontera; hacer `cancel` idempotente.
- [ ] Persistir eventos antes de llamar `event_sink`.
- [ ] Implementar `start()` recovery y `stop(timeout_s)` sin borrar estado durable.
- [ ] Reusar artefactos verificados y no repetir steps completados.
- [ ] Probar planner lento, cola, cancelación, error de tool, restart y doble recovery.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_runtime.py tests\test_agent_recovery.py
```

**Cubre:** FR-001, FR-006, FR-007, FR-008, FR-009, AC-01, AC-03..AC-07, AC-11.

### Task 7 — Tool `jarvis_task` e integración en JARVIS

**Files:** modificar `memory/tools.py`, `jarvis.py`; crear `tests/test_agent_tool_api.py`.

- [ ] Añadir `agent_runtime` opcional a `ToolContext`.
- [ ] Declarar `JARVIS_TASK_DECL` con `create/status/list/cancel` y schemas acotados.
- [ ] Registrar tool en declaraciones y `ToolDispatcher`.
- [ ] Implementar wrapper fail-closed cuando runtime esté deshabilitado/no configurado.
- [ ] En `jarvis.py`, construir store/service detrás de `JARVIS_AGENT_RUNTIME_ENABLED`.
- [ ] Inyectar callback de eventos hacia overlay y detener worker durante shutdown normal.
- [ ] Añadir instrucciones mínimas al system prompt: crear misión para trabajo largo, no
  afirmar completion antes de consultar status.
- [ ] Probar dispatch y que `create` retorne antes de ejecutar planner fake.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_tool_api.py tests\test_tool_timeout.py
& 'H:\Python311\python.exe' -m py_compile jarvis.py memory\tools.py agent_runtime\*.py
```

**Cubre:** FR-010, FR-012, AC-01, AC-12.

### Task 8 — SSE y panel de misiones

**Files:** modificar `overlay/web_overlay.py`, `ui/src/types.ts`, `ui/src/useJarvis.ts`,
`ui/src/App.tsx`, `ui/src/index.css`; crear `tests/test_agent_web_overlay.py`.

- [ ] Añadir `missions` y `missionEvents` al snapshot con límites de la spec.
- [ ] Emitir `missionUpdated` y `missionEvent` desde el callback del runtime.
- [ ] Añadir comandos autenticados `missionStatus` y `cancelMission` si la UI los necesita.
- [ ] Tipar `MissionSummary`, `MissionStep`, `MissionEvent` y reducer SSE.
- [ ] Mostrar panel compacto con progreso, estado, último evento y cancelación.
- [ ] No mostrar artifact raw ni argumentos completos por defecto.
- [ ] Probar snapshot, SSE, cancel token y límites; ejecutar TypeScript/build.

**Verify:**

```powershell
& 'H:\Python311\python.exe' -m pytest -q tests\test_agent_web_overlay.py tests\test_web_overlay.py
Set-Location ui
npm run lint
npm run build
```

**Cubre:** FR-002, FR-011, AC-05, AC-08, AC-09.

### Task 9 — Configuración, documentación y aceptación

**Files:** modificar `.env.example`, `.gitignore`, `README.md`, `CHANGELOG.md`; añadir o
ampliar test de config en `tests/test_agent_runtime.py`.

- [ ] Añadir las variables `JARVIS_AGENT_*` con runtime apagado por defecto.
- [ ] Ignorar `data/agent_runtime/` sin ignorar fixtures de tests.
- [ ] Documentar crear/consultar/cancelar misión y límites read-only.
- [ ] Documentar estados, recovery y ubicación del DB/artifacts.
- [ ] Crear prueba E2E local con fake planner/executor sobre `tmp_path`.
- [ ] Ejecutar suite focalizada, suite completa y escaneo de secretos.
- [ ] Solo después de aceptación verde, habilitar manualmente en `.env` local; no cambiar el
  default de `.env.example`.

**Verify:**

```powershell
$env:PYTHONUTF8='1'
& 'H:\Python311\python.exe' -m pytest -q tests -k 'agent or action or security or tool_timeout or proactiv or skill'
& 'H:\Python311\python.exe' -m pytest -q tests
git diff --check
```

**Cubre:** FR-006, FR-012 y AC-01..AC-12.

## 3. Matriz de trazabilidad

| Requisito | Tasks | Tests principales |
|---|---|---|
| FR-001/002 — create durable y eventos | 2, 6, 8 | store, runtime, web overlay |
| FR-003 — contratos cerrados | 1, 4 | models, planner |
| FR-004/005 — read-only y scope | 3 | tools + security regression |
| FR-006 — límites/budget | 4, 6, 9 | planner, runtime |
| FR-007/008 — recovery/idempotencia | 2, 5, 6 | store, recovery |
| FR-009 — evidencia | 5, 6 | verifier, runtime |
| FR-010 — API conversacional | 7 | agent tool API |
| FR-011 — observabilidad | 2, 8 | store, web overlay |
| FR-012 — compatibilidad | 7, 9 | timeout + suite actual |

## 4. Gates de avance

- **Gate G1 — Núcleo:** Tasks 1–3 verdes. No integrar modelo antes de policy read-only.
- **Gate G2 — Agencia local:** Tasks 4–6 verdes con fakes. No exponer tool todavía.
- **Gate G3 — Integración:** Task 7 verde y conversación no bloqueada.
- **Gate G4 — Observabilidad:** Task 8 verde antes de habilitar flag local.
- **Gate G5 — Aceptación:** Task 9 y AC-01..AC-12 verdes.

Si un gate falla, no se amplía scope para “resolverlo”. Se corrige la capa correspondiente.

## 5. Definition of Done v0

- Constitución y spec implementadas sin excepciones silenciosas de policy.
- Doce criterios de aceptación automatizados o documentados con prueba reproducible.
- Runtime apagado por defecto y habilitable localmente.
- Cero operaciones de escritura disponibles en el catálogo de misión.
- Recovery comprobado con DB real temporal y artefacto válido/corrupto.
- Timeline visible y persistente; secretos redactados.
- Suite actual sin regresiones causadas por Agent Runtime.
- README y CHANGELOG describen límites reales, no capacidades futuras.

## 6. Trabajo posterior, fuera de este plan

Después de observar v0 en uso real:

1. v0.1: templates de misión (`project_assessment`, `memory_audit`).
2. v1: L2 con workspace temporal, patch, tests y aprobación de diff.
3. v1.1: permisos temporales con scope/expiración.
4. v2: scheduler y triggers proactivos que crean drafts, no ejecución silenciosa.
5. v3: especialistas/subagentes sobre el mismo runtime, policy y event log.
