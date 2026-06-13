from telemetry.budgets import BudgetStatus, ProviderBudget
from types import SimpleNamespace
from overlay.web_overlay import (
    WebJarvisOverlay,
    _BridgeHandler,
    _audio_emit_interval_from_env,
    _provider_payload,
)


def test_audio_level_from_pcm_tracks_energy():
    quiet = (100).to_bytes(2, "little", signed=True) * 256
    loud = (12000).to_bytes(2, "little", signed=True) * 256

    assert WebJarvisOverlay.audio_level_from_pcm(b"") == 0.05
    assert WebJarvisOverlay.audio_level_from_pcm(loud) > WebJarvisOverlay.audio_level_from_pcm(quiet)
    assert 0.05 <= WebJarvisOverlay.audio_level_from_pcm(loud) <= 1.0


def test_provider_payload_caps_width_and_formats_label():
    budget = ProviderBudget(
        provider="gemini",
        limit_usd=2.0,
        spent_usd=3.0,
        status=BudgetStatus.BLOCKED,
        pct=1.5,
        blocked=True,
    )

    payload = _provider_payload(budget, tokens=1530)

    assert payload["pct"] == 1.0
    assert payload["status"] == "blocked"
    assert payload["tokensLabel"] == "1.5k"
    assert "$3.000/$2.00" in payload["label"]


def test_audio_visual_interval_defaults_to_30_fps(monkeypatch):
    monkeypatch.delenv("JARVIS_WEB_UI_AUDIO_FPS", raising=False)
    assert _audio_emit_interval_from_env() == 1 / 30

    monkeypatch.setenv("JARVIS_WEB_UI_AUDIO_FPS", "60")
    assert _audio_emit_interval_from_env() == 1 / 60

    monkeypatch.setenv("JARVIS_WEB_UI_AUDIO_FPS", "0")
    assert _audio_emit_interval_from_env() == 1

    monkeypatch.setenv("JARVIS_WEB_UI_AUDIO_FPS", "invalid")
    assert _audio_emit_interval_from_env() == 1 / 30


def test_bridge_post_authorization_requires_session_token():
    handler = object.__new__(_BridgeHandler)
    handler.server = SimpleNamespace(overlay=SimpleNamespace(ui_token="secret-token"))
    handler.headers = {}

    assert handler._authorized({}) is False
    assert handler._authorized({"uiToken": "secret-token"}) is True

    handler.headers = {"X-Jarvis-Ui-Token": "secret-token"}
    assert handler._authorized({}) is True
