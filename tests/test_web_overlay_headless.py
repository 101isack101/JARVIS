"""Tests TDD para WebJarvisOverlay headless (Task 1.3).

Verifica que WebJarvisOverlay:
- No importa ni instancia tkinter en su codigo de produccion
- Expone overlay.after() via UiScheduler
- Sirve /state con uiToken
- Se cierra limpiamente

Todos estos tests deben FALLAR hasta completar la refactorizacion de web_overlay.py.
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import urllib.request
from unittest.mock import MagicMock, patch


def _make_overlay():
    """Construye un WebJarvisOverlay con mocks minimos (sin tkinter, sin browser)."""
    from telemetry.budgets import BudgetReport, BudgetStatus, ProviderBudget

    def _budget(provider: str) -> ProviderBudget:
        return ProviderBudget(
            provider=provider, limit_usd=2.0, spent_usd=0.0,
            status=BudgetStatus.OK, pct=0.0, blocked=False,
        )

    report = BudgetReport(
        gemini=_budget("gemini"), claude=_budget("claude"),
        period="session", hard_stop=False,
    )

    tracker = MagicMock()
    tracker.tokens_by_provider.return_value = {"gemini": 0, "claude": 0}

    gate = MagicMock()
    gate.evaluate.return_value = report
    gate.period = "session"
    gate.can_invoke.return_value = True

    import os
    os.environ.setdefault("JARVIS_WEB_UI_OPEN_BROWSER", "0")

    from overlay.web_overlay import WebJarvisOverlay

    overlay = WebJarvisOverlay(tracker=tracker, gate=gate)
    return overlay


def test_no_tkinter_import_in_web_overlay():
    """web_overlay.py no debe importar tkinter en absoluto."""
    import importlib
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "overlay.web_overlay_source",
        "overlay/web_overlay.py",
    )
    loader = spec.loader
    source = loader.get_source("overlay.web_overlay_source")
    assert "import tkinter" not in source, (
        "web_overlay.py todavia importa tkinter — debe eliminarse"
    )


def test_no_root_attribute():
    """WebJarvisOverlay no debe tener atributo self.root (es tk-especifico)."""
    overlay = _make_overlay()
    try:
        assert not hasattr(overlay, "root"), (
            "overlay.root todavia existe — debe eliminarse del modo headless"
        )
    finally:
        overlay.close()


def test_state_endpoint_returns_ui_token():
    """GET /state devuelve JSON con uiToken del overlay."""
    overlay = _make_overlay()
    try:
        url = overlay.url.rstrip("/") + "/state"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        assert "uiToken" in data, f"uiToken no en /state: {data.keys()}"
        assert data["uiToken"] == overlay.ui_token
    finally:
        overlay.close()


def test_after_method_exists_and_fires():
    """overlay.after(delay_ms, fn) dispara sin tkinter."""
    overlay = _make_overlay()
    try:
        fired = threading.Event()
        overlay.after(50, fired.set)
        assert fired.wait(timeout=1.0), "overlay.after() no disparo en 1s"
    finally:
        overlay.close()


def test_close_sets_closed_flag():
    """overlay.close() pone overlay.closed=True."""
    overlay = _make_overlay()
    assert not overlay.closed
    overlay.close()
    assert overlay.closed


def test_approval_auto_reject():
    """show_approval() auto-rechaza despues del timeout configurado."""
    overlay = _make_overlay()
    try:
        from overlay.web_overlay import ApprovalAction

        resolved: list[bool] = []

        def on_resolve(action_id: str, approved: bool) -> None:
            resolved.append(approved)

        action = ApprovalAction(
            id="test-001",
            tool="test_tool",
            args={},
            risk="low",
            timeout_s=0.3,
            title="Test Tool",
            details="args: {}",
        )
        overlay.show_approval(action, on_resolve)
        time.sleep(0.6)
        assert resolved == [False], f"auto-reject esperaba [False], got {resolved}"
    finally:
        overlay.close()


def test_set_state_emits_event():
    """set_state() emite evento SSE setState al cliente."""
    overlay = _make_overlay()
    try:
        client = overlay.register_client()
        overlay.set_state("thinking")
        # El evento debe aparecer en la cola
        payload_str = client.get(timeout=1.0)
        payload = json.loads(payload_str)
        assert payload["command"] == "setState", f"comando inesperado: {payload}"
        assert payload["args"][0] == "thinking"
    finally:
        overlay.close()


def test_camera_methods_no_tk():
    """camera_look/watch_start/watch_stop no lanzan ni intentan abrir ventana tk."""
    overlay = _make_overlay()
    try:
        # Estos metodos deben existir y no lanzar excepciones en modo headless
        overlay.camera_look()
        overlay.camera_watch_start()
        overlay.camera_watch_stop()
    finally:
        overlay.close()
