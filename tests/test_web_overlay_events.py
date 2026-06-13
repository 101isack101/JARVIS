"""Eventos ampliados del bridge web: tools, audio telemetry y latencia."""

from __future__ import annotations

import pytest

from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker


@pytest.fixture()
def overlay(monkeypatch):
    monkeypatch.setenv("JARVIS_WEB_UI_PORT", "0")
    monkeypatch.setenv("JARVIS_WEB_UI_OPEN_BROWSER", "false")
    from overlay.web_overlay import WebJarvisOverlay

    ov = WebJarvisOverlay(TokenTracker(), BudgetGate())
    yield ov
    ov.close()


def test_record_tool_start_and_end_tracks_any_tool(overlay):
    overlay.record_tool_start("ask_claude_deep", {"prompt": "hola"})
    events = overlay.agent_events
    started = events[-1]
    assert events[-1]["name"] == "ask_claude_deep"
    assert events[-1]["status"] == "running"
    assert "hola" in events[-1]["summary"]
    assert started["id"]
    assert isinstance(started["startedAt"], int)
    assert started["endedAt"] is None

    overlay.record_tool_end("ask_claude_deep", 1234.5, True, {"ok": True})
    events = overlay.agent_events
    assert events[-1]["name"] == "ask_claude_deep"
    assert events[-1]["status"] == "ok"
    assert events[-1]["elapsedMs"] == pytest.approx(1234.5)
    assert events[-1]["id"] == started["id"]
    assert events[-1]["startedAt"] == started["startedAt"]
    assert isinstance(events[-1]["endedAt"], int)
    assert events[-1]["endedAt"] >= started["startedAt"]


def test_record_tool_delegates_to_memory_panel_for_memory_tools(overlay):
    overlay.record_tool_start("jarvis_recall", {"query": "x"})
    assert overlay.memory_events[-1]["status"] == "running"

    overlay.record_tool_end("jarvis_recall", 10.0, True, {"found": 1})
    assert overlay.memory_events[-1]["status"] == "ok"


def test_record_audio_telemetry_stored_and_in_snapshot(overlay):
    overlay.record_audio_telemetry({"erlePeakDb": 24.3, "wakewordPeak": 0.41})
    snap = overlay.snapshot()
    assert snap["audioTelemetry"]["erlePeakDb"] == 24.3
    assert snap["audioTelemetry"]["wakewordPeak"] == 0.41
    assert snap["audioTelemetry"]["stamp"]


def test_record_turn_latency_keeps_recent_lines(overlay):
    for i in range(25):
        overlay.record_turn_latency(f"turn {i}: ttfb=500ms")

    snap = overlay.snapshot()
    assert len(snap["latency"]) == 20
    assert "turn 5" in snap["latency"][0]
    assert "turn 24" in snap["latency"][-1]


def test_snapshot_includes_agent_events(overlay):
    overlay.record_tool_start("spotify_play", {"track": "a"})
    snap = overlay.snapshot()
    assert any(e["name"] == "spotify_play" for e in snap["agentEvents"])


def test_camera_frame_is_kept_in_snapshot_and_cleared_on_stop(overlay):
    from types import SimpleNamespace

    overlay.update_camera_preview(SimpleNamespace(jpeg_bytes=b"jpeg"))

    snap = overlay.snapshot()
    assert snap["cameraActive"] is True
    assert snap["cameraFrame"] == "anBlZw=="

    overlay.set_camera_focus([1, 2, 3, 4], "obj")
    assert overlay.snapshot()["cameraFocus"]["label"] == "obj"

    overlay.set_camera_active(False)
    snap = overlay.snapshot()
    assert snap["cameraActive"] is False
    assert snap["cameraFrame"] is None
    assert snap["cameraFocus"] is None
