"""
overlay/telemetry_footer.py - Footer compacto con barras de budget.

Diseno: una linea horizontal al pie del overlay, ~30px alto, siempre visible.
Renderiza por modelo (Gemini + Claude) una barra de budget con codigo de color
y texto compacto: "Gemini 14.2k $0.42/$2.00".

Lee del TokenTracker via polling con root.after(500ms). No bloquea ni la UI ni
el thread de la sesion porque solo lee numeros atomicos.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from overlay.ui_theme import BG, BORDER_SOFT, FONT_DISPLAY, FONT_MONO, FONT_UI, TEXT_DIM, TEXT_PRIMARY
from telemetry.budgets import BudgetGate, BudgetReport, BudgetStatus, ProviderBudget
from telemetry.tracker import TokenTracker

REFRESH_MS = 500
HEIGHT = 30
PAD_X = 12
BAR_WIDTH = 92
BAR_HEIGHT = 6
TRACK = BORDER_SOFT


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
        self._closed = False
        self._after_id: str | None = None

        # Layout: [Gemini | bar | tokens | $] [Claude | bar | tokens | $] [total]
        self.gemini_widgets = self._build_provider_block("Gemini")
        self.gemini_widgets["frame"].pack(side="left", padx=(PAD_X, 16))

        self.claude_widgets = self._build_provider_block("Claude")
        self.claude_widgets["frame"].pack(side="left", padx=(0, 16))

        self.total_label = tk.Label(
            self, text="Total $0.00", bg=BG, fg=TEXT_PRIMARY,
            font=(FONT_DISPLAY, 9, "bold"),
        )
        self.total_label.pack(side="right", padx=(0, PAD_X))

        self._schedule_refresh()

    def stop(self) -> None:
        self._closed = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _schedule_refresh(self) -> None:
        if self._closed:
            return
        try:
            self._after_id = self.after(REFRESH_MS, self._refresh)
        except Exception:
            self._after_id = None

    def _build_provider_block(self, name: str) -> dict:
        frame = tk.Frame(self, bg=BG)
        label = tk.Label(
            frame, text=name, bg=BG, fg=TEXT_DIM,
            font=(FONT_DISPLAY, 8, "bold"), width=7, anchor="w",
        )
        label.pack(side="left")

        canvas = tk.Canvas(
            frame, width=BAR_WIDTH, height=BAR_HEIGHT,
            bg=BG, highlightthickness=0,
        )
        canvas.pack(side="left", padx=(2, 6))
        track_id = canvas.create_line(
            2,
            BAR_HEIGHT / 2,
            BAR_WIDTH - 2,
            BAR_HEIGHT / 2,
            fill=TRACK,
            width=BAR_HEIGHT,
            capstyle="round",
        )
        bar_id = canvas.create_line(
            2,
            BAR_HEIGHT / 2,
            2,
            BAR_HEIGHT / 2,
            fill="#9cf5d5",
            width=BAR_HEIGHT,
            capstyle="round",
        )
        info = tk.Label(
            frame, text="0 $0.00/$0.00", bg=BG, fg=TEXT_DIM,
            font=(FONT_MONO, 9), anchor="w",
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
        if self._closed:
            return
        self._after_id = None
        try:
            report = self.gate.evaluate(self.tracker)
            tokens_by = self.tracker.tokens_by_provider()
            self._render_provider(self.gemini_widgets, report.gemini, tokens_by["gemini"])
            self._render_provider(self.claude_widgets, report.claude, tokens_by["claude"])
            total = report.gemini.spent_usd + report.claude.spent_usd
            self.total_label.config(text=f"Total ${total:.4f}")
            if report.gemini.blocked and "gemini" not in self._already_blocked:
                self._already_blocked.add("gemini")
                self._on_blocked("gemini")
            if report.claude.blocked and "claude" not in self._already_blocked:
                self._already_blocked.add("claude")
                self._on_blocked("claude")
        except Exception as exc:
            print(f"[footer] refresh error: {exc}")
        finally:
            self._schedule_refresh()

    def _render_provider(self, w: dict, pb: ProviderBudget, tokens: int) -> None:
        info_text = f"{_format_tokens(tokens):>5s} ${pb.spent_usd:>5.3f}/${pb.limit_usd:.2f}"
        w["info"].config(text=info_text)
        fill_w = max(0, min(int(BAR_WIDTH * pb.pct), BAR_WIDTH))
        color = pb.status.color
        fill_x = max(2, min(BAR_WIDTH - 2, fill_w))
        w["canvas"].coords(w["bar_id"], 2, BAR_HEIGHT / 2, fill_x, BAR_HEIGHT / 2)
        w["canvas"].itemconfig(w["bar_id"], fill=color)
        label_color = TEXT_PRIMARY if pb.status != BudgetStatus.OK else TEXT_DIM
        w["label"].config(fg=label_color)


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
    gate = BudgetGate(gemini_limit_usd=0.05, claude_limit_usd=0.05, hard_stop=False)

    label = tk.Label(
        root,
        text="Simulando uso. Veras transiciones de color verde -> amarillo -> naranja -> rojo.",
        bg=BG,
        fg="#9ca3af",
    )
    label.pack(pady=4)

    footer = TelemetryFooter(root, tracker, gate)
    footer.pack(side="bottom", fill="x")

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
