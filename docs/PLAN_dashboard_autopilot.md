# Plan: Dashboard/Log Viewer + Autopilot

Fecha: 2026-06-13 · Estado: propuesto (sin implementar)

Plan de los dos pendientes del roadmap, con dependencias y orden. No incluye
código todavía; es el mapa para decidir.

## Orden recomendado y por qué

**1º Dashboard/log viewer → 2º Autopilot.**

No es solo cuestión de riesgo. El dashboard es **precondición de seguridad** de
Autopilot: no se debe dar acción autónoma a Jarvis sin antes tener visibilidad de
sus decisiones. El dashboard da los ojos; Autopilot da las manos. Además, el log
de ejecución de Autopilot (plan, dry-run, cada paso, aprobación) **alimenta** el
mismo dashboard — así que construirlo primero deja el canal de observabilidad
listo para cuando Autopilot empiece a actuar.

```
Dashboard (observabilidad)  ──►  Autopilot (acción autónoma)
        feed de decisiones            emite eventos al feed
```

---

## Feature 1 — Dashboard / Log Viewer

**Objetivo:** una vista para inspeccionar qué está pensando/haciendo Jarvis:
tools en curso y completadas, eventos, latencia por turno, budgets, memoria y el
journal de errores.

**Ventaja:** gran parte ya existe. Los datos fluyen por SSE (`events`,
`agentEvents`, `latency`, `budget`, `memory`) y hay componentes huérfanos ya
escritos: `ui/src/components/ThoughtLog.tsx` (feed de decisiones) y
`ui/src/components/Telemetry.tsx` (budget rings).

### Fase D1 — Reactivar componentes huérfanos (frontend, bajo riesgo)

- Verificar/extender los tokens Tailwind que usan (`jarvis-green`, `jarvis-red`,
  `jarvis-yellow`, `jarvis-text-dim`) en la config de Tailwind v4; si faltan,
  definirlos para que rinda igual que el HUD actual.
- Montar `ThoughtLog` y `Telemetry` en una vista nueva (no romper el HUD actual).
- Datos: ya disponibles en `useJarvis` (`ui.agentTools`, `ui.events`,
  `ui.latencyLines`, `ui.budget`, `ui.memory`). Cero backend nuevo en esta fase.
- Verificación: `tsc + vite build`; los paneles muestran datos en vivo.

### Fase D2 — Endpoint de error journal (backend)

- Nuevo endpoint read-only en `overlay/web_overlay.py` (`GET /errors`) que lea las
  últimas N líneas de `data/error_journal.jsonl` (formato `{ts, severity, source,
  message}` vía `telemetry/error_journal.py`). Tail acotado (p.ej. 200) para no
  cargar 36 KB+ en cada poll.
- Alternativa más simple: incluir las últimas ~20 entradas en `snapshot()` y
  emitir `errorJournal` por SSE en `_refresh_runtime_panels` (consistente con el
  patrón de systemStats). **Recomendado** por reutilizar el canal existente.
- Frontend: tipo `ErrorEntry`, estado en `useJarvis`, panel "Error Journal".
- Seguridad: solo lectura, ruta fija dentro de `data/`, sin parámetros de path.

### Fase D3 — Navegación HUD ↔ Dashboard

- Toggle para alternar entre el HUD (orb) y el Dashboard. Opciones:
  - Pestaña/segmented control en la TopBar, o
  - Reutilizar el `dot`/paginador inferior que ya aparece en el diseño.
- Persistir la vista elegida en `localStorage`.
- Verificación: cambiar de vista no rompe la sesión ni el SSE.

**Esfuerzo total F1:** medio-bajo. Mayormente frontend + un endpoint trivial.
**Riesgo:** bajo (todo read-only).

---

## Feature 2 — Autopilot con confirmaciones y escritura controlada

**Objetivo:** que Jarvis ejecute secuencias de acciones de escritura de forma
semi-autónoma, con confirmación humana y límites estrictos.

