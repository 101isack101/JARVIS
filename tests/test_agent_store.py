import sqlite3

import pytest

from agent_runtime.models import (
    ArtifactMeta,
    MissionStatus,
    PlanContract,
    StepStatus,
    make_mission,
    new_id,
    utc_now,
)
from agent_runtime.store import MissionStore


def _mission(tmp_path):
    return make_mission(
        goal="Evaluar el estado técnico de JARVIS",
        root_path=str(tmp_path),
        max_steps=4,
        deadline_seconds=120,
    )


def _plan():
    return PlanContract.from_dict({
        "version": 1,
        "goal": "Evaluar el estado técnico de JARVIS",
        "assumptions": [],
        "steps": [{
            "step_id": "s1",
            "operation": "git_status",
            "arguments": {},
            "purpose": "Revisar cambios",
            "expected_evidence": "Estado Git",
        }],
        "report_questions": [],
    })


def test_store_persists_mission_plan_and_events(tmp_path):
    db = tmp_path / "agent.db"
    store = MissionStore(db)
    mission = _mission(tmp_path)
    created = store.create_mission(mission)
    assert created.event_type == "mission.created"

    store.transition(mission.id, MissionStatus.PLANNING, event_type="mission.planning")
    steps = store.save_plan(mission.id, _plan())
    store.transition(mission.id, MissionStatus.READY, event_type="mission.ready")
    store.start_step(steps[0].id)
    store.finish_step(
        steps[0].id,
        StepStatus.COMPLETED,
        artifact_id="art_one",
        verification_status="passed",
    )

    reopened = MissionStore(db)
    assert reopened.get_mission(mission.id).status == MissionStatus.READY
    assert reopened.get_steps(mission.id)[0].attempt_count == 1
    assert [event.event_type for event in reopened.events(mission.id)][0] == "mission.created"


def test_transition_and_event_are_atomic(tmp_path):
    store = MissionStore(tmp_path / "agent.db")
    mission = _mission(tmp_path)
    store.create_mission(mission)

    with pytest.raises(Exception):
        store.transition(mission.id, MissionStatus.COMPLETED, event_type="bad")

    assert store.get_mission(mission.id).status == MissionStatus.QUEUED
    assert [e.event_type for e in store.events(mission.id)] == ["mission.created"]


def test_cancel_is_idempotent_and_terminal_is_unchanged(tmp_path):
    store = MissionStore(tmp_path / "agent.db")
    mission = _mission(tmp_path)
    store.create_mission(mission)
    assert store.request_cancel(mission.id).status == MissionStatus.CANCEL_REQUESTED
    assert store.request_cancel(mission.id).status == MissionStatus.CANCEL_REQUESTED
    store.transition(mission.id, MissionStatus.CANCELLED, event_type="mission.cancelled")
    assert store.request_cancel(mission.id).status == MissionStatus.CANCELLED


def test_artifact_fk_and_metadata(tmp_path):
    store = MissionStore(tmp_path / "agent.db")
    mission = _mission(tmp_path)
    store.create_mission(mission)
    artifact = ArtifactMeta(
        id=new_id("art"), mission_id=mission.id, step_id=None, kind="tool_result",
        relative_path="x.json", sha256="a" * 64, size_bytes=10, redacted=True,
        created_at=utc_now(),
    )
    store.upsert_artifact(artifact)
    assert store.get_artifact(artifact.id) == artifact

    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_artifact(ArtifactMeta(
            id=new_id("art"), mission_id="missing", step_id=None, kind="x",
            relative_path="x", sha256="b" * 64, size_bytes=1, redacted=True,
            created_at=utc_now(),
        ))
