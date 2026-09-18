"""Background mission runtime with cancellation, verification and recovery."""

from __future__ import annotations

import os
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from telemetry.logger import get_logger

from .models import (
    ContractError,
    Mission,
    MissionEvent,
    MissionStatus,
    StepStatus,
    TERMINAL_MISSION_STATUSES,
    make_mission,
)
from .planner import MissionPlanner, PlannerConfig, PlannerError
from .store import MissionNotFound, MissionStore
from .tools import MissionPolicy, ReadOnlyToolAdapter, READ_ONLY_OPERATIONS, WRITE_OPERATIONS, result_summary
from .verifier import ArtifactStore, MissionVerifier, ReportBuilder, VerificationError


log = get_logger("agent_runtime")
EventSink = Callable[[dict[str, Any]], None]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(low, min(value, high))


@dataclass(frozen=True)
class AgentRuntimeConfig:
    enabled: bool = False
    max_steps: int = 8
    deadline_seconds: int = 180
    max_active: int = 1
    artifact_max_chars: int = 20000
    planner_max_tokens: int = 1800
    autonomy_level: str = "L1"

    @classmethod
    def from_env(cls) -> "AgentRuntimeConfig":
        raw_level = (os.environ.get("JARVIS_AGENT_AUTONOMY_LEVEL") or "L1").strip().upper()
        return cls(
            enabled=_env_bool("JARVIS_AGENT_RUNTIME_ENABLED", False),
            max_steps=_env_int("JARVIS_AGENT_MAX_STEPS", 8, 1, 8),
            deadline_seconds=_env_int("JARVIS_AGENT_DEADLINE_S", 180, 30, 600),
            max_active=_env_int("JARVIS_AGENT_MAX_ACTIVE", 1, 1, 1),
            artifact_max_chars=_env_int("JARVIS_AGENT_ARTIFACT_MAX_CHARS", 20000, 1000, 100000),
            planner_max_tokens=_env_int("JARVIS_AGENT_PLANNER_MAX_TOKENS", 1800, 256, 4096),
            autonomy_level="L2" if raw_level == "L2" else "L1",
        )


@dataclass(frozen=True)
class _MissionTelemetryBaseline:
    costs: dict[str, float]
    tokens: dict[str, int]


