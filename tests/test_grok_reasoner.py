import json
from types import SimpleNamespace

from grok.reasoner import GrokReasoner, ReasonerResponse, grok_cli_access_token, resolve_xai_api_key
from telemetry.tracker import TokenTracker


class FakeCompletions:
    def __init__(self, fail_times=0):
        self.calls = 0
        self.fail_times = fail_times

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            exc = RuntimeError("rate")
            exc.status_code = 429
            raise exc
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hola senor"))],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=4,
                prompt_tokens_details=SimpleNamespace(cached_tokens=2),
            ),
        )


def test_grok_reasoner_records_usage_and_text():
    completions = FakeCompletions()
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    tracker = TokenTracker()
    reasoner = GrokReasoner(api_key="xai-test", tracker=tracker, client=client)
    result = reasoner.ask("ping", max_tokens=128)
    assert isinstance(result, ReasonerResponse)
    assert result.text == "hola senor"
    assert result.input_tokens == 10
    assert result.cache_read_tokens == 2
    assert result.cost_usd > 0
    assert completions.calls == 1


def test_resolve_xai_prefers_durable_xai_prefix(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"x": {"key": "eyJhbGciOiJ.test", "auth_mode": "oidc"}}), encoding="utf-8")
    monkeypatch.setenv("JARVIS_GROK_AUTH_PATH", str(auth))
    monkeypatch.setenv("XAI_API_KEY", "xai-durable-console-key")
    assert resolve_xai_api_key() == "xai-durable-console-key"


def test_grok_cli_token_skips_expired_entries(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({
            "old": {"key": "expired-token", "expires_at": "2020-01-01T00:00:00Z", "auth_mode": "oidc"},
            "live": {"key": "live-token", "expires_at": "2099-01-01T00:00:00Z", "auth_mode": "oidc"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_GROK_AUTH_PATH", str(auth))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert grok_cli_access_token() == "live-token"
    assert resolve_xai_api_key() == "live-token"


def test_grok_reasoner_retries_on_429():
    completions = FakeCompletions(fail_times=1)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    reasoner = GrokReasoner(api_key="xai-test", client=client)
    result = reasoner.ask("ping")
    assert result.text == "hola senor"
    assert completions.calls == 2
