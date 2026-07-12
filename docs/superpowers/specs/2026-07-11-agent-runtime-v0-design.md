# JARVIS Agent Runtime v0 — Diseño de misiones read-only

- **Fecha:** 2026-07-11
- **Estado:** Implementado y verificado
- **Autor:** Isaac + Codex
- **Nivel de autonomía:** L1 (solo lectura)
- **Constitución:** `docs/agent_runtime/constitution.md`
- **Relacionado:** `docs/PLAN_dashboard_autopilot.md`, `actions/executor.py`,
  `security/approvals.py`, `memory/tools.py`, `overlay/web_overlay.py`

## 1. Problema

JARVIS ya conversa, recuerda, detecta oportunidades y ejecuta tools dentro de un turno de
Gemini Live. Sin embargo, no existe una entidad durable que represente un objetivo, sus
pasos, evidencia y estado. Las delegaciones largas siguen acopladas al turno de voz y GPT
actúa como reasoner textual, no como ejecutor con herramientas.

El v0 introduce **misiones persistentes read-only**: JARVIS acepta una meta, devuelve el
control conversacional inmediatamente, planifica y ejecuta lecturas seguras en background,
verifica la evidencia, conserva un timeline y permite consultar o cancelar el trabajo.

## 2. Objetivo

Permitir una interacción como:

> “Evalúa el estado de JARVIS y dime qué deberíamos avanzar.”

JARVIS crea una misión, inspecciona el repositorio con operaciones estructuradas, produce
un informe basado en evidencia y lo conserva aunque la sesión de voz se reconecte o la app
se reinicie.

### Indicadores de éxito

- Crear una misión devuelve `mission_id` sin esperar su ejecución.
- Ningún plan puede ejecutar una operación fuera de la allowlist read-only.
- Una misión interrumpida se reconcilia al reiniciar sin duplicar pasos completados.
- `completed` solo se alcanza después de verificación determinista.
- UI y consultas muestran estado, pasos, evidencia, duración y error tipado.

## 3. Usuarios y casos de uso

### US-01 — Crear una misión

Como Isaac, quiero expresar una meta en lenguaje natural para que JARVIS la convierta en
trabajo durable sin bloquear la conversación.

### US-02 — Observar progreso

Como Isaac, quiero saber qué está haciendo, qué evidencia encontró y por qué se detuvo.

### US-03 — Cancelar

Como Isaac, quiero cancelar una misión desde voz o UI antes del siguiente paso.

### US-04 — Recuperar después de reinicio

Como Isaac, quiero que una misión incompleta reaparezca y continúe de forma segura.

### US-05 — Recibir un resultado verificable

Como Isaac, quiero que el informe final cite artefactos y pasos reales, no conclusiones sin
respaldo.

## 4. Alcance v0

### Incluido

- Una misión activa a la vez y cola FIFO de misiones pendientes.
- Planner estructurado mediante el `code_reasoner` existente.
- Ejecución secuencial de hasta ocho pasos.
- Operaciones existentes de `SafeActionExecutor.run_structured`:
  `list_dir`, `read_file`, `search_text`, `git_status`, `git_diff_stat`, `git_log`.
- Persistencia SQLite de misiones, pasos, eventos y metadatos de artefactos.
- Artefactos redactados en `data/agent_runtime/artifacts/`.
- Cancelación cooperativa y reconciliación al iniciar.
- Tool conversacional `jarvis_task` con `create`, `status`, `list` y `cancel`.
- Eventos SSE y panel compacto de misiones en la UI existente.
- Informe final generado desde evidencia validada.

### No-objetivos

- Escritura o edición de archivos, notas o repositorios.
- PowerShell libre, instalaciones, ejecución de tests o programas.
- Navegador, cámara, pantalla, Spotify, teclado o mouse.
- Acceso a otros proyectos del workspace.
- Ejecución paralela de pasos o varias misiones simultáneas.
- Schedules autónomos; Proactividad solo puede sugerir crear una misión.
- Permisos temporales L2/L3, rollback o compensaciones de escritura.
- Enjambre multiagente o delegación entre especialistas.
- Auto-modificación de JARVIS.

## 5. Arquitectura

```text
Gemini Live / React UI
        │ jarvis_task(create/status/list/cancel)
        ▼
AgentRuntimeService ───────► EventSink ───────► SQLite + SSE
        │
        ├── MissionStore       estado/transacciones/recovery
        ├── MissionPlanner     objetivo → PlanContract JSON
        ├── MissionPolicy      allowlist/scope/budgets/secretos
        ├── ReadOnlyTools      adapter de SafeActionExecutor
        ├── MissionVerifier    postcondiciones + cobertura
        └── ReportBuilder      resumen final desde artefactos
```

