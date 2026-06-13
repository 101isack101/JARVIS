from types import SimpleNamespace

from actions.executor import SafeActionExecutor
from security.approvals import AutoApprovalBroker


def _fake_proc(stdout="ok"):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_safe_action_executor_dry_run_allows_readonly(tmp_path):
    ex = SafeActionExecutor(root=tmp_path, mode="dev")

    result = ex.run_powershell("Get-ChildItem", cwd=str(tmp_path))

    assert result["allowed"] is True
    assert result["executed"] is False
    assert "dry-run" in result["stdout"]


def test_safe_action_executor_blocks_destructive(tmp_path):
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_powershell("Remove-Item important.txt", cwd=str(tmp_path))

    assert result["allowed"] is False
    assert result["executed"] is False


def test_safe_action_executor_hard_blocks_sendkeys_without_hitl(tmp_path):
    broker = AutoApprovalBroker(approve=True)
    ex = SafeActionExecutor(root=tmp_path, mode="prod", approval_broker=broker)

    result = ex.run_powershell(
        "(New-Object -ComObject WScript.Shell).SendKeys([char]179)",
        cwd=str(tmp_path),
    )

    assert result["allowed"] is False
    assert result["executed"] is False
    assert "automatizacion" in result["error"]
    assert broker.requests == []


def test_safe_action_executor_open_powershell_requires_hitl(tmp_path):
    ex = SafeActionExecutor(root=tmp_path, mode="dev")

    result = ex.open_powershell(cwd=str(tmp_path))

    assert result["allowed"] is False
    assert "aprobacion HITL" in result["error"]


def test_safe_action_executor_open_powershell_dry_run_when_approved(tmp_path):
    broker = AutoApprovalBroker(approve=True)
    ex = SafeActionExecutor(root=tmp_path, mode="dev", approval_broker=broker)

    result = ex.open_powershell(cwd=str(tmp_path))

    assert result["allowed"] is True
    assert result["executed"] is False
    assert "dry-run" in result["stdout"]
    assert broker.requests[0][0] == "open_terminal"


def test_safe_action_executor_open_url_validates_scheme(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url, new=0: opened.append((url, new)) or True)
    ex = SafeActionExecutor(root=tmp_path, mode="dev")

    ok = ex.open_url("example.com")
    blocked = ex.open_url("file:///C:/Windows")

    assert ok["allowed"] is True
    assert ok["executed"] is True
    assert opened == [("https://example.com", 2)]
    assert blocked["allowed"] is False


def test_structured_read_file_blocks_parent_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    ex = SafeActionExecutor(root=root, mode="prod")

    result = ex.run_structured("read_file", path="../outside.txt")

    assert result["allowed"] is False
    assert "fuera del root" in result["error"]


def test_structured_read_file_blocks_secret_paths(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN=abc", encoding="utf-8")
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_structured("read_file", path=".env")

    assert result["allowed"] is False
    assert "sensible" in result["error"]


def test_structured_read_file_reads_inside_project(tmp_path):
    note = tmp_path / "README.md"
    note.write_text("Jarvis docs", encoding="utf-8")
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_structured("read_file", path="README.md")

    assert result["ok"] is True
    assert result["content"] == "Jarvis docs"


def test_readonly_powershell_blocks_path_escape_outside_root(tmp_path, monkeypatch):
    """S1: un comando readonly con ruta que escapa del root NO debe ejecutarse."""
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "actions.executor.subprocess.run",
        lambda *a, **k: calls.append(a) or _fake_proc(),
    )
    ex = SafeActionExecutor(root=root, mode="prod")

    result = ex.run_powershell("Get-Content ../outside.txt", cwd=str(root))

    assert result["allowed"] is False
    assert result["executed"] is False
    assert calls == []  # el guard corta antes de ejecutar


def test_readonly_powershell_blocks_absolute_path_outside_root(tmp_path, monkeypatch):
    """S1: ruta absoluta fuera del root (ej. .ssh/id_rsa) bloqueada."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "id_rsa"
    outside.write_text("PRIVATE", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "actions.executor.subprocess.run",
        lambda *a, **k: calls.append(a) or _fake_proc(),
    )
    ex = SafeActionExecutor(root=root, mode="prod")

    result = ex.run_powershell(f"Get-Content {outside}", cwd=str(root))

    assert result["allowed"] is False
    assert result["executed"] is False
    assert calls == []


def test_readonly_powershell_blocks_secret_filename(tmp_path, monkeypatch):
    """S1: leer un archivo sensible (.env) por fast-path readonly bloqueado."""
    (tmp_path / ".env").write_text("TOKEN=abc", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "actions.executor.subprocess.run",
        lambda *a, **k: calls.append(a) or _fake_proc(),
    )
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_powershell("Get-Content .env", cwd=str(tmp_path))

    assert result["allowed"] is False
    assert result["executed"] is False
    assert calls == []


def test_readonly_powershell_allows_inproject_path(tmp_path, monkeypatch):
    """Regresion: un readonly con ruta dentro del root sigue ejecutando."""
    (tmp_path / "README.md").write_text("docs", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        "actions.executor.subprocess.run",
        lambda *a, **k: calls.append(a) or _fake_proc("docs"),
    )
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_powershell("Get-Content README.md", cwd=str(tmp_path))

    assert result["allowed"] is True
    assert result["executed"] is True
    assert len(calls) == 1


def test_structured_search_text_skips_secret_paths(tmp_path):
    (tmp_path / "notes.md").write_text("visible needle", encoding="utf-8")
    (tmp_path / "token_notes.md").write_text("hidden needle", encoding="utf-8")
    ex = SafeActionExecutor(root=tmp_path, mode="prod")

    result = ex.run_structured("search_text", query="needle")

    assert result["ok"] is True
    assert result["matches"] == [{"path": "notes.md", "line": 1, "text": "visible needle"}]
