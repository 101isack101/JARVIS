from memory.tools import ToolContext, ToolDispatcher
from runtime_modes import ModeManager


def test_dispatcher_caps_repeated_write_tools(tmp_path):
    from memory.obsidian_vault import ObsidianVault
    from memory.rag import VaultRAG

    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    modes = ModeManager()
    modes.set_mode("agentic")
    dispatcher = ToolDispatcher(ToolContext(vault=vault, rag=rag, modes=modes))

    def fake_organizer(**kwargs):
        return {"ok": True, "executed": False, "action": kwargs.get("action")}

    dispatcher._tools["file_organizer"] = fake_organizer
    args = {"action": "apply", "plan_id": "pln_test"}
    for _ in range(3):
        result = dispatcher.call("file_organizer", args)
        assert result.get("error_code") != "REPEAT_CAP"
    fourth = dispatcher.call("file_organizer", args)
    assert fourth["ok"] is False
    assert fourth["error_code"] == "REPEAT_CAP"
