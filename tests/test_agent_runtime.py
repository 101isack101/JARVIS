import json
import threading
import time
from types import SimpleNamespace

from agent_runtime.models import MissionStatus
from agent_runtime.planner import MissionPlanner
from agent_runtime.runtime import AgentRuntimeConfig, AgentRuntimeService
from agent_runtime.store import MissionStore
from agent_runtime.tools import MissionPolicy, ReadOnlyToolAdapter
from agent_runtime.verifier import ArtifactStore, MissionVerifier, ReportBuilder


def _plan(step_count=1, goal="Evaluar el estado técnico de JARVIS"):
    steps = []
    for index in range(1, step_count + 1):
        steps.append({
            "step_id": f"s{index}",
            "operation": "read_file",
            "arguments": {"path": "README.md"},
            "purpose": "Leer evidencia",
            "expected_evidence": "Contenido",
        })
    return json.dumps({
        "version": 1,
        "goal": goal,
        "assumptions": [],
        "steps": steps,
        "report_questions": [],
    })


class QueueReasoner:
    configured = True
    model = "fake"

    def __init__(self, outputs, delay=0):
        self.outputs = list(outputs)
        self.delay = delay

    def ask(self, *args, **kwargs):
        if self.delay:
            time.sleep(self.delay)
        return SimpleNamespace(text=self.outputs.pop(0))


class Executor:
    def __init__(self, gate=None):
        self.calls = 0
        self.gate = gate

    def run_structured(self, operation, **kwargs):
        self.calls += 1
        if self.gate:
            self.gate.wait(2)
        return {
            "ok": True,
            "allowed": True,
            "operation": operation,
            "path": "README.md",
            "content": "evidence",
            "truncated": False,
        }


def _service(tmp_path, reasoner, executor, events=None, tracker=None):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    store = MissionStore(tmp_path / "agent.db")
    policy = MissionPolicy(tmp_path)
    adapter = ReadOnlyToolAdapter(executor, policy=policy)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    verifier = MissionVerifier(policy=policy, artifacts=artifacts)
    return AgentRuntimeService(
        root=tmp_path,
        store=store,
        planner=MissionPlanner(reasoner),
        tools=adapter,
        verifier=verifier,
        artifacts=artifacts,
        report_builder=ReportBuilder(reasoner, artifacts=artifacts),
        config=AgentRuntimeConfig(enabled=True, max_steps=8, deadline_seconds=120),
        event_sink=(events.append if events is not None else None),
        tracker=tracker,
    )


class TemplateExecutor:
    def run_structured(self, operation, **kwargs):
        if operation == "list_dir":
            return {
                "ok": True,
                "allowed": True,
                "operation": "list_dir",
                "items": [{"path": "README.md", "type": "file"}],
            }
        if operation == "read_file":
            return {
                "ok": True,
                "allowed": True,
                "operation": "read_file",
                "path": "README.md",
                "content": "hello",
                "truncated": False,
            }
        if operation == "search_text":
            return {
                "ok": True,
                "allowed": True,
                "operation": "search_text",
                "matches": [{"path": "README.md", "line": 1}],
            }
        return {
            "ok": True,
            "allowed": True,
            "operation": operation,
            "returncode": 0,
            "stdout": "ok",
        }


def test_create_without_reasoner_uses_template_plan(tmp_path):
    events = []
    service = _service(tmp_path, None, TemplateExecutor(), events)
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=3)
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=3)
    service.stop()
    assert created["ok"] is True
    assert mission.status == MissionStatus.COMPLETED
    assert "[step:s1]" in (mission.final_summary or "")


def test_create_returns_before_slow_planner_and_completes(tmp_path):
    reasoner = QueueReasoner([_plan(), "Hallazgo [step:s1]"], delay=0.15)
    events = []
    service = _service(tmp_path, reasoner, Executor(), events)
    service.start()
    started = time.perf_counter()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=3)
    service.stop()
    assert mission.status == MissionStatus.COMPLETED
    assert "[step:s1]" in mission.final_summary
    assert [event["event_type"] for event in events][-1] == "mission.completed"


def test_cancel_during_step_prevents_next_step(tmp_path):
    gate = threading.Event()
    executor = Executor(gate)
    reasoner = QueueReasoner([_plan(step_count=2), "unused"])
    service = _service(tmp_path, reasoner, executor)
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    deadline = time.time() + 2
    while executor.calls == 0 and time.time() < deadline:
        time.sleep(0.01)
    service.cancel(created["mission_id"])
    gate.set()
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=2)
    service.stop()
    assert mission.status == MissionStatus.CANCELLED
    assert executor.calls == 1


def test_policy_rejection_blocks_before_executor(tmp_path):
    bad = json.loads(_plan())
    bad["steps"][0]["operation"] = "run_powershell"
    executor = Executor()
    service = _service(tmp_path, QueueReasoner([json.dumps(bad)] * 2), executor)
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=2)
    service.stop()
    assert mission.status == MissionStatus.BLOCKED
    assert mission.error_code == "UNKNOWN_OPERATION"
    assert executor.calls == 0


