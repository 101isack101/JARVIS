from actions.executor import SafeActionExecutor
from memory.obsidian_vault import ObsidianVault
from memory.rag import VaultRAG
from memory.tools import ToolContext, ToolDispatcher
from security.approvals import AutoApprovalBroker


def test_action_executor_requires_hitl_for_write_command(tmp_path):
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_powershell("git push origin main", cwd=str(tmp_path))

    assert result["allowed"] is False
    assert "aprobacion HITL" in result["error"]


def test_action_executor_allows_approved_write_command_as_dry_run(tmp_path):
    broker = AutoApprovalBroker(approve=True)
    ex = SafeActionExecutor(root=tmp_path, mode="dev", approval_broker=broker)

    result = ex.run_powershell("git push origin main", cwd=str(tmp_path))

    assert result["allowed"] is True
    assert result["executed"] is False
    assert broker.requests[0][0] == "git_publish"


def test_rag_redacts_secrets_before_indexing(tmp_path):
    import numpy as np

    vault = ObsidianVault(tmp_path, read_all=True)
    note = tmp_path / "Regular Note.md"
    note.write_text(
        "# Secrets\n\nGEMINI_API_KEY=AIza1234567890abcdefghijklmnopqrstuvwxyz",
        encoding="utf-8",
    )
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    rag._embed = lambda texts: np.zeros((len(texts), 384), dtype=np.float32)

    added = rag.index_file(note)

    assert added == 1
    assert "AIza" not in rag.chunks[0].text
    assert "[REDACTED" in rag.chunks[0].text


def test_rag_skips_secret_named_files(tmp_path):
    import numpy as np

    vault = ObsidianVault(tmp_path, read_all=True)
    note = tmp_path / "Secrets In Note.md"
    note.write_text(
        "# Secrets\n\nGEMINI_API_KEY=AIza1234567890abcdefghijklmnopqrstuvwxyz",
        encoding="utf-8",
    )
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    rag._embed = lambda texts: np.zeros((len(texts), 384), dtype=np.float32)

    added = rag.index_file(note)

    assert added == 0
    assert not rag.chunks


def test_obsidian_mcp_write_requires_approval(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")

    class FakeMCP:
        def call_tool(self, name, args):
            return {"ok": True, "tool": name, "args": args}

    ctx = ToolContext(vault=vault, rag=rag, obsidian_mcp=FakeMCP())
    dispatcher = ToolDispatcher(ctx)

    denied = dispatcher.call("obsidian_mcp", {"operation": "create_folder", "path": "X"})

    assert denied["ok"] is False
    assert "HITL" in denied["error"]


def test_obsidian_mcp_write_uses_approval_broker(tmp_path):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    approvals = AutoApprovalBroker(approve=True)

    class FakeMCP:
        def call_tool(self, name, args):
            return {"ok": True, "tool": name, "args": args}

    ctx = ToolContext(
        vault=vault,
        rag=rag,
        obsidian_mcp=FakeMCP(),
        approvals=approvals,
    )
    dispatcher = ToolDispatcher(ctx)

    ok = dispatcher.call("obsidian_mcp", {"operation": "create_folder", "path": "X"})

    assert ok["ok"] is True
    assert approvals.requests[0][0] == "write"
