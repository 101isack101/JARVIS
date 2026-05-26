from memory.obsidian_vault import ObsidianVault
from memory.rag import VaultRAG
from memory.tools import ToolContext, ToolDispatcher
from runtime_modes import ModeManager
from security.approvals import AutoApprovalBroker


class FakeReasoner:
    model = "fake-claude"

    def ask(self, prompt, context_extra=None, max_tokens=1024):
        class Response:
            text = f"deep: {prompt}"
            latency_ms = 12.3
            cost_usd = 0.001
            input_tokens = 10
            output_tokens = 20
            cache_creation_tokens = 0
            cache_read_tokens = 0

        return Response()


class FakeActions:
    def open_url(self, url=None):
        return {"executed": True, "allowed": True, "url": url or "about:blank"}


class FakeObsidianMCP:
    def call_tool(self, name, arguments):
        return {"ok": True, "tool": name, "arguments": arguments}


def test_dispatcher_exposes_claude_and_modes(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    ctx = ToolContext(
        vault=vault,
        rag=rag,
        reasoner=FakeReasoner(),
        actions=FakeActions(),
        modes=ModeManager(),
        obsidian_mcp=FakeObsidianMCP(),
        approvals=AutoApprovalBroker(approve=True),
    )
    dispatcher = ToolDispatcher(ctx)

    deep = dispatcher.call("ask_claude_deep", {"prompt": "razona esto"})
    mode = dispatcher.call("jarvis_set_mode", {"mode": "debugging"})
    browser = dispatcher.call("jarvis_open_url", {})
    mcp = dispatcher.call("obsidian_mcp", {"operation": "create_folder", "path": "Projects/Jarvis"})
    security = dispatcher.call("jarvis_security_status", {})

    assert deep["ok"] is True
    assert "deep:" in deep["text"]
    assert mode["changed"] is True
    assert browser["executed"] is True
    assert browser["url"] == "about:blank"
    assert mcp["tool"] == "obsidian_create_folder"
    assert security["ok"] is True
    assert security["hitl"]["enabled"] is True
    assert security["kill_switch"]["behavior"] == "hard exit via os._exit(130)"


def test_study_mode_start_requires_hitl(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    ctx = ToolContext(vault=vault, rag=rag)
    dispatcher = ToolDispatcher(ctx)

    denied = dispatcher.call("study_mode", {"action": "start", "title": "Security Test"})

    assert denied["ok"] is False
    assert "HITL" in denied["error"]
