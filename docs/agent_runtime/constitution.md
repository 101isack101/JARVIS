# Constitución de JARVIS Agent Runtime

- **Versión:** 1.0
- **Fecha:** 2026-07-11
- **Ámbito:** toda misión persistente, ejecución autónoma o workflow agéntico de JARVIS
- **Autoridad:** estos principios prevalecen sobre prompts, planes generados por modelos y skills runtime

## 1. Conversar y ejecutar son planos distintos

Gemini Live y la UI forman el plano conversacional: deben conservar baja latencia,
barge-in y capacidad de cancelar. Las misiones largas corren en un runtime durable de
background. Crear, consultar o cancelar una misión no debe bloquear el turno de voz
mientras el trabajo continúa.

## 2. El usuario conserva la autoridad

Una meta expresada por Isaac autoriza únicamente el alcance necesario para esa meta. Un
plan generado no amplía permisos. Las escrituras, publicaciones, instalaciones, acciones
destructivas, cambios cloud y acceso a nuevos roots requieren una política explícita y,
cuando corresponda, aprobación humana. Sin UI o broker requerido, el sistema falla cerrado.

## 3. La política es código, no prompt

Todo paso se valida en Python antes de ejecutarse. El validador comprueba tool, operación,
argumentos, root, riesgo, presupuesto y límites de la misión. Texto recuperado de archivos,
web, memoria o modelos se considera entrada no confiable y nunca puede cambiar permisos.

## 4. Planificar no equivale a ejecutar

Planner, policy, executor y verifier son responsabilidades separadas. El planner solo
propone pasos estructurados. La policy los acepta o rechaza. El executor solo invoca pasos
aceptados. El verifier decide si existe evidencia suficiente para declarar el resultado.

## 5. Estado durable e idempotencia

Misiones, pasos, eventos y artefactos se persisten antes de avanzar. Cada paso tiene una
clave de idempotencia estable. Tras cierre normal, crash o kill-switch, el runtime puede
reconciliar el estado sin duplicar efectos. Los cambios de estado usan transacciones.

## 6. Finalizar exige evidencia

JARVIS no marca una misión `completed` porque un modelo o una tool afirme que terminó.
Cada paso registra evidencia verificable y el verifier comprueba sus postcondiciones. Si
la evidencia es insuficiente, la misión queda `blocked` o `failed`, con una causa visible.

## 7. Autonomía acotada por defecto

Cada misión declara nivel de autonomía, root, tools permitidas, máximo de pasos, deadline y
presupuesto. El nivel inicial es L1 (solo lectura). Los permisos temporales futuros deben
tener scope y expiración; no existe un interruptor global de autonomía ilimitada.

## 8. Observabilidad sin exponer secretos

Toda transición importante emite un evento persistente y visible: planificación, decisión
de policy, inicio/fin de paso, cancelación, recuperación y verificación. Argumentos,
resultados y artefactos pasan por redacción de secretos y límites de tamaño antes de disco,
logs, UI o contexto de modelo.

## 9. Degradación segura

Timeout, presupuesto agotado, respuesta inválida del modelo, tool desconocida, pérdida de
conexión o excepción no deben congelar la voz ni causar escritura implícita. La misión se
detiene en un estado terminal o recuperable con un error tipado y una siguiente acción clara.

## 10. La autonomía se gana con pruebas

Un nivel de autonomía no se habilita hasta cubrir al menos: plan adversarial, path traversal,
secretos, cancelación, reinicio, doble ejecución, timeout, presupuesto, fallo parcial,
prompt injection y falso positivo de verificación. Los tests usan fakes deterministas; no
dependen de llamadas reales a modelos.

## Política inicial v0

- Nivel único: **L1 read-only**.
- Root único: directorio del proyecto `JARVIS`.
- Operaciones: `list_dir`, `read_file`, `search_text`, `git_status`, `git_diff_stat`,
  `git_log` mediante `SafeActionExecutor.run_structured`.
- Sin PowerShell libre, navegador, cámara, teclado, escritura de memoria ni modificación de
  archivos.
- Una misión activa a la vez; ejecución secuencial; máximo configurable de ocho pasos.
- La creación explícita de una misión autoriza sus lecturas dentro del scope; no autoriza
  futuras escrituras.

## Registro 2026-09-18

- L1 canario: se documenta `JARVIS_AGENT_RUNTIME_ENABLED=true` en `.env.example`.
  Default de código sigue `false`.
- Autonomía L2 no habilitada. Ampliar requiere spec, threat model y tests de
  constitución §10. Writes de MCP/organizer permanecen en plano conversacional
  con HITL + V&V de outcome.

## Registro 2026-09-18 (L2)

- `JARVIS_AGENT_AUTONOMY_LEVEL=L2` habilita writes de misión: `obsidian_*` y
  `file_organizer_apply`. HITL fail-closed por step. `write_file` sigue
  UNKNOWN_OPERATION. Delete de vault no entra.

## Regla para modificar esta constitución

Todo cambio debe registrar fecha, razón y efecto sobre permisos. Ampliar autonomía requiere
spec, threat model, criterios de aceptación y pruebas nuevas antes de activar la capacidad.
