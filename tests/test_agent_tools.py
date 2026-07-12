from pathlib import Path

from actions.executor import SafeActionExecutor
from agent_runtime.models import PlanStep, make_mission_step
from agent_runtime.tools import MissionPolicy, ReadOnlyToolAdapter, result_summary


def _step(operation, arguments):
    return make_mission_step(
        "msn_test",
        1,
        PlanStep("s1", operation, arguments, "purpose", "evidence"),
    )


def test_policy_only_allows_closed_read_operations(tmp_path):
    policy = MissionPolicy(tmp_path)
    assert policy.evaluate("git_status", {}).allowed is True
    assert policy.evaluate("run_powershell", {"command": "dir"}).reason_code == "UNKNOWN_OPERATION"
    assert policy.evaluate("git_status", {"surprise": 1}).reason_code == "INVALID_ARGUMENTS"


def test_policy_blocks_escape_secret_and_symlink(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = MissionPolicy(root)

    assert policy.evaluate("read_file", {"path": "../outside/x"}).reason_code == "OUTSIDE_ROOT"
    assert policy.evaluate("read_file", {"path": ".env"}).reason_code == "SECRET_PATH"

    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    assert policy.evaluate("list_dir", {"path": "linked"}).reason_code == "OUTSIDE_ROOT"


def test_adapter_reads_and_redacts_results(tmp_path):
    note = tmp_path / "README.md"
    note.write_text("OPENAI_API_KEY=sk-" + "a" * 30, encoding="utf-8")
    executor = SafeActionExecutor(root=tmp_path, mode="prod")
    adapter = ReadOnlyToolAdapter(executor, policy=MissionPolicy(tmp_path))

    result = adapter.execute(_step("read_file", {"path": "README.md"}))
    assert result["ok"] is True
    assert "sk-" + "a" * 30 not in result["content"]
    assert "REDACTED" in result["content"]
    assert "archivo leído" in result_summary("read_file", result)


def test_adapter_rejects_executor_claiming_wrong_operation(tmp_path):
    class Liar:
        def run_structured(self, operation, **kwargs):
            return {"ok": True, "allowed": True, "operation": "write_file"}

    adapter = ReadOnlyToolAdapter(Liar(), policy=MissionPolicy(tmp_path))
    result = adapter.execute(_step("git_status", {}))
    assert result["ok"] is False
    assert result["error_code"] == "INVALID_TOOL_RESULT"
