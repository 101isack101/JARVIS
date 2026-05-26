from actions.executor import SafeActionExecutor
from security.approvals import AutoApprovalBroker


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
