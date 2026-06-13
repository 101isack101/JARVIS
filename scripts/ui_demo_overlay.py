"""Launch a visual demo of the Jarvis overlay UI without starting full Jarvis."""

from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from security.approvals import PendingAction
from overlay.window import JarvisOverlay
from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker


def main() -> None:
    tracker = TokenTracker()
    gate = BudgetGate(gemini_limit_usd=2.00, claude_limit_usd=2.00, hard_stop=False)
    overlay = JarvisOverlay(tracker, gate)

    overlay.append_input("Jarvis, resume mi estado actual y revisa memoria.")
    overlay.append_output(
        "Listo. Estoy conectado, con memoria observable y Command Center "
        "preparado para revisar actividad local."
    )
    overlay.set_connection_status("connected")
    overlay.set_mode("LIBRE")
    overlay.record_memory_tool_start("jarvis_recall", {"query": "planes UI Jarvis", "top_k": 3})
    overlay.record_memory_tool_end(
        "jarvis_recall",
        86.0,
        True,
        {"found": 2, "results": [{"title": "UI quick wins"}, {"title": "Command Center"}]},
    )
    tracker.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=2200)
    tracker.record("claude-sonnet-4-6", input_tokens=900, output_tokens=320)
    tick = {"i": 0}

    def show_center() -> None:
        overlay.open_dashboard()
        overlay.command_center.select_tab("overview")

    def show_approval() -> None:
        action = PendingAction(
            id="demo-action",
            risk="destructive",
            title="Eliminar archivo temporal de prueba",
            details=(
                "Demo visual: esta aprobacion simula una accion sensible. "
                "No ejecuta cambios reales."
            ),
            timeout_s=30,
        )
        overlay.show_approval(action, lambda *_: None)

    def animate_voice() -> None:
        tick["i"] += 1
        overlay.set_state("speaking")
        amp = 0.18 + 0.62 * abs(math.sin(tick["i"] * 0.23))
        samples = []
        for i in range(720):
            value = int(math.sin((i + tick["i"] * 17) * 0.18) * 21000 * amp)
            samples.append(value.to_bytes(2, "little", signed=True))
        overlay.feed_voice_audio(b"".join(samples))
        if tick["i"] < 260:
            overlay.root.after(55, animate_voice)
        else:
            overlay.set_state("idle")

    def preview_listening() -> None:
        overlay.set_state("listening")
        overlay.log_event("Escucha activa", "ok")

    def preview_thinking() -> None:
        overlay.set_state("thinking")
        overlay.log_event("Analizando contexto", "warn")

    overlay.root.after(500, show_center)
    overlay.root.after(900, preview_listening)
    overlay.root.after(2400, preview_thinking)
    overlay.root.after(3900, animate_voice)
    overlay.root.after(7600, show_approval)
    overlay.root.after(120000, overlay.close)
    overlay.run()


if __name__ == "__main__":
    main()
