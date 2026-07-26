from io import BytesIO

from telemetry.budgets import BudgetGate, BudgetStatus, ProviderBudget
from telemetry.tracker import TokenTracker
from types import SimpleNamespace
from overlay.web_overlay import (
    WebJarvisOverlay,
    _BridgeHandler,
    MAX_JSON_REQUEST_BYTES,
    _audio_emit_interval_from_env,
    _camera_emit_interval_from_env,
    _provider_payload,
    resolve_web_dir,
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


def test_camera_preview_interval_uses_camera_fps(monkeypatch):
    monkeypatch.delenv("JARVIS_CAMERA_PREVIEW_FPS", raising=False)
    monkeypatch.setenv("JARVIS_CAMERA_FPS", "10.0")
    assert _camera_emit_interval_from_env() == 0.1

    monkeypatch.setenv("JARVIS_CAMERA_PREVIEW_FPS", "20")
    assert _camera_emit_interval_from_env() == 1 / 15

    monkeypatch.setenv("JARVIS_CAMERA_PREVIEW_FPS", "invalid")
    assert _camera_emit_interval_from_env() == 0.25


def test_resolve_web_dir_honors_env_override(tmp_path, monkeypatch):
    web_dir = tmp_path / "ui"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setenv("JARVIS_WEB_UI_DIR", str(web_dir))

    assert resolve_web_dir() == web_dir


def test_bridge_post_authorization_requires_session_token():
    handler = object.__new__(_BridgeHandler)
    handler.server = SimpleNamespace(overlay=SimpleNamespace(ui_token="secret-token"))
    handler.headers = {}

    assert handler._authorized({}) is False
    assert handler._authorized({"uiToken": "secret-token"}) is True

    handler.headers = {"X-Jarvis-Ui-Token": "secret-token"}
    assert handler._authorized({}) is True


def test_bridge_rejects_non_loopback_host_header():
    """DNS rebinding: /state entrega el uiToken, asi que el Host es la barrera."""
    handler = object.__new__(_BridgeHandler)
    handler.server = SimpleNamespace(server_address=("127.0.0.1", 8765))

    for bad in ("", "evil.com", "evil.com:8765", "127.0.0.1.evil.com:8765"):
        handler.headers = {"Host": bad} if bad else {}
        assert handler._host_allowed() is False, bad

    for good in ("127.0.0.1:8765", "localhost:8765", "127.0.0.1", "[::1]:8765"):
        handler.headers = {"Host": good}
        assert handler._host_allowed() is True, good


def test_bridge_allows_host_matching_explicit_bind_address():
    handler = object.__new__(_BridgeHandler)
    handler.server = SimpleNamespace(server_address=("192.168.1.50", 8765))
    handler.headers = {"Host": "192.168.1.50:8765"}
    assert handler._host_allowed() is True


def test_bridge_json_body_rejects_invalid_or_oversized_content_length():
    handler = object.__new__(_BridgeHandler)

    handler.headers = {"Content-Length": "not-a-number"}
    handler.rfile = BytesIO(b'{"command":"close"}')
    assert handler._read_json() == {}

    body = b"x" * (MAX_JSON_REQUEST_BYTES + 1)
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    assert handler._read_json() == {}
    assert handler.rfile.tell() == 0


def test_export_conversation_writes_markdown(tmp_path, monkeypatch):
    from tests.test_web_overlay_headless import _make_overlay

    monkeypatch.setenv("JARVIS_CONVERSATION_EXPORT_DIR", str(tmp_path))
    overlay = _make_overlay()
    try:
        overlay.append_input("Hola Jarvis")
        overlay.append_output("Hola Isaac")

        path = overlay.export_conversation()

        assert path is not None
        assert path.parent == tmp_path
        text = path.read_text(encoding="utf-8")
        assert "# Conversacion JARVIS" in text
        assert "## Isaac" in text
        assert "Hola Jarvis" in text
        assert "## JARVIS" in text
        assert "Hola Isaac" in text
    finally:
        overlay.close()


def test_runtime_telemetry_reports_cost_tokens_and_camera_bandwidth(monkeypatch):
    monkeypatch.setenv("JARVIS_WEB_UI_PORT", "0")
    monkeypatch.setenv("JARVIS_WEB_UI_OPEN_BROWSER", "false")
    overlay = WebJarvisOverlay(TokenTracker(), BudgetGate())
    try:
        overlay.tracker.record("gemini-3.1-flash-live-preview:text-in", input_tokens=1200)
        overlay.tracker.record("claude-sonnet-4-6", output_tokens=200)
        overlay.update_camera_preview(SimpleNamespace(jpeg_bytes=b"x" * 4096))

        payload = overlay._runtime_telemetry_payload()

        assert payload["totalTokens"] >= 1400
        assert payload["totalUsd"] > 0
        assert payload["gemini"]["tokens"] >= 1200
        assert payload["claude"]["tokens"] >= 200
        assert payload["camera"]["streamFrames"] == 1
        assert payload["camera"]["streamKbps"] > 0
        assert payload["camera"]["streamMb"] > 0
    finally:
        overlay.close()