def test_plan_cannot_change_authorized_goal(tmp_path):
    raw = json.loads(_plan())
    raw["goal"] = "Inspeccionar un objetivo diferente"
    executor = Executor()
    service = _service(tmp_path, QueueReasoner([json.dumps(raw)]), executor)
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=2)
    service.stop()
    assert mission.status == MissionStatus.BLOCKED
    assert mission.error_code == "GOAL_MISMATCH"
    assert executor.calls == 0


def test_budget_gate_blocks_before_planner(tmp_path):
    reasoner = QueueReasoner([_plan()])
    service = _service(tmp_path, reasoner, Executor())
    service.budget_check = lambda: False
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=2)
    service.stop()
    assert mission.status == MissionStatus.BLOCKED
    assert mission.error_code == "BUDGET_EXCEEDED"
    assert reasoner.outputs == [_plan()]


def test_only_one_mission_runs_at_a_time(tmp_path):
    gate = threading.Event()
    executor = Executor(gate)
    goal_one = "Evaluar el estado técnico de JARVIS número uno"
    goal_two = "Evaluar el estado técnico de JARVIS número dos"
    reasoner = QueueReasoner([_plan(goal=goal_one), "Uno [step:s1]", _plan(goal=goal_two), "Dos [step:s1]"])
    service = _service(tmp_path, reasoner, executor)
    service.start()
    first = service.create(goal_one, max_steps=4)
    second = service.create(goal_two, max_steps=4)
    deadline = time.time() + 2
    while executor.calls == 0 and time.time() < deadline:
        time.sleep(0.01)
    assert service.status(second["mission_id"])["mission"]["status"] == "queued"
    gate.set()
    assert service.wait_for_terminal(first["mission_id"], 3).status == MissionStatus.COMPLETED
    assert service.wait_for_terminal(second["mission_id"], 3).status == MissionStatus.COMPLETED
    service.stop()


def test_event_sink_observes_event_after_commit(tmp_path):
    store_ref = {}
    observed = []

    def sink(event):
        store = store_ref["store"]
        observed.append(any(saved.id == event["id"] for saved in store.events(event["mission_id"])))

    service = _service(tmp_path, QueueReasoner([_plan(), "Listo [step:s1]"]), Executor(), [])
    service.event_sink = sink
    store_ref["store"] = service.store
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    service.wait_for_terminal(created["mission_id"], 3)
    service.stop()
    assert observed and all(observed)


def test_status_exposes_bounded_mission_window_telemetry(tmp_path):
    class Tracker:
        def __init__(self):
            self.costs = {"gemini": 1.0, "claude": 2.0, "other": 0.0}
            self.tokens = {"gemini": 100, "claude": 200, "other": 0}

        def cost_by_provider(self):
            return dict(self.costs)

        def tokens_by_provider(self):
            return dict(self.tokens)

    class MeteredReasoner(QueueReasoner):
        def __init__(self, tracker):
            super().__init__([_plan(), "Hallazgo [step:s1]"])
            self.tracker = tracker

        def ask(self, *args, **kwargs):
            self.tracker.costs["claude"] += 0.125
            self.tracker.tokens["claude"] += 250
            return super().ask(*args, **kwargs)

    tracker = Tracker()
    service = _service(tmp_path, MeteredReasoner(tracker), Executor(), tracker=tracker)
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=3)
    detail = service.status(created["mission_id"])

    assert mission.status == MissionStatus.COMPLETED
    assert detail["ok"] is True
    assert detail["mission"]["telemetry"] == {
        "duration_ms": detail["mission"]["duration_ms"],
        "cost_usd": 0.25,
        "tokens": 500,
        "providers": {
            "gemini": {"cost_usd": 0.0, "tokens": 0},
            "claude": {"cost_usd": 0.25, "tokens": 500},
            "other": {"cost_usd": 0.0, "tokens": 0},
        },
        "scope": "mission_window_delta",
    }
    assert detail["steps"][0]["duration_ms"] is not None
    tracker.costs["claude"] += 5.0
    tracker.tokens["claude"] += 5000
    assert service.status(created["mission_id"])["mission"]["telemetry"] == detail["mission"]["telemetry"]
    service.stop()


def test_cancel_during_report_wins_over_completion(tmp_path):
    report_started = threading.Event()
    release_report = threading.Event()

    class BlockingReportReasoner:
        configured = True
        model = "fake"
        calls = 0

        def ask(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(text=_plan())
            report_started.set()
            release_report.wait(2)
            return SimpleNamespace(text="Hallazgo [step:s1]")

    service = _service(tmp_path, BlockingReportReasoner(), Executor())
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=4)
    assert report_started.wait(2)
    service.cancel(created["mission_id"])
    release_report.set()
    mission = service.wait_for_terminal(created["mission_id"], 2)
    service.stop()
    assert mission.status == MissionStatus.CANCELLED
