from types import SimpleNamespace

import pytest

from agent_runtime.planner import MissionPlanner, PlannerError, extract_single_json_object


VALID = """{
  "version": 1,
  "goal": "Evaluar el estado técnico de JARVIS",
  "assumptions": [],
  "steps": [{
    "step_id": "s1",
    "operation": "git_status",
    "arguments": {},
    "purpose": "Revisar cambios",
    "expected_evidence": "Estado Git"
  }],
  "report_questions": []
}"""


class FakeReasoner:
    configured = True
    model = "fake-planner"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def ask(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return SimpleNamespace(text=self.outputs.pop(0))


def test_planner_parses_valid_and_fenced_json():
    for output in (VALID, f"```json\n{VALID}\n```"):
        planner = MissionPlanner(FakeReasoner([output]))
        plan = planner.plan(goal="Evaluar el estado técnico de JARVIS", max_steps=4, deadline_seconds=120)
        assert plan.steps[0].operation == "git_status"


def test_planner_repairs_once():
    reasoner = FakeReasoner(["not json", VALID])
    plan = MissionPlanner(reasoner).plan(
        goal="Evaluar el estado técnico de JARVIS", max_steps=4, deadline_seconds=120,
    )
    assert plan.version == 1
    assert len(reasoner.calls) == 2


def test_planner_blocks_double_invalid_output():
    with pytest.raises(PlannerError, match="tras repair"):
        MissionPlanner(FakeReasoner(["bad", "still bad"])).plan(
            goal="Evaluar el estado técnico de JARVIS", max_steps=4, deadline_seconds=120,
        )


def test_json_extractor_rejects_prose_or_multiple_objects():
    with pytest.raises(PlannerError):
        extract_single_json_object("here: {}")
    with pytest.raises(PlannerError):
        extract_single_json_object("{} {}")
