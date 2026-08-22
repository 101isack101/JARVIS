from memory.tool_sets import is_tool_allowed_for_mode, tools_for_mode
from memory.tool_declarations import all_function_declarations
from memory.tools import ToolContext, ToolDispatcher
from runtime_modes import ModeManager


def test_general_mode_blocks_computer_use_tools():
    assert is_tool_allowed_for_mode("jarvis_recall", "general")
    assert is_tool_allowed_for_mode("ask_grok_deep", "general")
    assert is_tool_allowed_for_mode("jarvis_task", "general")
    assert not is_tool_allowed_for_mode("camera_look", "general")
    assert not is_tool_allowed_for_mode("file_organizer", "general")
    assert "jarvis_task" in tools_for_mode("agentic")
    assert "file_organizer" in tools_for_mode("agentic")


def test_dispatcher_enforces_mode_when_modes_present(tmp_path):
    from memory.obsidian_vault import ObsidianVault
    from memory.rag import VaultRAG

    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    ctx = ToolContext(vault=vault, rag=rag, modes=ModeManager())
    dispatcher = ToolDispatcher(ctx)
    blocked = dispatcher.call("file_organizer", {"action": "status"})
    assert blocked["ok"] is False
    assert "no disponible" in blocked["error"]
    listed = dispatcher.call("jarvis_task", {"action": "list"})
    assert listed.get("error_code") == "AGENT_RUNTIME_UNAVAILABLE"
    ctx.modes.set_mode("agentic")
    allowed = dispatcher.call("file_organizer", {"action": "status"})
    assert "no disponible" not in str(allowed.get("error") or "")


def test_live_declarations_follow_mode():
    general = {d.name for d in all_function_declarations("general")}
    agentic = {d.name for d in all_function_declarations("agentic")}
    assert "jarvis_task" in general
    assert "ask_grok_deep" in general
    assert "camera_look" not in general
    assert "file_organizer" not in general
    assert "file_organizer" in agentic
    assert "jarvis_run_safe_command" in agentic


def test_dispatcher_without_modes_allows_all(tmp_path):
    from memory.obsidian_vault import ObsidianVault
    from memory.rag import VaultRAG

    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    dispatcher = ToolDispatcher(ToolContext(vault=vault, rag=rag))
    result = dispatcher.call("jarvis_task", {"action": "list"})
    assert result.get("error_code") == "AGENT_RUNTIME_UNAVAILABLE"
