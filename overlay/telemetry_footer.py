"""
overlay/telemetry_footer.py - Footer compacto con barras de budget.

Diseno: una linea horizontal al pie del overlay, ~22px alto, siempre visible.
Renderiza por modelo (Gemini + Claude) una barra de budget con codigo de color
y texto compacto: "Gemini 14.2k tok $0.42/$2.00".

Lee del TokenTracker via polling con root.after(500ms). No bloquea ni la UI ni
el thread de la sesion porque solo lee numeros atomicos.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from telemetry.budgets import BudgetGate, BudgetReport, BudgetStatus, ProviderBudget
from telemetry.tracker import TokenTracker

REFRESH_MS = 500
HEIGHT = 24
PAD_X = 8
BAR_WIDTH = 80
BAR_HEIGHT = 8

BG = "#0d1117"
FG = "#9ca3af"
FG_BRIGHT = "#e5e7eb"
TRACK = "#1f2937"


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


class TelemetryFooter(tk.Frame):
    """Footer thread-safe que lee tracker cada 500ms via root.after."""

    def __init__(
        self,
        master: tk.Misc,
        tracker: TokenTracker,
        gate: BudgetGate,
        on_blocked: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, bg=BG, height=HEIGHT)
        self.pack_propagate(False)
        self.tracker = tracker
        self.gate = gate
        self._on_blocked = on_blocked or (lambda _: None)
        self._already_blocked: set[str] = set()

        # Layout: [Gemini | bar | tokens | $] [Claude | bar | tokens | $] [Σ total]
        self.gemini_widgets = self._build_provider_block("Gemini")
        self.gemini_widgets["frame"].pack(side="left", padx=(PAD_X, 16))

        self.claude_widgets = self._build_provider_block("Claude")
        self.claude_widgets["frame"].pack(side="left", padx=(0, 16))

        self.total_label = tk.Label(
            self, text="Σ $0.00", bg=BG, fg=FG_BRIGHT,
            font=("Segoe UI", 9, "bold"),
        )
        self.total_label.pack(side="right", padx=(0, PAD_X))

        # Arrancar polling
        self.after(REFRESH_MS, self._refresh)

    def _build_provider_block(self, name: str) -> dict:
        frame = tk.Frame(self, bg=BG)
        label = tk.Label(
            frame, text=name, bg=BG, fg=FG,
            font=("Segoe UI", 9, "bold"), width=7, anchor="w",
        )
        label.pack(side="left")
        # Canvas para la barra de budget
        canvas = tk.Canvas(
            frame, width=BAR_WIDTH, height=BAR_HEIGHT,
            bg=BG, highlightthickness=0,
        )
        canvas.pack(side="left", padx=(2, 6))
        track_id = canvas.create_rectangle(
            0, 0, BAR_WIDTH, BAR_HEIGHT, fill=TRACK, outline="",
        )
        bar_id = canvas.create_rectangle(
            0, 0, 0, BAR_HEIGHT, fill="#3ecf8e", outline="",
        )
        info = tk.Label(
            frame, text="0 tok $0.00/$0.00", bg=BG, fg=FG,
            font=("Consolas", 9), anchor="w",
        )
        info.pack(side="left")
        return {
            "frame": frame,
            "label": label,
            "canvas": canvas,
            "track_id": track_id,
            "bar_id": bar_id,
            "info": info,
        }

    def _refresh(self) -> None:
        try:
            report = self.gate.evaluate(self.tracker)
            tokens_by = self.tracker.tokens_by_provider()
            self._render_provider(self.gemini_widgets, report.gemini, tokens_by["gemini"])
            self._render_provider(self.claude_widgets, report.claude, tokens_by["claude"])
            total = report.gemini.spent_usd + report.claude.spent_usd
            self.total_label.config(text=f"Σ ${total:.4f}")
            # Disparar callback de blocked solo en transicion (no spam)
            if report.gemini.blocked and "gemini" not in self._already_blocked:
                self._already_blocked.add("gemini")
                self._on_blocked("gemini")
            if report.claude.blocked and "claude" not in self._already_blocked:
                self._already_blocked.add("claude")
                self._on_blocked("claude")
        except Exception as exc:
            print(f"[footer] refresh error: {exc}")
        finally:
            self.after(REFRESH_MS, self._refresh)

    def _render_provider(self, w: dict, pb: ProviderBudget, tokens: int) -> None:
        # Texto compacto: "1.2k $0.42/$2.00". Se elimino "tok" porque la barra
        # ya visualiza el uso; tener "tok" ahi consumia ~30px de ancho que
        # cortaban el bloque de Claude en overlays <=560px.
        info_text = f"{_format_tokens(tokens):>5s} ${pb.spent_usd:>5.3f}/${pb.limit_usd:.2f}"
        w["info"].config(text=info_text)
        # Barra: largo proporcional, color segun estado
        fill_w = max(0, min(int(BAR_WIDTH * pb.pct), BAR_WIDTH))
        color = pb.status.color
        w["canvas"].coords(w["bar_id"], 0, 0, fill_w, BAR_HEIGHT)
        w["canvas"].itemconfig(w["bar_id"], fill=color)
        # Tinte el label del provider segun estado
        label_color = FG_BRIGHT if pb.status != BudgetStatus.OK else FG
        w["label"].config(fg=label_color)


# Smoke test: ventana con tracker simulado, gasto incrementando
if __name__ == "__main__":
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    root = tk.Tk()
    root.title("Telemetry footer test")
    root.geometry("700x80")
    root.configure(bg=BG)

    tracker = TokenTracker()
    # Limites bajos para ver gates en pocos segundos
    gate = BudgetGate(gemini_limit_usd=0.05, claude_limit_usd=0.05, hard_stop=False)

    label = tk.Label(root, text="Simulando uso. Vera transiciones de color verde -> amarillo -> naranja -> rojo.",
                     bg=BG, fg="#9ca3af")
    label.pack(pady=4)

    footer = TelemetryFooter(root, tracker, gate)
    footer.pack(side="bottom", fill="x")

    # Simular uso creciente
    def burn():
        tracker.record("claude-sonnet-4-6", output_tokens=300)
        tracker.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=2500)
        snap = tracker.snapshot()
        if snap.total_cost_usd < 0.15:
            root.after(400, burn)

    root.after(500, burn)
    print("[INFO] Cierra la ventana cuando quieras terminar")
    root.mainloop()
    print("[OK] TelemetryFooter smoke test passed")
