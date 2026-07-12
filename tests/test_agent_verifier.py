from types import SimpleNamespace

import pytest

from agent_runtime.models import (
    MissionStatus,
    PlanStep,
    StepStatus,
    make_mission,
    make_mission_step,
)
from agent_runtime.tools import MissionPolicy
from agent_runtime.verifier import ArtifactStore, MissionVerifier, ReportBuilder, VerificationError


def _objects(tmp_path, operation="read_file"):
    mission = make_mission(
        goal="Evaluar el estado técnico de JARVIS",
        root_path=str(tmp_path),
        max_steps=4,
        deadline_seconds=120,
    )
    step = make_mission_step(
        mission.id, 1, PlanStep("s1", operation, {"path": "README.md"}, "leer", "contenido"),
    )
    return mission, step


def test_artifact_is_atomic_redacted_and_hash_checked(tmp_path):
    mission, step = _objects(tmp_path)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    meta = artifacts.write_result(
        mission, step, {"ok": True, "token": "OPENAI_API_KEY=sk-" + "x" * 30},
    )
    payload = artifacts.read(meta)
    assert "sk-" + "x" * 30 not in str(payload)
    path = artifacts.root / meta.relative_path
    path.write_text("{}", encoding="utf-8")
    assert artifacts.validate(meta) is False


def test_step_verifier_checks_schema_and_policy(tmp_path):
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    _, step = _objects(tmp_path)
    verifier = MissionVerifier(policy=MissionPolicy(tmp_path), artifacts=ArtifactStore(tmp_path / "arts"))
    verification = verifier.verify_step(step, {
        "ok": True,
        "allowed": True,
        "operation": "read_file",
        "path": "README.md",
        "content": "hello",
        "truncated": False,
    })
    assert verification["status"] == "passed"
    with pytest.raises(VerificationError):
        verifier.verify_step(step, {"ok": True, "allowed": True, "operation": "read_file"})


def test_report_requires_valid_step_references(tmp_path):
    mission, step = _objects(tmp_path)
    artifacts = ArtifactStore(tmp_path / "arts")
    meta = artifacts.write_result(mission, step, {
        "ok": True, "allowed": True, "operation": "read_file", "content": "ok", "truncated": False,
    })
    step.status = StepStatus.COMPLETED
    step.artifact_id = meta.id
    step.verification_status = "passed"

    class Reasoner:
        def __init__(self, text): self.text = text
        def ask(self, *args, **kwargs): return SimpleNamespace(text=self.text)

    builder = ReportBuilder(Reasoner("Hallazgo confirmado [step:s1]"), artifacts=artifacts)
    assert "confirmado" in builder.build(mission, [step], {meta.id: meta})
    with pytest.raises(VerificationError) as exc:
        ReportBuilder(Reasoner("Sin evidencia"), artifacts=artifacts).build(mission, [step], {meta.id: meta})
    assert exc.value.code == "UNSUPPORTED_REPORT"


def test_large_artifact_is_compacted_but_valid(tmp_path):
    mission, step = _objects(tmp_path)
    artifacts = ArtifactStore(tmp_path / "arts", max_chars=1000)
    meta = artifacts.write_result(mission, step, {
        "ok": True,
        "allowed": True,
        "operation": "read_file",
        "path": "README.md",
        "content": "x" * 5000,
        "truncated": False,
    })
    payload = artifacts.read(meta)
    assert payload["result"]["artifact_truncated"] is True
    assert meta.size_bytes < 1600
