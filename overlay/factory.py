"""Overlay selection for Jarvis."""

from __future__ import annotations

import os
from typing import Callable

from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker


def create_overlay(
    tracker: TokenTracker,
    gate: BudgetGate,
    on_close: Callable[[], None] | None = None,
):
    """Create the configured overlay implementation.

    JARVIS_UI=tk uses the classic Tkinter overlay. JARVIS_UI=web enables the
    premium browser UI explicitly.
    """

    ui = os.environ.get("JARVIS_UI", os.environ.get("JARVIS_OVERLAY", "tk")).strip().lower()
    if ui in {"web", "browser", "premium"}:
        try:
            from overlay.web_overlay import WebJarvisOverlay

            return WebJarvisOverlay(tracker, gate, on_close=on_close)
        except Exception as exc:
            print(f"[overlay] Web UI no disponible, usando tkinter: {type(exc).__name__}: {exc}")

    from overlay.window import JarvisOverlay

    return JarvisOverlay(tracker, gate, on_close=on_close)