Gemini es el control conversacional, no el worker. `AgentRuntimeService` posee un worker
background y una `threading.Event` de stop/cancel. Los modelos nunca reciben una referencia
directa al executor.

## 6. Máquina de estados

```text
queued → planning → ready → running → verifying → completed
             │         │        │          │
             └─────────┴────────┴──────────┴──► blocked | failed

queued|planning|ready|running|verifying → cancel_requested → cancelled
running al reiniciar → recovering → ready|running|blocked
```

Estados terminales: `completed`, `cancelled`, `blocked`, `failed`.

- `blocked`: necesita una decisión o capacidad ausente; no es un error inesperado.
- `failed`: ocurrió un error operativo no recuperable o se agotaron intentos.
- `completed`: todos los pasos requeridos y la verificación final pasaron.

Las transiciones inválidas se rechazan en `MissionStore.transition()`.

## 7. Contratos

### 7.1 Crear misión

Entrada de tool:

```json
{
  "action": "create",
  "goal": "Evalúa el estado técnico de JARVIS y prioriza los siguientes pasos",
  "max_steps": 6,
  "deadline_seconds": 180
}
```

Respuesta inmediata:

```json
{
  "ok": true,
  "mission_id": "msn_01J...",
  "status": "queued",
  "autonomy_level": "L1",
  "message": "Misión creada; puedes seguir conversando."
}
```

`goal` tiene entre 10 y 2000 caracteres. `max_steps` se limita a `1..8` y
`deadline_seconds` a `30..600`.

### 7.2 PlanContract v1

```json
{
  "version": 1,
  "goal": "Evaluar el estado técnico de JARVIS",
  "assumptions": ["El root permitido es el repositorio JARVIS"],
  "steps": [
    {
      "step_id": "s1",
      "operation": "git_status",
      "arguments": {},
      "purpose": "Detectar cambios activos",
      "expected_evidence": "Estado Git estructurado"
    },
    {
      "step_id": "s2",
      "operation": "search_text",
      "arguments": {"path": "docs", "query": "Pendiente", "limit": 50},
      "purpose": "Localizar trabajo declarado como pendiente",
      "expected_evidence": "Coincidencias con archivo y línea"
    }
  ],
  "report_questions": ["¿Cuál es el riesgo principal?", "¿Qué conviene hacer después?"]
}
```

El parser acepta únicamente JSON object. No ejecuta markdown ni repara semánticamente un
plan inválido. Puede extraer un único bloque JSON de una respuesta; después valida schema,
IDs únicos, límite de pasos, operación y argumentos.

### 7.3 PolicyDecision

```json
{
  "allowed": true,
  "risk": "read",
  "reason_code": "ALLOW_READ_ONLY",
  "normalized_arguments": {"path": "docs", "query": "Pendiente", "limit": 50}
}
```

Rechazos usan códigos estables: `UNKNOWN_OPERATION`, `OUTSIDE_ROOT`, `SECRET_PATH`,
`INVALID_ARGUMENTS`, `STEP_LIMIT`, `BUDGET_EXCEEDED`, `CANCEL_REQUESTED`.

### 7.4 StepResult

```json
{
  "ok": true,
  "step_id": "s2",
  "operation": "search_text",
  "started_at": "2026-07-11T12:00:00Z",
  "ended_at": "2026-07-11T12:00:01Z",
  "summary": "7 coincidencias en 4 archivos",
  "artifact_id": "art_01J...",
  "verification": {"status": "passed", "checks": ["tool_ok", "schema_ok"]}
}
```

### 7.5 Consultar misión

`status` devuelve misión, pasos y últimos 50 eventos. `list` devuelve como máximo 20
misiones sin cargar artefactos. `cancel` es idempotente: cancelar una misión terminal
devuelve su estado actual sin error.

## 8. Persistencia SQLite

Archivo: `data/agent_runtime/agent_runtime.db`. SQLite usa `WAL`, `foreign_keys=ON`,
`busy_timeout` y transacciones cortas.