**Base existente:** `actions/executor.py` (`SafeActionExecutor`: `run_structured`,
`_risk_for`, `_hard_block_reason`, guards de path), `security/approvals.py`
(`ApprovalBroker.request()` HITL + `AutoApprovalBroker` para tests), y el patrón
`file_organizer` (plan → preview → apply con HITL) como plantilla.

**Lo que falta:** la capa de orquestación con políticas. Por su peso de seguridad,
**primero diseño, luego código.**

### Fase A0 — Documento de diseño (sin código) — BLOQUEANTE

Definir y cerrar antes de implementar:

- **Política de confirmación:** ¿plan completo aprobado una vez (batch) vs paso a
  paso por riesgo? Propuesta: batch para el plan + re-confirmación individual de
  cada acción de riesgo `write`/`high`.
- **Allowlist de acciones:** qué operaciones puede encadenar Autopilot (subconjunto
  de `run_structured`), explícitamente NO `run_powershell` libre.
- **Scope:** roots permitidos (reutilizar whitelist de `file_organizer`), máximo de
  pasos por plan, stop-on-error.
- **Dry-run obligatorio:** todo plan se simula y se muestra antes de cualquier
  escritura real.
- **Rollback / journal:** cada acción aplicada se registra (qué, antes/después si
  aplica) en un audit trail que alimenta el Dashboard (F1).
- **Kill-switch:** `Ctrl+Alt+Q` existente debe abortar un plan en curso de forma
  limpia (no dejar a medias una secuencia).
- **Modo desactivado por defecto:** flag `JARVIS_AUTOPILOT=false`; sin él, no se
  expone la tool.

### Fase A1 — Planner (genera el plan, no ejecuta)

- Tool `autopilot(goal)` que produce un plan estructurado (lista de acciones del
  allowlist) y lo persiste en `data/autopilot/plans/` (espejo de `file_organizer`).
- Sin ejecución: solo plan + validación contra políticas (scope, allowlist, máx
  pasos). Devuelve el plan para confirmación.
- Verificación: tests de que planes fuera de política se rechazan.

### Fase A2 — Dry-run + preview

- Simular cada paso (sin escribir) y emitir el preview al Dashboard/Approval UI.
- Reutilizar `ApprovalModal` para la confirmación del plan completo.

### Fase A3 — Ejecutor con gating por paso

- Ejecutar el plan aprobado paso a paso vía `SafeActionExecutor`, con
  re-aprobación HITL en acciones `write`/`high`, stop-on-error y registro de cada
  paso en el audit trail.
- Integrar kill-switch para abortar a mitad de plan.
- Verificación: test con `AutoApprovalBroker` (camino feliz) + test de abort.

### Fase A4 — Exposición controlada + observabilidad

- Activar la tool solo con `JARVIS_AUTOPILOT=true`.
- El feed de Autopilot (plan, dry-run, cada paso, aprobaciones) se ve en el
  Dashboard de F1 → cierre del bucle observabilidad ↔ acción.

**Esfuerzo total F2:** alto. **Riesgo:** alto (escritura autónoma) → mitigado por
diseño previo, dry-run, allowlist, HITL y kill-switch.

---

## Dependencias y secuencia

| Paso | Depende de | Riesgo | Entrega |
|---|---|---|---|
| D1 Componentes huérfanos | — | bajo | Vista con tools/budget en vivo |
| D2 Error journal | D1 | bajo | Panel de errores |
| D3 Navegación | D1 | bajo | Toggle HUD↔Dashboard |
| A0 Diseño | D1–D3 (observabilidad lista) | — | Doc de políticas cerrado |
| A1 Planner | A0 | medio | Plan validado, sin ejecutar |
| A2 Dry-run/preview | A1 | medio | Preview + confirmación |
| A3 Ejecutor | A2 | alto | Ejecución con gating + abort |
| A4 Exposición | A3 + D1–D3 | alto | Autopilot activable + auditado |

**Hito 1 (v1.04):** Dashboard completo (D1–D3). Bajo riesgo, alto valor de debug.
**Hito 2 (v1.05):** Autopilot tras A0 cerrado contigo. No empezar A1 sin A0.
