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

    def run_structured(self, **kwargs):
        return {"ok": True, "allowed": True, **kwargs}


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

    assert "jarvis_session_recall" in dispatcher.tool_names
    assert deep["ok"] is True
    assert "deep:" in deep["text"]
    assert mode["changed"] is True
    assert browser["executed"] is True
    assert browser["url"] == "about:blank"
    assert mcp["tool"] == "obsidian_create_folder"
    assert security["ok"] is True
    assert security["hitl"]["enabled"] is True
    assert security["kill_switch"]["behavior"] == "hard exit via os._exit(130)"


def test_run_safe_command_rejects_legacy_shell_command(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    ctx = ToolContext(vault=vault, rag=rag, actions=FakeActions())
    dispatcher = ToolDispatcher(ctx)

    result = dispatcher.call("jarvis_run_safe_command", {"command": "Get-Content C:/secret.txt"})

    assert result["ok"] is False
    assert result["allowed"] is False
    assert "PowerShell libre" in result["error"]


def test_session_recall_tool_reads_recent_session_notes(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    sessions = vault.memory_path / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / "2026-05-30_2100_sesion.md").write_text(
        "---\ntype: session-journal\n---\n\n# S\n\n"
        "## Resumen\n- Mejoramos la UI cyberpunk de Jarvis.\n\n"
        "## Pendientes\n- Conectar ondas a la voz.\n",
        encoding="utf-8",
    )
    ctx = ToolContext(vault=vault, rag=rag)
    dispatcher = ToolDispatcher(ctx)

    result = dispatcher.call(
        "jarvis_session_recall",
        {"query": "UI cyberpunk", "when": "2026-05-30"},
    )

    assert result["found"] == 1
    assert "ondas" in result["sessions"][0]["summary"]


def test_english_practice_toggle_changes_runtime_mode(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    modes = ModeManager()
    ctx = ToolContext(vault=vault, rag=rag, modes=modes)
    dispatcher = ToolDispatcher(ctx)

    started = dispatcher.call(
        "english_practice",
        {"action": "start", "level": "B1", "focus": "dev"},
    )
    status = dispatcher.call("english_practice", {"action": "status"})
    stopped = dispatcher.call("english_practice", {"action": "stop"})

    assert started["ok"] is True
    assert started["active"] is True
    assert started["mode"] == "english"
    assert status["active"] is True
    assert status["mode"] == "english"
    assert stopped["ok"] is True
    assert stopped["active"] is False
    assert modes.get_mode()["mode"] == "general"


def test_study_mode_start_requires_hitl(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    ctx = ToolContext(vault=vault, rag=rag)
    dispatcher = ToolDispatcher(ctx)

    denied = dispatcher.call("study_mode", {"action": "start", "title": "Security Test"})

    assert denied["ok"] is False
    assert "HITL" in denied["error"]
