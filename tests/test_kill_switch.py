from security import kill_switch


def test_hard_exit_calls_os_exit(monkeypatch):
    called = {}

    def fake_exit(code):
        called["code"] = code

    monkeypatch.setattr(kill_switch.os, "_exit", fake_exit)

    kill_switch.hard_exit(130)

    assert called["code"] == 130
