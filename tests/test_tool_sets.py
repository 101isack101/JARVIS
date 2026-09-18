from memory.tool_sets import GATED_TOOLS, is_tool_allowed_for_mode, tools_for_mode
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


def test_general_mode_blocks_every_gated_tool():
    gated = set().union(*GATED_TOOLS.values())
    assert gated, "catálogo gated vacío"
    for name in sorted(gated):
        assert not is_tool_allowed_for_mode(name, "general"), name


def test_general_mode_still_allows_mcp_and_remember():
    # Documentado, no es un bug: HITL cubre MCP write; remember es nota.
    assert is_tool_allowed_for_mode("obsidian_mcp", "general")
    assert is_tool_allowed_for_mode("jarvis_remember", "general")
    assert is_tool_allowed_for_mode("jarvis_open_url", "general")


def test_dispatcher_rejects_each_gated_tool_in_general(tmp_path):
    from memory.obsidian_vault import ObsidianVault
    from memory.rag import VaultRAG

    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    dispatcher = ToolDispatcher(ToolContext(vault=vault, rag=rag, modes=ModeManager()))
    for name in sorted(set().union(*GATED_TOOLS.values())):
        result = dispatcher.call(name, {})
        error = str(result.get("error") or "")
        assert result.get("ok") is False or "desconocida" in error, name
        assert "no disponible" in error or "desconocida" in error, name