class AgentRuntimeService:
    def __init__(
        self,
        *,
        root: Path | str,
        store: MissionStore,
        planner: MissionPlanner,
        tools: ReadOnlyToolAdapter,
        verifier: MissionVerifier,
        artifacts: ArtifactStore,
        report_builder: ReportBuilder,
        config: AgentRuntimeConfig | None = None,
        event_sink: EventSink | None = None,
        budget_check: Callable[[], bool] | None = None,
        tracker: Any | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.store = store
        self.planner = planner
        self.tools = tools
        self.verifier = verifier
        self.artifacts = artifacts
        self.report_builder = report_builder
        self.config = config or AgentRuntimeConfig()
        self.event_sink = event_sink
        self.budget_check = budget_check
        self.tracker = tracker
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._queued_ids: set[str] = set()
        self._queue_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active_id: str | None = None
        self._telemetry_baselines: dict[str, _MissionTelemetryBaseline] = {}
        self._final_telemetry: dict[str, dict[str, Any]] = {}
        self._telemetry_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def active_mission_id(self) -> str | None:
        return self._active_id

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._queue = queue.Queue()
        with self._queue_lock:
            self._queued_ids.clear()
        self._recover()
        self._thread = threading.Thread(target=self._worker, name="JarvisAgentRuntime", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 3.0) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=max(0.0, timeout_s))
            if not self._thread.is_alive():
                self._thread = None

    def create(
        self,
        goal: str,
        *,
        max_steps: int | None = None,
        deadline_seconds: int | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": False, "error_code": "AGENT_RUNTIME_DISABLED", "error": "runtime deshabilitado"}
        try:
            mission = make_mission(
                goal=goal,
                root_path=str(self.root),
                max_steps=max_steps if max_steps is not None else self.config.max_steps,
                deadline_seconds=(
                    deadline_seconds if deadline_seconds is not None else self.config.deadline_seconds
                ),
                planner_model=self.planner.model,
                autonomy_level=self.config.autonomy_level,
            )
        except ContractError as exc:
            return {"ok": False, "error_code": exc.code, "error": str(exc)}
        event = self.store.create_mission(mission)
        self._emit(event)
        self._enqueue(mission.id)
        return {
            "ok": True,
            "mission_id": mission.id,
            "status": mission.status.value,
            "autonomy_level": mission.autonomy_level,
            "message": "Misión creada; puedes seguir conversando.",
        }

    def cancel(self, mission_id: str) -> dict[str, Any]:
        try:
            before = self.store.get_mission(mission_id)
            mission = self.store.request_cancel(mission_id)
        except MissionNotFound:
            return {"ok": False, "error_code": "MISSION_NOT_FOUND", "error": mission_id}
        if before.status != mission.status:
            self._emit_latest(mission_id)
        self._enqueue(mission_id)
        return {"ok": True, "mission_id": mission.id, "status": mission.status.value}

    def status(self, mission_id: str) -> dict[str, Any]:
        try:
            mission = self.store.get_mission(mission_id)
        except MissionNotFound:
            return {"ok": False, "error_code": "MISSION_NOT_FOUND", "error": mission_id}
        return {
            "ok": True,
            "mission": _mission_summary(
                self.store,
                mission,
                telemetry=self._telemetry_for(mission),
                include_summary=True,
            ),
            "steps": [_step_dict(step) for step in self.store.get_steps(mission_id)],
            "events": [_event_dict(event) for event in self.store.events(mission_id, limit=50)],
        }

    def list(self, limit: int = 20) -> dict[str, Any]:
        missions = self.store.list_missions(limit=limit)
        return {
            "ok": True,
            "missions": [
                _mission_summary(self.store, item, telemetry=self._telemetry_for(item))
                for item in missions
            ],
        }

    def wait_for_terminal(self, mission_id: str, timeout_s: float = 5.0) -> Mission:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            mission = self.store.get_mission(mission_id)
            if mission.status in TERMINAL_MISSION_STATUSES:
                return mission
            time.sleep(0.01)
        return self.store.get_mission(mission_id)

    def _enqueue(self, mission_id: str) -> None:
        with self._queue_lock:
            if mission_id in self._queued_ids or mission_id == self._active_id:
                return
            self._queued_ids.add(mission_id)
            self._queue.put(mission_id)

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                mission_id = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if mission_id is None:
                break
            with self._queue_lock:
                self._queued_ids.discard(mission_id)
                self._active_id = mission_id
            try:
                self._process(mission_id)
            except Exception as exc:  # defensive boundary: a mission cannot kill the worker
                log.exception("mision {} fallo fuera del pipeline: {}", mission_id, exc)
                self._fail(mission_id, "RUNTIME_ERROR", f"{type(exc).__name__}: {exc}")
            finally:
                with self._queue_lock:
                    self._active_id = None

    def _process(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        # Start the accounting window only when this worker actually owns the
        # mission, so FIFO queue time never absorbs another mission's usage.
        self._ensure_telemetry_baseline(mission_id)
        if mission.status in TERMINAL_MISSION_STATUSES:
            return
        if mission.cancel_requested or mission.status == MissionStatus.CANCEL_REQUESTED:
            self._cancel(mission_id)
            return

        steps = self.store.get_steps(mission_id)
        if not steps:
            if mission.status != MissionStatus.PLANNING:
                event = self.store.transition(
                    mission_id, MissionStatus.PLANNING, event_type="mission.planning",
                )
                self._emit(event)
            if self._budget_exceeded():
                self._block(mission_id, "BUDGET_EXCEEDED", "budget no permite planificar")
                return
            mission = self.store.get_mission(mission_id)
            try:
                plan = self.planner.plan(
                    goal=mission.goal,
                    max_steps=mission.max_steps,
                    deadline_seconds=mission.deadline_seconds,
                )
            except PlannerError as exc:
                self._block(mission_id, exc.code, str(exc))
                return
            if plan.goal.strip() != mission.goal.strip():
                self._block(mission_id, "GOAL_MISMATCH", "el plan intento cambiar la meta autorizada")
                return
            if self._cancel_if_requested(mission_id):
                return
            for step in plan.steps:
                decision = self.tools.policy.evaluate(step.operation, step.arguments)
                if not decision.allowed:
                    self._block(mission_id, decision.reason_code, decision.detail)
                    return
            self.store.save_plan(mission_id, plan)
            self._emit_latest(mission_id)
            event = self.store.transition(mission_id, MissionStatus.READY, event_type="mission.ready")
            self._emit(event)
            steps = self.store.get_steps(mission_id)

        mission = self.store.get_mission(mission_id)
        if mission.status == MissionStatus.RECOVERING:
            target = MissionStatus.VERIFYING if all(s.status == StepStatus.COMPLETED for s in steps) else MissionStatus.RUNNING
            event = self.store.transition(mission_id, target, event_type="mission.recovered")
            self._emit(event)
        elif mission.status == MissionStatus.READY:
            event = self.store.transition(mission_id, MissionStatus.RUNNING, event_type="mission.started")
            self._emit(event)

        mission = self.store.get_mission(mission_id)
        if mission.status == MissionStatus.RUNNING:
            for step in self.store.get_steps(mission_id):
                if step.status == StepStatus.COMPLETED:
                    continue
                if self._cancel_if_requested(mission_id):
                    return
                if self._deadline_exceeded(self.store.get_mission(mission_id)):
                    self._block(mission_id, "DEADLINE_EXCEEDED", "deadline de mision agotado")
                    return
                decision = self.tools.policy.evaluate_step(step)
                if not decision.allowed:
                    self._block(mission_id, decision.reason_code, decision.detail)
                    return
                start_event = self.store.start_step(step.id)
                self._emit(start_event)
                result = self.tools.execute(step)
                if self._cancel_if_requested(mission_id):
                    return
                try:
                    verification = self.verifier.verify_step(step, result)
                    artifact = self.artifacts.write_result(mission, step, result)
                    self.store.upsert_artifact(artifact)
                    done = self.store.finish_step(
                        step.id,
                        StepStatus.COMPLETED,
                        artifact_id=artifact.id,
                        verification_status="passed",
                        payload={"summary": verification["summary"]},
                    )
                    self._emit(done)
                except VerificationError as exc:
                    failed = self.store.finish_step(
                        step.id,
                        StepStatus.FAILED,
                        verification_status="failed",
                        error_code=exc.code,
                        error_message=str(exc),
                    )
                    self._emit(failed)
                    self._fail(mission_id, exc.code, str(exc))
                    return
            event = self.store.transition(
                mission_id, MissionStatus.VERIFYING, event_type="mission.verifying",
            )
            self._emit(event)

        if self._cancel_if_requested(mission_id):
            return
        mission = self.store.get_mission(mission_id)
        if mission.status != MissionStatus.VERIFYING:
            return
        steps = self.store.get_steps(mission_id)
        artifacts_by_id = {
            step.artifact_id: artifact
            for step in steps
            if step.artifact_id and (artifact := self.store.get_artifact(step.artifact_id)) is not None
        }
        try:
            self.verifier.verify_mission(steps, artifacts_by_id)
            if self._deadline_exceeded(mission):
                raise VerificationError("deadline de mision agotado", code="DEADLINE_EXCEEDED")
            if self._budget_exceeded():
                raise VerificationError("budget no permite generar informe", code="BUDGET_EXCEEDED")
            summary = self.report_builder.build(mission, steps, artifacts_by_id)
        except VerificationError as exc:
            self._block(mission_id, exc.code, str(exc))
            return
        if self._cancel_if_requested(mission_id):
            return
        mission = self.store.get_mission(mission_id)
        if self._deadline_exceeded(mission):
            self._block(mission_id, "DEADLINE_EXCEEDED", "deadline de mision agotado durante informe")
            return
        if self._budget_exceeded():
            self._block(mission_id, "BUDGET_EXCEEDED", "budget agotado durante informe")
            return
        event = self.store.transition(
            mission_id,
            MissionStatus.COMPLETED,
            event_type="mission.completed",
            payload={"step_count": len(steps)},
            final_summary=summary,
        )
        self._emit(event)

    def _recover(self) -> None:
        for mission in self.store.nonterminal_missions():
            if mission.status == MissionStatus.QUEUED:
                self._enqueue(mission.id)
                continue
            if mission.cancel_requested or mission.status == MissionStatus.CANCEL_REQUESTED:
                self._cancel(mission.id)
                continue
            if mission.status != MissionStatus.RECOVERING:
                event = self.store.transition(
                    mission.id,
                    MissionStatus.RECOVERING,
                    event_type="mission.recovered",
                    level="warn",
                    payload={"phase": "startup"},
                )
                self._emit(event)
            for step in self.store.get_steps(mission.id):
                if step.status != StepStatus.RUNNING:
                    continue
                artifact = (
                    self.store.get_artifact(step.artifact_id)
                    if step.artifact_id
                    else self.store.get_artifact_for_step(step.id)
                )
                if artifact and self.artifacts.validate(artifact):
                    try:
                        result = self.artifacts.read(artifact).get("result") or {}
                        verification = self.verifier.verify_step(step, result)
                        event = self.store.finish_step(
                            step.id,
                            StepStatus.COMPLETED,
                            artifact_id=artifact.id,
                            verification_status="passed",
                            payload={"summary": verification["summary"], "recovered": True},
                        )
                        self._emit(event)
                        continue
                    except VerificationError:
                        pass
                self.store.reset_step_for_recovery(step.id)
                event = self.store.append_event(
                    mission.id,
                    "step.requeued",
                    step_id=step.id,
                    level="warn",
                    payload={"reason": "missing_or_invalid_artifact"},
                )
                self._emit(event)
            refreshed = self.store.get_steps(mission.id)
            if any(step.status == StepStatus.FAILED for step in refreshed):
                self._fail(mission.id, "RECOVERED_FAILED_STEP", "se recupero un step fallido")
                continue
            if any(step.status == StepStatus.BLOCKED for step in refreshed):
                self._block(mission.id, "RECOVERED_BLOCKED_STEP", "se recupero un step bloqueado")
                continue
            self._enqueue(mission.id)

    def _cancel_if_requested(self, mission_id: str) -> bool:
        mission = self.store.get_mission(mission_id)
        if self._stop.is_set():
            return True
        if mission.cancel_requested or mission.status == MissionStatus.CANCEL_REQUESTED:
            self._cancel(mission_id)
            return True
        return False

    def _cancel(self, mission_id: str) -> None:
        mission = self.store.get_mission(mission_id)
        if mission.status in TERMINAL_MISSION_STATUSES:
            return
        if mission.status != MissionStatus.CANCEL_REQUESTED:
            mission = self.store.request_cancel(mission_id)
            self._emit_latest(mission_id)
        for step in self.store.get_steps(mission_id):
            if step.status == StepStatus.RUNNING:
                event = self.store.finish_step(step.id, StepStatus.CANCELLED)
                self._emit(event)
        event = self.store.transition(
            mission_id, MissionStatus.CANCELLED, event_type="mission.cancelled", level="warn",
        )
        self._emit(event)

    def _block(self, mission_id: str, code: str, message: str) -> None:
        self._terminal_error(mission_id, MissionStatus.BLOCKED, "mission.blocked", code, message)

    def _fail(self, mission_id: str, code: str, message: str) -> None:
        self._terminal_error(mission_id, MissionStatus.FAILED, "mission.failed", code, message)

    def _terminal_error(
        self,
        mission_id: str,
        status: MissionStatus,
        event_type: str,
        code: str,
        message: str,
    ) -> None:
        try:
            mission = self.store.get_mission(mission_id)
            if mission.status in TERMINAL_MISSION_STATUSES:
                return
            event = self.store.transition(
                mission_id,
                status,
                event_type=event_type,
                level="error",
                error_code=code,
                error_message=message[:1000],
            )
            self._emit(event)
        except Exception as exc:
            log.error("no se pudo terminalizar mision {}: {}", mission_id, exc)

    def _budget_exceeded(self) -> bool:
        return bool(self.budget_check and not self.budget_check())

    @staticmethod
    def _deadline_exceeded(mission: Mission) -> bool:
        if not mission.started_at:
            return False
        try:
            started = datetime.fromisoformat(mission.started_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        return (datetime.now(timezone.utc) - started).total_seconds() > mission.deadline_seconds

    def _emit_latest(self, mission_id: str) -> None:
        events = self.store.events(mission_id, limit=1)
        if events:
            self._emit(events[-1])

    def _emit(self, event: MissionEvent) -> None:
        if not self.event_sink:
            return
        try:
            payload = _event_dict(event)
            mission = self.store.get_mission(event.mission_id)
            payload["mission"] = _mission_summary(
                self.store,
                mission,
                telemetry=self._telemetry_for(mission),
            )
            self.event_sink(payload)
        except Exception as exc:
            log.warning("event_sink fallo: {}", exc)

    def _ensure_telemetry_baseline(self, mission_id: str) -> None:
        if self.tracker is None:
            return
        with self._telemetry_lock:
            if mission_id in self._telemetry_baselines:
                return
        try:
            baseline = _MissionTelemetryBaseline(
                costs=dict(self.tracker.cost_by_provider()),
                tokens=dict(self.tracker.tokens_by_provider()),
            )
            with self._telemetry_lock:
                self._telemetry_baselines.setdefault(mission_id, baseline)
        except Exception as exc:
            log.warning("telemetria de mision no disponible: {}", exc)

    def _telemetry_for(self, mission: Mission) -> dict[str, Any]:
        duration_ms = _duration_ms(mission.started_at, mission.finished_at)
        with self._telemetry_lock:
            frozen = self._final_telemetry.get(mission.id)
            baseline = self._telemetry_baselines.get(mission.id)
        if frozen is not None:
            return frozen
        if baseline is None or self.tracker is None:
            return {
                "duration_ms": duration_ms,
                "cost_usd": None,
                "tokens": None,
                "providers": {},
                "scope": "duration_only",
            }
        try:
            costs = dict(self.tracker.cost_by_provider())
            tokens = dict(self.tracker.tokens_by_provider())
        except Exception:
            return {
                "duration_ms": duration_ms,
                "cost_usd": None,
                "tokens": None,
                "providers": {},
                "scope": "duration_only",
            }
        names = ("gemini", "claude", "other")
        providers = {
            name: {
                "cost_usd": round(max(0.0, float(costs.get(name, 0.0)) - float(baseline.costs.get(name, 0.0))), 6),
                "tokens": max(0, int(tokens.get(name, 0)) - int(baseline.tokens.get(name, 0))),
            }
            for name in names
        }
        telemetry = {
            "duration_ms": duration_ms,
            "cost_usd": round(sum(item["cost_usd"] for item in providers.values()), 6),
            "tokens": sum(item["tokens"] for item in providers.values()),
            "providers": providers,
            "scope": "mission_window_delta",
        }
        if mission.status in TERMINAL_MISSION_STATUSES:
            with self._telemetry_lock:
                return self._final_telemetry.setdefault(mission.id, telemetry)
        return telemetry


def build_agent_runtime(
    *,
    root: Path,
    reasoner: Any,
    executor: Any,
    config: AgentRuntimeConfig,
    event_sink: EventSink | None = None,
    budget_check: Callable[[], bool] | None = None,
    tracker: Any | None = None,
    approval_broker: Any | None = None,
    writers: dict[str, Any] | None = None,
) -> AgentRuntimeService:
    store = MissionStore(root / "data" / "agent_runtime" / "agent_runtime.db")
    policy = MissionPolicy(root, autonomy_level=config.autonomy_level)
    tools = ReadOnlyToolAdapter(
        executor,
        policy=policy,
        max_result_chars=config.artifact_max_chars,
        approval_broker=approval_broker,
        writers=writers,
    )
    artifacts = ArtifactStore(
        root / "data" / "agent_runtime" / "artifacts",
        max_chars=config.artifact_max_chars,
    )
    verifier = MissionVerifier(policy=policy, artifacts=artifacts)
    operations = dict(READ_ONLY_OPERATIONS)
    if config.autonomy_level == "L2":
        operations.update(WRITE_OPERATIONS)
    planner = MissionPlanner(
        reasoner,
        config=PlannerConfig(max_output_tokens=config.planner_max_tokens),
        operations=operations,
    )
    reporter = ReportBuilder(reasoner, artifacts=artifacts, max_output_tokens=config.planner_max_tokens)
    return AgentRuntimeService(
        root=root,
        store=store,
        planner=planner,
        tools=tools,
        verifier=verifier,
        artifacts=artifacts,
        report_builder=reporter,
        config=config,
        event_sink=event_sink,
        budget_check=budget_check,
        tracker=tracker,
    )


def _step_dict(step) -> dict[str, Any]:
    data = asdict(step)
    data["status"] = step.status.value
    data["duration_ms"] = _duration_ms(step.started_at, step.finished_at)
    return data


def _event_dict(event: MissionEvent) -> dict[str, Any]:
    return asdict(event)


def _mission_summary(
    store: MissionStore,
    mission: Mission,
    *,
    telemetry: dict[str, Any] | None = None,
    include_summary: bool = False,
) -> dict[str, Any]:
    steps = store.get_steps(mission.id)
    completed = sum(step.status == StepStatus.COMPLETED for step in steps)
    summary_limit = 4000 if include_summary else 800
    final_summary = mission.final_summary or ""
    return {
        "id": mission.id,
        "goal": mission.goal,
        "status": mission.status.value,
        "autonomy_level": mission.autonomy_level,
        "created_at": mission.created_at,
        "updated_at": mission.updated_at,
        "started_at": mission.started_at,
        "finished_at": mission.finished_at,
        "duration_ms": _duration_ms(mission.started_at, mission.finished_at),
        "completed_steps": completed,
        "total_steps": len(steps),
        "error_code": mission.error_code,
        "final_summary": (
            final_summary[:summary_limit] + ("…" if len(final_summary) > summary_limit else "")
            if final_summary else None
        ),
        "telemetry": telemetry or {
            "duration_ms": _duration_ms(mission.started_at, mission.finished_at),
            "cost_usd": None,
            "tokens": None,
            "providers": {},
            "scope": "duration_only",
        },
    }


def _duration_ms(started_at: str | None, finished_at: str | None) -> int | None:
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(finished_at.replace("Z", "+00:00")) if finished_at else datetime.now(timezone.utc)
    except (ValueError, AttributeError):
        return None
    return max(0, int((ended - started).total_seconds() * 1000))
