from types import SimpleNamespace

from memory.tools import ToolContext, ToolDispatcher, all_function_declarations, jarvis_task


class Runtime:
    def __init__(self): self.calls = []
    def create(self, goal, **kwargs):
        self.calls.append(("create", goal, kwargs))
        return {"ok": True, "mission_id": "msn_test", "status": "queued"}
    def status(self, mission_id):
        self.calls.append(("status", mission_id))
        return {"ok": True, "mission": {"id": mission_id}}
    def cancel(self, mission_id):
        self.calls.append(("cancel", mission_id))
        return {"ok": True, "status": "cancel_requested"}
    def list(self, limit=20):
        self.calls.append(("list", limit))
        return {"ok": True, "missions": []}


def test_jarvis_task_routes_closed_actions():
    runtime = Runtime()
    ctx = ToolContext(vault=None, rag=None, agent_runtime=runtime)
    assert jarvis_task(ctx, "create", goal="Evaluar el estado técnico de JARVIS")["status"] == "queued"
    assert jarvis_task(ctx, "status", mission_id="msn_test")["ok"] is True
    assert jarvis_task(ctx, "cancel", mission_id="msn_test")["ok"] is True
    assert jarvis_task(ctx, "list", limit=999)["ok"] is True
    assert runtime.calls[-1] == ("list", 20)
    assert jarvis_task(ctx, "unknown")["error_code"] == "INVALID_ACTION"


def test_jarvis_task_fails_closed_without_runtime_or_ids():
    assert jarvis_task(ToolContext(vault=None, rag=None), "list")["error_code"] == "AGENT_RUNTIME_UNAVAILABLE"
    runtime = Runtime()
    ctx = ToolContext(vault=None, rag=None, agent_runtime=runtime)
    assert jarvis_task(ctx, "create", goal="")["error_code"] == "INVALID_GOAL"
    assert jarvis_task(ctx, "status")["error_code"] == "MISSION_ID_REQUIRED"


def test_tool_is_declared_and_dispatched():
    names = [declaration.name for declaration in all_function_declarations()]
    assert "jarvis_task" in names
    runtime = Runtime()
    dispatcher = ToolDispatcher(ToolContext(vault=None, rag=None, agent_runtime=runtime))
    result = dispatcher.call("jarvis_task", {"action": "list"})
    assert result["ok"] is True
    assert "jarvis_task" in dispatcher.tool_names


def test_jarvis_task_create_does_not_accept_autonomy_level():
    import pytest

    with pytest.raises(TypeError):
        jarvis_task(
            ToolContext(vault=None, rag=None),
            "create",
            goal="Evaluar el estado técnico de JARVIS",
            autonomy_level="L2",
        )
