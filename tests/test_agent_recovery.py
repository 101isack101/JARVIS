from types import SimpleNamespace

from agent_runtime.models import MissionStatus, PlanContract, make_mission
from agent_runtime.planner import MissionPlanner
from agent_runtime.runtime import AgentRuntimeConfig, AgentRuntimeService
from agent_runtime.store import MissionStore
from agent_runtime.tools import MissionPolicy, ReadOnlyToolAdapter
from agent_runtime.verifier import ArtifactStore, MissionVerifier, ReportBuilder


class ReportReasoner:
    configured = True
    model = "fake"

    def ask(self, *args, **kwargs):
        return SimpleNamespace(text="Recuperado [step:s1]")


class Executor:
    def __init__(self): self.calls = 0
    def run_structured(self, operation, **kwargs):
        self.calls += 1
        return {
            "ok": True, "allowed": True, "operation": operation,
            "path": "README.md", "content": "ok", "truncated": False,
        }


def test_recovery_requeues_running_step_without_artifact(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    store = MissionStore(tmp_path / "agent.db")
    mission = make_mission(
        goal="Evaluar el estado técnico de JARVIS",
        root_path=str(tmp_path), max_steps=4, deadline_seconds=120,
    )
    store.create_mission(mission)
    store.transition(mission.id, MissionStatus.PLANNING, event_type="mission.planning")
    plan = PlanContract.from_dict({
        "version": 1,
        "goal": mission.goal,
        "assumptions": [],
        "steps": [{
            "step_id": "s1", "operation": "read_file", "arguments": {"path": "README.md"},
            "purpose": "leer", "expected_evidence": "contenido",
        }],
        "report_questions": [],
    })
    step = store.save_plan(mission.id, plan)[0]
    store.transition(mission.id, MissionStatus.READY, event_type="mission.ready")
    store.transition(mission.id, MissionStatus.RUNNING, event_type="mission.started")
    store.start_step(step.id)

    reasoner = ReportReasoner()
    executor = Executor()
    policy = MissionPolicy(tmp_path)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = AgentRuntimeService(
        root=tmp_path, store=store, planner=MissionPlanner(reasoner),
        tools=ReadOnlyToolAdapter(executor, policy=policy),
        verifier=MissionVerifier(policy=policy, artifacts=artifacts), artifacts=artifacts,
        report_builder=ReportBuilder(reasoner, artifacts=artifacts),
        config=AgentRuntimeConfig(enabled=True),
    )
    service.start()
    recovered = service.wait_for_terminal(mission.id, timeout_s=2)
    service.stop()
    assert recovered.status == MissionStatus.COMPLETED
    assert executor.calls == 1
    assert any(event.event_type == "step.requeued" for event in store.events(mission.id))


def test_recovery_uses_confirmed_artifact_without_reexecuting(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    store = MissionStore(tmp_path / "agent.db")
    mission = make_mission(
        goal="Evaluar el estado técnico de JARVIS",
        root_path=str(tmp_path), max_steps=4, deadline_seconds=120,
    )
    store.create_mission(mission)
    store.transition(mission.id, MissionStatus.PLANNING, event_type="mission.planning")
    plan = PlanContract.from_dict({
        "version": 1, "goal": mission.goal, "assumptions": [],
        "steps": [{
            "step_id": "s1", "operation": "read_file", "arguments": {"path": "README.md"},
            "purpose": "leer", "expected_evidence": "contenido",
        }], "report_questions": [],
    })
    step = store.save_plan(mission.id, plan)[0]
    store.transition(mission.id, MissionStatus.READY, event_type="mission.ready")
    store.transition(mission.id, MissionStatus.RUNNING, event_type="mission.started")
    store.start_step(step.id)

    # Simula crash entre persistir artifact metadata y finish_step.
    artifacts = ArtifactStore(tmp_path / "artifacts")
    result = {
        "ok": True, "allowed": True, "operation": "read_file",
        "path": "README.md", "content": "ok", "truncated": False,
    }
    meta = artifacts.write_result(mission, step, result)
    store.upsert_artifact(meta)

    reasoner = ReportReasoner()
    executor = Executor()
    policy = MissionPolicy(tmp_path)
    service = AgentRuntimeService(
        root=tmp_path, store=store, planner=MissionPlanner(reasoner),
        tools=ReadOnlyToolAdapter(executor, policy=policy),
        verifier=MissionVerifier(policy=policy, artifacts=artifacts), artifacts=artifacts,
        report_builder=ReportBuilder(reasoner, artifacts=artifacts),
        config=AgentRuntimeConfig(enabled=True),
    )
    service.start()
    recovered = service.wait_for_terminal(mission.id, timeout_s=2)
    service.stop()
    assert recovered.status == MissionStatus.COMPLETED
    assert executor.calls == 0
