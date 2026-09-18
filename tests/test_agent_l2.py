from pathlib import Path

from actions.file_organizer import FileOrganizer
from agent_runtime.models import PlanStep, make_mission, make_mission_step
from agent_runtime.tools import MissionPolicy, ReadOnlyToolAdapter
from mcp_obsidian import ops
from security.approvals import AutoApprovalBroker


def _write_step(operation, arguments):
    return make_mission_step(
        "msn_l2",
        1,
        PlanStep("s1", operation, arguments, "purpose", "evidence"),
    )


def test_l1_policy_rejects_write_ops_and_invented_write_file(tmp_path):
    policy = MissionPolicy(tmp_path, autonomy_level="L1")
    assert policy.evaluate("obsidian_create_note", {"path": "a.md", "content": "x"}).reason_code == "AUTONOMY_DENIED"
    assert policy.evaluate("file_organizer_apply", {"plan_id": "pln_x"}).reason_code == "AUTONOMY_DENIED"
    assert policy.evaluate("write_file", {"path": "a.md", "content": "x"}).reason_code == "UNKNOWN_OPERATION"


def test_l2_policy_allows_closed_writes_and_still_rejects_write_file(tmp_path):
    policy = MissionPolicy(tmp_path, autonomy_level="L2")
    allowed = policy.evaluate("obsidian_create_note", {"path": "Jarvis/Note.md", "content": "hola"})
    assert allowed.allowed is True
    assert allowed.risk == "write"
    assert policy.evaluate("write_file", {"path": "a.md", "content": "x"}).reason_code == "UNKNOWN_OPERATION"
    assert policy.evaluate("obsidian_create_note", {"path": "../escape.md", "content": "x"}).reason_code == "INVALID_ARGUMENTS"


def test_l2_write_without_broker_fails_closed(tmp_path):
    policy = MissionPolicy(tmp_path, autonomy_level="L2")
    adapter = ReadOnlyToolAdapter(
        executor=object(),
        policy=policy,
        writers={"obsidian_create_note": lambda **kw: {"ok": True}},
    )
    result = adapter.execute(_write_step("obsidian_create_note", {"path": "n.md", "content": "x"}))
    assert result["ok"] is False
    assert result["allowed"] is False
    assert result["error_code"] == "HITL_DENIED"


def test_l2_write_rejected_hitl_does_not_call_writer(tmp_path):
    calls = []
    policy = MissionPolicy(tmp_path, autonomy_level="L2")
    adapter = ReadOnlyToolAdapter(
        executor=object(),
        policy=policy,
        approval_broker=AutoApprovalBroker(approve=False),
        writers={"obsidian_create_note": lambda **kw: calls.append(kw) or {"ok": True}},
    )
    result = adapter.execute(_write_step("obsidian_create_note", {"path": "n.md", "content": "x"}))
    assert result["error_code"] == "HITL_DENIED"
    assert calls == []


def test_l2_verifier_requires_sha256(tmp_path):
    from agent_runtime.verifier import ArtifactStore, MissionVerifier, VerificationError
    import pytest

    step = _write_step("obsidian_create_note", {"path": "n.md", "content": "x"})
    verifier = MissionVerifier(
        policy=MissionPolicy(tmp_path, autonomy_level="L2"),
        artifacts=ArtifactStore(tmp_path / "arts"),
    )
    ok = verifier.verify_step(step, {
        "ok": True,
        "allowed": True,
        "operation": "obsidian_create_note",
        "path": "n.md",
        "verified": True,
        "sha256": "a" * 64,
    })
    assert ok["status"] == "passed"
    with pytest.raises(VerificationError):
        verifier.verify_step(step, {
            "ok": True,
            "allowed": True,
            "operation": "obsidian_create_note",
            "path": "n.md",
            "verified": True,
        })


def test_l2_runtime_creates_note_after_hitl(tmp_path):
    import json
    from types import SimpleNamespace

    from agent_runtime.planner import MissionPlanner
    from agent_runtime.runtime import AgentRuntimeConfig, AgentRuntimeService
    from agent_runtime.store import MissionStore
    from agent_runtime.verifier import ArtifactStore, MissionVerifier, ReportBuilder
    from agent_runtime.models import MissionStatus

    vault = tmp_path / "vault"
    vault.mkdir()
    plan = json.dumps({
        "version": 1,
        "goal": "Evaluar el estado técnico de JARVIS",
        "assumptions": [],
        "steps": [{
            "step_id": "s1",
            "operation": "obsidian_create_note",
            "arguments": {"path": "Jarvis/L2.md", "content": "canario L2"},
            "purpose": "Crear nota",
            "expected_evidence": "nota verificada",
        }],
        "report_questions": [],
    })

    class Reasoner:
        configured = True
        model = "fake"

        def __init__(self):
            self.outputs = [plan, "Nota creada [step:s1]"]

        def ask(self, *args, **kwargs):
            return SimpleNamespace(text=self.outputs.pop(0))

    class Executor:
        def run_structured(self, operation, **kwargs):
            raise AssertionError("L2 write no debe usar executor read-only")

    reasoner = Reasoner()
    policy = MissionPolicy(tmp_path, autonomy_level="L2")
    adapter = ReadOnlyToolAdapter(
        Executor(),
        policy=policy,
        approval_broker=AutoApprovalBroker(approve=True),
        writers={
            "obsidian_create_note": lambda path, content: ops.create_note(
                path, content, vault_path=str(vault)
            )
        },
    )
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = AgentRuntimeService(
        root=tmp_path,
        store=MissionStore(tmp_path / "agent.db"),
        planner=MissionPlanner(reasoner),
        tools=adapter,
        verifier=MissionVerifier(policy=policy, artifacts=artifacts),
        artifacts=artifacts,
        report_builder=ReportBuilder(reasoner, artifacts=artifacts),
        config=AgentRuntimeConfig(enabled=True, max_steps=8, deadline_seconds=120, autonomy_level="L2"),
    )
    service.start()
    created = service.create("Evaluar el estado técnico de JARVIS", max_steps=1)
    mission = service.wait_for_terminal(created["mission_id"], timeout_s=5)
    service.stop()
    assert created["autonomy_level"] == "L2"
    assert mission.status == MissionStatus.COMPLETED
    note = vault / "Jarvis" / "L2.md"
    assert note.is_file()
    assert "canario L2" in note.read_text(encoding="utf-8")
