import pytest

from agent_runtime.models import (
    ContractError,
    MissionStatus,
    PlanContract,
    ensure_transition,
    make_mission,
    make_mission_step,
)


def _plan(**overrides):
    raw = {
        "version": 1,
        "goal": "Evaluar el estado técnico de JARVIS",
        "assumptions": ["Root local"],
        "steps": [{
            "step_id": "s1",
            "operation": "git_status",
            "arguments": {},
            "purpose": "Revisar cambios",
            "expected_evidence": "Estado Git",
        }],
        "report_questions": ["¿Qué sigue?"],
    }
    raw.update(overrides)
    return raw


def test_plan_contract_is_closed_and_requires_unique_steps():
    plan = PlanContract.from_dict(_plan())
    assert plan.steps[0].operation == "git_status"

    with pytest.raises(ContractError):
        PlanContract.from_dict({**_plan(), "surprise": True})

    duplicated = _plan(steps=_plan()["steps"] * 2)
    with pytest.raises(ContractError, match="duplicado"):
        PlanContract.from_dict(duplicated)


def test_plan_step_limit_has_stable_error_code():
    with pytest.raises(ContractError) as exc:
        PlanContract.from_dict(_plan(steps=_plan()["steps"] * 9), max_steps=8)
    assert exc.value.code == "STEP_LIMIT"


def test_mission_limits_and_idempotency_are_stable(tmp_path):
    mission = make_mission(
        goal="Evaluar el estado del proyecto JARVIS",
        root_path=str(tmp_path),
        max_steps=4,
        deadline_seconds=120,
    )
    plan = PlanContract.from_dict(_plan())
    a = make_mission_step(mission.id, 1, plan.steps[0])
    b = make_mission_step(mission.id, 1, plan.steps[0])
    assert a.id != b.id
    assert a.idempotency_key == b.idempotency_key


def test_state_machine_rejects_invalid_transition():
    ensure_transition(MissionStatus.QUEUED, MissionStatus.PLANNING)
    with pytest.raises(ContractError) as exc:
        ensure_transition(MissionStatus.QUEUED, MissionStatus.COMPLETED)
    assert exc.value.code == "INVALID_TRANSITION"


def test_make_mission_autonomy_is_always_l1(tmp_path):
    mission = make_mission(
        goal="Evaluar el estado técnico de JARVIS",
        root_path=str(tmp_path),
        max_steps=3,
        deadline_seconds=60,
    )
    assert mission.autonomy_level == "L1"
