"""Closed contracts and state machines for durable JARVIS missions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class ContractError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_CONTRACT") -> None:
        super().__init__(message)
        self.code = code


class MissionStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    CANCEL_REQUESTED = "cancel_requested"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    FAILED = "failed"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    FAILED = "failed"


TERMINAL_MISSION_STATUSES = {
    MissionStatus.COMPLETED,
    MissionStatus.CANCELLED,
    MissionStatus.BLOCKED,
    MissionStatus.FAILED,
}

MISSION_TRANSITIONS: dict[MissionStatus, set[MissionStatus]] = {
    MissionStatus.QUEUED: {
        MissionStatus.PLANNING,
        MissionStatus.CANCEL_REQUESTED,
        MissionStatus.CANCELLED,
        MissionStatus.BLOCKED,
        MissionStatus.FAILED,
    },
    MissionStatus.PLANNING: {
        MissionStatus.READY,
        MissionStatus.CANCEL_REQUESTED,
        MissionStatus.RECOVERING,
        MissionStatus.BLOCKED,
        MissionStatus.FAILED,
    },
    MissionStatus.READY: {
        MissionStatus.RUNNING,
        MissionStatus.CANCEL_REQUESTED,
        MissionStatus.RECOVERING,
        MissionStatus.BLOCKED,
        MissionStatus.FAILED,
    },
    MissionStatus.RUNNING: {
        MissionStatus.VERIFYING,
        MissionStatus.CANCEL_REQUESTED,
        MissionStatus.RECOVERING,
        MissionStatus.BLOCKED,
        MissionStatus.FAILED,
    },
    MissionStatus.VERIFYING: {
        MissionStatus.COMPLETED,
        MissionStatus.CANCEL_REQUESTED,
        MissionStatus.RECOVERING,
        MissionStatus.BLOCKED,
        MissionStatus.FAILED,
    },
    MissionStatus.CANCEL_REQUESTED: {MissionStatus.CANCELLED, MissionStatus.FAILED},
    MissionStatus.RECOVERING: {
        MissionStatus.PLANNING,
        MissionStatus.READY,
        MissionStatus.RUNNING,
        MissionStatus.VERIFYING,
        MissionStatus.CANCEL_REQUESTED,
        MissionStatus.CANCELLED,
        MissionStatus.BLOCKED,
        MissionStatus.FAILED,
    },
    MissionStatus.COMPLETED: set(),
    MissionStatus.CANCELLED: set(),
    MissionStatus.BLOCKED: set(),
    MissionStatus.FAILED: set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ensure_transition(current: MissionStatus | str, target: MissionStatus | str) -> None:
    current = MissionStatus(current)
    target = MissionStatus(target)
    if target == current:
        return
    if target not in MISSION_TRANSITIONS[current]:
        raise ContractError(
            f"transicion de mision invalida: {current.value} -> {target.value}",
            code="INVALID_TRANSITION",
        )


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    operation: str
    arguments: dict[str, Any]
    purpose: str
    expected_evidence: str

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PlanStep":
        allowed = {"step_id", "operation", "arguments", "purpose", "expected_evidence"}
        _reject_extra(raw, allowed, "plan step")
        required = allowed
        missing = required - set(raw)
        if missing:
            raise ContractError(f"plan step sin campos: {sorted(missing)}")
        step_id = _bounded_text(raw["step_id"], "step_id", 1, 64)
        operation = _bounded_text(raw["operation"], "operation", 1, 64)
        purpose = _bounded_text(raw["purpose"], "purpose", 1, 500)
        evidence = _bounded_text(raw["expected_evidence"], "expected_evidence", 1, 500)
        arguments = raw["arguments"]
        if not isinstance(arguments, dict):
            raise ContractError("arguments debe ser object")
        return cls(step_id, operation, dict(arguments), purpose, evidence)


@dataclass(frozen=True)
class PlanContract:
    version: int
    goal: str
    assumptions: list[str]
    steps: list[PlanStep]
    report_questions: list[str]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, max_steps: int = 8) -> "PlanContract":
        allowed = {"version", "goal", "assumptions", "steps", "report_questions"}
        _reject_extra(raw, allowed, "plan")
        missing = allowed - set(raw)
        if missing:
            raise ContractError(f"plan sin campos: {sorted(missing)}")
        if raw["version"] != 1:
            raise ContractError("version de plan no soportada", code="UNSUPPORTED_PLAN_VERSION")
        goal = validate_goal(raw["goal"])
        assumptions = _text_list(raw["assumptions"], "assumptions", max_items=20, max_chars=500)
        questions = _text_list(raw["report_questions"], "report_questions", max_items=20, max_chars=500)
        if not isinstance(raw["steps"], list):
            raise ContractError("steps debe ser array")
        if not 1 <= len(raw["steps"]) <= max_steps:
            raise ContractError(f"steps debe contener 1..{max_steps}", code="STEP_LIMIT")
        steps = [PlanStep.from_dict(item) if isinstance(item, Mapping) else _bad_step() for item in raw["steps"]]
        ids = [step.step_id for step in steps]
        if len(ids) != len(set(ids)):
            raise ContractError("step_id duplicado")
        return cls(1, goal, assumptions, steps, questions)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Mission:
    id: str
    goal: str
    status: MissionStatus
    root_path: str
    max_steps: int
    deadline_seconds: int
    autonomy_level: str = "L1"
    planner_model: str | None = None
    cancel_requested: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    final_summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    schema_version: int = 1


@dataclass
class MissionStep:
    id: str
    mission_id: str
    ordinal: int
    operation: str
    arguments: dict[str, Any]
    purpose: str
    expected_evidence: str
    status: StepStatus
    idempotency_key: str
    attempt_count: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    artifact_id: str | None = None
    verification_status: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class MissionEvent:
    id: int | None
    mission_id: str
    step_id: str | None
    event_type: str
    level: str
    payload: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class ArtifactMeta:
    id: str
    mission_id: str
    step_id: str | None
    kind: str
    relative_path: str
    sha256: str
    size_bytes: int
    redacted: bool
    created_at: str


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    risk: str
    reason_code: str
    normalized_arguments: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class StepResult:
    ok: bool
    step_id: str
    operation: str
    started_at: str
    ended_at: str
    summary: str
    artifact_id: str | None
    verification: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None


def validate_goal(value: Any) -> str:
    return _bounded_text(value, "goal", 10, 2000)


def validate_limits(max_steps: Any, deadline_seconds: Any, *, configured_max_steps: int = 8) -> tuple[int, int]:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int):
        raise ContractError("max_steps debe ser integer")
    if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, int):
        raise ContractError("deadline_seconds debe ser integer")
    if not 1 <= max_steps <= configured_max_steps:
        raise ContractError(f"max_steps debe estar entre 1 y {configured_max_steps}", code="STEP_LIMIT")
    if not 30 <= deadline_seconds <= 600:
        raise ContractError("deadline_seconds debe estar entre 30 y 600")
    return max_steps, deadline_seconds


def make_mission(*, goal: str, root_path: str, max_steps: int, deadline_seconds: int, planner_model: str | None = None, autonomy_level: str = "L1") -> Mission:
    goal = validate_goal(goal)
    max_steps, deadline_seconds = validate_limits(max_steps, deadline_seconds)
    level = "L2" if str(autonomy_level or "").strip().upper() == "L2" else "L1"
    return Mission(
        id=new_id("msn"),
        goal=goal,
        status=MissionStatus.QUEUED,
        root_path=root_path,
        max_steps=max_steps,
        deadline_seconds=deadline_seconds,
        planner_model=planner_model,
        autonomy_level=level,
    )


def make_mission_step(mission_id: str, ordinal: int, plan_step: PlanStep) -> MissionStep:
    canonical = canonical_json({
        "mission_id": mission_id,
        "ordinal": ordinal,
        "step": asdict(plan_step),
    })
    return MissionStep(
        id=new_id("stp"),
        mission_id=mission_id,
        ordinal=ordinal,
        operation=plan_step.operation,
        arguments=dict(plan_step.arguments),
        purpose=plan_step.purpose,
        expected_evidence=plan_step.expected_evidence,
        status=StepStatus.PENDING,
        idempotency_key=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _reject_extra(raw: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = set(raw) - allowed
    if extra:
        raise ContractError(f"{label} contiene campos desconocidos: {sorted(extra)}")


def _bounded_text(value: Any, label: str, min_chars: int, max_chars: int) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} debe ser string")
    text = value.strip()
    if not min_chars <= len(text) <= max_chars:
        raise ContractError(f"{label} debe tener {min_chars}..{max_chars} caracteres")
    return text


def _text_list(value: Any, label: str, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ContractError(f"{label} debe ser array con máximo {max_items} elementos")
    return [_bounded_text(item, label, 1, max_chars) for item in value]


def _bad_step() -> PlanStep:
    raise ContractError("cada step debe ser object")
