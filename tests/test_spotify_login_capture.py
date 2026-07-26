from __future__ import annotations

from io import BytesIO

from actions import spotify_login_capture as login


def _handler(path: str):
    handler = object.__new__(login.CallbackHandler)
    statuses: list[int] = []
    handler.path = path
    handler.wfile = BytesIO()
    handler.send_response = statuses.append
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    return handler, statuses


def _reset(monkeypatch, state: str = "expected-state") -> None:
    monkeypatch.setattr(login, "_expected_state", state)
    login._captured.update(code=None, state=None, error=None)
    login._done_event.clear()


def test_oauth_callback_rejects_missing_or_mismatched_state(monkeypatch):
    _reset(monkeypatch)
    handler, statuses = _handler("/callback?code=foreign-code&state=wrong-state")

    handler.do_GET()

    assert statuses == [400]
    assert login._captured == {"code": None, "state": None, "error": "state_mismatch"}
    assert login._done_event.is_set()
    assert b"estado no coincide" in handler.wfile.getvalue()


def test_oauth_callback_accepts_only_the_expected_state(monkeypatch):
    _reset(monkeypatch)
    handler, statuses = _handler("/callback?code=valid-code&state=expected-state")

    handler.do_GET()

    assert statuses == [200]
    assert login._captured == {
        "code": "valid-code",
        "state": "expected-state",
        "error": None,
    }
    assert login._done_event.is_set()


def test_oauth_error_is_html_escaped(monkeypatch):
    _reset(monkeypatch)
    handler, statuses = _handler("/callback?error=%3Cscript%3E&state=expected-state")

    handler.do_GET()

    assert statuses == [200]
    assert b"&lt;script&gt;" in handler.wfile.getvalue()
