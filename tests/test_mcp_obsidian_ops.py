from mcp_obsidian import ops
from memory.obsidian_vault import VaultError
import hashlib
import pytest


def test_mcp_obsidian_create_move_and_read_note(tmp_path):
    ops.create_folder("Projects/Jarvis", vault_path=str(tmp_path))

    created = ops.create_note(
        "Projects/Jarvis/Idea",
        "# Idea\n\nContenido inicial.",
        tags=["jarvis"],
        vault_path=str(tmp_path),
    )
    moved = ops.move_path(
        "Projects/Jarvis/Idea.md",
        "Projects/Jarvis/Idea Renombrada.md",
        vault_path=str(tmp_path),
    )
    read = ops.read_note("Projects/Jarvis/Idea Renombrada", vault_path=str(tmp_path))

    assert created["ok"] is True
    assert moved["ok"] is True
    assert read["ok"] is True
    assert "Contenido inicial" in read["body"]


def test_mcp_obsidian_blocks_hidden_paths(tmp_path):
    with pytest.raises(VaultError):
        ops.create_note(".obsidian/Bad", "nope", vault_path=str(tmp_path))


def test_mcp_obsidian_delete_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("JARVIS_OBSIDIAN_MCP_ALLOW_DELETE", raising=False)
    created = ops.create_note("Tmp", "# Tmp", vault_path=str(tmp_path))
    deleted = ops.delete_path("Tmp.md", vault_path=str(tmp_path))

    assert created["ok"] is True
    assert deleted["ok"] is False


def test_mcp_obsidian_blocks_parent_escape(tmp_path):
    with pytest.raises(VaultError):
        ops.create_note("../outside", "nope", vault_path=str(tmp_path))


def test_mcp_create_note_returns_matching_sha256(tmp_path):
    created = ops.create_note(
        "Projects/Hash",
        "# Hola",
        vault_path=str(tmp_path),
    )
    assert created["ok"] is True
    assert created["verified"] is True
    digest = created["sha256"]
    assert len(digest) == 64
    data = (tmp_path / "Projects" / "Hash.md").read_bytes()
    assert hashlib.sha256(data).hexdigest() == digest