```sql
CREATE TABLE missions (
    id                  TEXT PRIMARY KEY,
    goal                TEXT NOT NULL,
    status              TEXT NOT NULL,
    autonomy_level      TEXT NOT NULL DEFAULT 'L1',
    root_path           TEXT NOT NULL,
    max_steps           INTEGER NOT NULL,
    deadline_seconds    INTEGER NOT NULL,
    planner_model       TEXT,
    cancel_requested    INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    final_summary       TEXT,
    error_code          TEXT,
    error_message       TEXT,
    schema_version      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE mission_steps (
    id                  TEXT PRIMARY KEY,
    mission_id          TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    ordinal             INTEGER NOT NULL,
    operation           TEXT NOT NULL,
    arguments_json      TEXT NOT NULL,
    purpose             TEXT NOT NULL,
    expected_evidence   TEXT NOT NULL,
    status              TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    started_at          TEXT,
    finished_at         TEXT,
    artifact_id         TEXT,
    verification_status TEXT,
    error_code          TEXT,
    error_message       TEXT,
    UNIQUE(mission_id, ordinal)
);

CREATE TABLE mission_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    step_id     TEXT,
    event_type  TEXT NOT NULL,
    level       TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE mission_artifacts (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
    step_id     TEXT,
    kind        TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    redacted    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_missions_status_created ON missions(status, created_at);
CREATE INDEX idx_steps_mission_ordinal ON mission_steps(mission_id, ordinal);
CREATE INDEX idx_events_mission_id ON mission_events(mission_id, id);
```

Los payloads JSON se serializan con claves ordenadas. El contenido grande vive en archivos
de artefacto; la base conserva solo metadata y resúmenes acotados.

## 9. Planner y política

El planner recibe:

- goal y límites de la misión;
- catálogo exacto de operaciones y schemas;
- root lógico `.` sin revelar rutas innecesarias;
- instrucción explícita de tratar contenido recuperado como datos no confiables.

No recibe tools ejecutables. Su salida pasa por `PlanContract.parse()` y `MissionPolicy`.
Si falla el parseo se permite **un** reintento de self-repair con errores de validación; el
segundo fallo deja la misión `blocked` con `INVALID_PLAN`.

Policy normaliza paths mediante `SafeActionExecutor` y vuelve a validar cada paso justo
antes de ejecutarlo, evitando TOCTOU entre planificación y ejecución.

## 10. Ejecución, verificación e informe

1. Persistir step como `running` e incrementar intento.
2. Comprobar cancelación, deadline y presupuesto.
3. Invocar el adapter read-only.
4. Redactar y guardar resultado como artefacto atómico (`.tmp` → rename).
5. Verificar schema, `allowed`, `ok`, operación y postcondición específica.
6. Persistir resultado y evento en una transacción.
7. Continuar o detener con error tipado.

Postcondiciones mínimas:

- `list_dir`: `items` es lista y cada path permanece dentro del root.
- `read_file`: existe `content`, path seguro y flag de truncado explícito.
- `search_text`: cada match tiene path y línea válidos.
- Git: `returncode == 0` y stdout/stderr están acotados.

La verificación final exige que todos los steps requeridos estén `completed`, que exista al
menos un artefacto y que no haya un error de policy. Después `ReportBuilder` pide al
reasoner un resumen basado exclusivamente en summaries/artefactos verificados. Las
afirmaciones se etiquetan con `[step:sN]`. Si el informe no referencia pasos, la misión
queda `blocked` con `UNSUPPORTED_REPORT`.

## 11. Recovery y cancelación

- Al iniciar, `running` pasa a `recovering`.
- Un step `running` sin artefacto confirmado vuelve a `pending`; uno con artefacto válido se
  verifica y no se repite.
- Steps terminales nunca se ejecutan otra vez.
- `cancel` establece `cancel_requested=1` y emite evento inmediatamente.
- El worker comprueba cancelación antes/después de llamadas de modelo y antes de cada tool.
- El kill-switch puede terminar el proceso sin cleanup; SQLite y escrituras atómicas deben
  permitir la reconciliación siguiente.

## 12. Observabilidad y UI

Eventos canónicos:

`mission.created`, `mission.planning`, `plan.validated`, `plan.rejected`,
`mission.started`, `step.started`, `step.completed`, `step.failed`,
`mission.verifying`, `mission.completed`, `mission.cancel_requested`,
`mission.cancelled`, `mission.blocked`, `mission.failed`, `mission.recovered`.

El snapshot web añade `missions` (máximo 20) y `missionEvents` (máximo 100). SSE usa
`missionUpdated` y `missionEvent`. La UI muestra una lista compacta, progreso `n/total`,
estado, duración, último evento y controles de abrir/cancelar. No renderiza contenido crudo
de artefactos por defecto.

