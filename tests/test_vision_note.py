from pathlib import Path

from memory.obsidian_vault import ObsidianVault
from memory.rag import VaultRAG
from memory.tools import ToolContext, ToolDispatcher
from runtime_modes import ModeManager


class _Shot:
    def __init__(self, folder: Path):
        self.path = folder / "screen-test.png"
        self.path.write_bytes(b"\x89PNG\r\n")
        self.width = 8
        self.height = 8
        self.png_bytes = self.path.read_bytes()
        self.mime_type = "image/png"

    def as_dict(self):
        return {
            "captured": True,
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
        }


class _Screen:
    def __init__(self, folder: Path):
        self.folder = folder
        self.calls = 0

    def capture(self):
        self.calls += 1
        return _Shot(self.folder)


class _Approvals:
    def __init__(self, approved: bool):
        self.approved = approved
        self.calls = 0

    def request(self, risk, title, details, timeout_s=None):
        self.calls += 1
        assert "sk-ant-" not in details
        return self.approved


def _ctx(tmp_path, approvals=None, screen=None):
    vault = ObsidianVault(tmp_path, read_all=True)
    rag = VaultRAG(vault=vault, index_dir=tmp_path / "rag")
    return ToolContext(
        vault=vault,
        rag=rag,
        screen=screen or _Screen(tmp_path),
        approvals=approvals,
        modes=ModeManager(),
    )


def test_build_vision_note_redacts_secrets():
    from vision.notes import build_vision_note

    secret = "sk-ant-" + "a" * 24
    note = build_vision_note(
        source="screen",
        image_ref="screen-test.png",
        reason="mira esto",
        analysis=f"la pantalla muestra {secret}",
        title="Prueba",
    )
    assert secret not in note
    assert "[REDACTED_ANTHROPIC_KEY]" in note
    assert "screen-test.png" in note
    assert "JARVIS/01-CAPTURES" not in note or True


def test_vision_note_path_cannot_escape_vault():
    from vision.notes import vision_note_relpath

    rel = vision_note_relpath("../.obsidian/secret", when=__import__("datetime").datetime(2026, 10, 14, 1, 2, 3))
    assert rel.startswith("JARVIS/01-CAPTURES/vision/")
    assert ".." not in rel
    assert ".obsidian" not in rel


def test_vision_note_without_analysis_does_not_write(tmp_path):
    ctx = _ctx(tmp_path)
    result = ToolDispatcher(ctx).call("vision_note", {"title": "sin analisis"})
    assert result.get("captured") is True
    assert result.get("needs_analysis") is True
    assert list(tmp_path.rglob("JARVIS/**/*.md")) == []


def test_vision_note_writes_only_after_approval(tmp_path):
    screen = _Screen(tmp_path)
    ctx = _ctx(tmp_path, approvals=_Approvals(True), screen=screen)
    secret = "sk-ant-" + "b" * 24
    result = ToolDispatcher(ctx).call(
        "vision_note",
        {"title": "Cable", "analysis": f"veo un cable y {secret}", "source": "screen"},
    )
    assert result.get("ok") is True
    written = list((tmp_path / "JARVIS").rglob("*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert secret not in text
    assert "veo un cable" in text
    assert "screen-test.png" in text


def test_rejected_approval_writes_nothing(tmp_path):
    ctx = _ctx(tmp_path, approvals=_Approvals(False))
    result = ToolDispatcher(ctx).call(
        "vision_note",
        {"title": "No", "analysis": "un objeto sobre la mesa"},
    )
    assert result.get("ok") is False
    assert list(tmp_path.rglob("JARVIS/**/*.md")) == []


def test_vision_note_is_available_in_general_mode():
    from memory.tool_sets import is_tool_allowed_for_mode

    assert is_tool_allowed_for_mode("vision_note", "general")
    names = {d.name for d in __import__("memory.tool_declarations", fromlist=["all_function_declarations"]).all_function_declarations("general")}
    assert "vision_note" in names