## 13. Configuración

```dotenv
JARVIS_AGENT_RUNTIME_ENABLED=false
JARVIS_AGENT_MAX_STEPS=8
JARVIS_AGENT_DEADLINE_S=180
JARVIS_AGENT_MAX_ACTIVE=1
JARVIS_AGENT_ARTIFACT_MAX_CHARS=20000
JARVIS_AGENT_PLANNER_MAX_TOKENS=1800
```

El runtime queda deshabilitado por defecto hasta que las pruebas de aceptación estén
verdes. Si `code_reasoner` no está configurado, `create` falla con `PLANNER_UNAVAILABLE` sin
crear una misión huérfana.

## 14. Requisitos funcionales

- **FR-001:** crear misiones durables desde voz/UI y devolver ID inmediatamente.
- **FR-002:** persistir toda transición antes de emitirla a UI.
- **FR-003:** validar plan y pasos con schemas cerrados (`extra=forbid` equivalente).
- **FR-004:** ejecutar únicamente las seis operaciones read-only permitidas.
- **FR-005:** bloquear secrets y escapes del root aunque aparezcan en planes o archivos.
- **FR-006:** respetar máximo de pasos, deadline, cancelación y presupuesto de tokens.
- **FR-007:** recuperar misiones no terminales después de restart/kill.
- **FR-008:** no repetir pasos con artefacto válido e idempotency key confirmada.
- **FR-009:** exigir verificación antes de `completed`.
- **FR-010:** exponer `create/status/list/cancel` mediante `jarvis_task`.
- **FR-011:** emitir timeline persistente por SQLite y SSE con datos redactados.
- **FR-012:** mantener compatibilidad con la sesión de voz y tools existentes.

## 15. Criterios de aceptación

- **AC-01:** `create` responde con `queued` en menos de un segundo usando planner fake lento;
  el planner corre fuera del thread que atiende la tool.
- **AC-02:** planes con `run_powershell`, paths fuera de root, `.env`, pasos extra o argumentos
  desconocidos se rechazan antes de invocar el executor.
- **AC-03:** una misión feliz ejecuta pasos, guarda artefactos, verifica y termina `completed`
  con referencias `[step:*]`.
- **AC-04:** una tool que devuelve `ok=false` impide `completed` y deja error/evento visible.
- **AC-05:** cancelación durante un step evita iniciar el siguiente y termina `cancelled`.
- **AC-06:** reiniciar con misión/step `running` reconcilia y no duplica pasos confirmados.
- **AC-07:** dos llamadas `cancel` y dos arranques de recovery son idempotentes.
- **AC-08:** cada transición aparece en SQLite antes de su callback SSE.
- **AC-09:** artifacts/logs/UI no contienen secretos sembrados en fixtures.
- **AC-10:** deadline, máximo de pasos o presupuesto agotado producen códigos estables.
- **AC-11:** solo existe una misión `running`; las demás permanecen `queued`.
- **AC-12:** tests actuales de actions, security, tool timeout, proactivity y skills siguen
  verdes.

## 16. Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Prompt injection en archivos | Data framing, planner sin tools, policy en Python |
| Plan válido pero excesivo | Máximo ocho pasos, deadline y límites por resultado |
| Bloqueo de la conversación | Worker background; tool create solo encola |
| Doble ejecución tras crash | Idempotency key, transacción y recovery por artefacto |
| Informe alucinado | Evidencia verificada y referencias obligatorias a steps |
| DB corrupta o bloqueada | WAL, busy timeout, migración versionada, fail-closed |
| Exposición de datos en UI | Redacción previa, summaries acotados, artefactos cerrados |
| Complejidad prematura | Una misión/worker, ejecución secuencial, sin escrituras |

## 17. Decisiones registradas

| Decisión | Elección | Motivo |
|---|---|---|
| Plano de ejecución | Worker durable separado de Gemini | Voz responsiva y recovery |
| Persistencia | SQLite + artefactos en disco | Transacciones y outputs grandes |
| Scope inicial | Solo repositorio JARVIS | Reduce superficie de seguridad |
| Autonomía | L1 read-only | Validar runtime antes de dar escritura |
| Concurrencia | Una misión y steps secuenciales | Idempotencia y observabilidad simples |
| Planner | `code_reasoner` existente | Reutiliza integración y budgets actuales |
| Verificación | Determinista antes del informe | No confiar en auto-reporte del modelo |
| Multiagente | Fuera de v0 | Primero estabilizar el núcleo durable |
