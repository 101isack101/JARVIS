"""
telemetry/budgets.py - Gates de budget con estados visuales y hard-stop.

Carga limits del .env y evalua el snapshot del tracker para decidir:
  - OK     ( 0-60% del budget): verde, todo bien
  - WARN   (60-80%): amarillo, log de aviso
  - ALERT  (80-95%): naranja, voz dice "estoy al 85% del budget de X"
  - BLOCKED (>=95% con hard-stop): rojo, NO permite nuevas invocaciones

Periodo configurable: session | daily | weekly. Por ahora, session-only es
lo que se usa en Fase 1; daily/weekly requieren persistence.py para sumar
historico.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .tracker import Snapshot, TokenTracker


class BudgetStatus(Enum):
    OK = "ok"
    WARN = "warn"
    ALERT = "alert"
    BLOCKED = "blocked"

    @property
    def color(self) -> str:
        """Color hex para tkinter."""
        return {
            BudgetStatus.OK: "#3ecf8e",       # verde
            BudgetStatus.WARN: "#facc15",     # amarillo
            BudgetStatus.ALERT: "#f97316",    # naranja
            BudgetStatus.BLOCKED: "#ef4444",  # rojo
        }[self]


@dataclass
class ProviderBudget:
    provider: str               # 'gemini' | 'claude'
    limit_usd: float
    spent_usd: float
    status: BudgetStatus
    pct: float                  # 0.0..1.0+ (puede pasarse de 1.0 si overshoot)
    blocked: bool


@dataclass
class BudgetReport:
    gemini: ProviderBudget
    claude: ProviderBudget
    period: str                 # 'session' | 'daily' | 'weekly'
    hard_stop: bool


def _status_for(pct: float, hard_stop: bool) -> BudgetStatus:
    if pct >= 0.95 and hard_stop:
        return BudgetStatus.BLOCKED
    if pct >= 0.80:
        return BudgetStatus.ALERT
    if pct >= 0.60:
        return BudgetStatus.WARN
    return BudgetStatus.OK


class BudgetGate:
    """Lee config del .env y evalua snapshots del tracker."""

    def __init__(
        self,
        gemini_limit_usd: float | None = None,
        claude_limit_usd: float | None = None,
        period: str | None = None,
        hard_stop: bool | None = None,
        history_provider_costs: Callable[[], dict[str, float]] | None = None,
    ) -> None:
        self.gemini_limit = (
            gemini_limit_usd
            if gemini_limit_usd is not None
            else float(os.environ.get("JARVIS_BUDGET_GEMINI_USD", "2.00"))
        )
        self.claude_limit = (
            claude_limit_usd
            if claude_limit_usd is not None
            else float(os.environ.get("JARVIS_BUDGET_CLAUDE_USD", "1.00"))
        )
        self.period = period or os.environ.get("JARVIS_BUDGET_PERIOD", "session")
        if hard_stop is not None:
            self.hard_stop = hard_stop
        else:
            self.hard_stop = (
                os.environ.get("JARVIS_BUDGET_HARD_STOP", "true").lower() == "true"
            )
        self.history_provider_costs = history_provider_costs

    def evaluate(self, tracker: TokenTracker) -> BudgetReport:
        """Genera reporte completo a partir del estado actual del tracker."""
        costs = tracker.cost_by_provider()
        if self.period != "session" and self.history_provider_costs:
            historic = self.history_provider_costs()
            for provider, spent in historic.items():
                costs[provider] = costs.get(provider, 0.0) + spent
        return BudgetReport(
            gemini=self._provider_budget("gemini", costs["gemini"], self.gemini_limit),
            claude=self._provider_budget("claude", costs["claude"], self.claude_limit),
            period=self.period,
            hard_stop=self.hard_stop,
        )

    def _provider_budget(self, name: str, spent: float, limit: float) -> ProviderBudget:
        pct = spent / limit if limit > 0 else 0.0
        status = _status_for(pct, self.hard_stop)
        return ProviderBudget(
            provider=name,
            limit_usd=limit,
            spent_usd=spent,
            status=status,
            pct=pct,
            blocked=(status == BudgetStatus.BLOCKED),
        )

    def can_invoke(self, tracker: TokenTracker, provider: str) -> bool:
        """Devuelve False si el provider esta BLOCKED (hard-stop activo y >=95%)."""
        report = self.evaluate(tracker)
        if provider == "gemini":
            return not report.gemini.blocked
        if provider == "claude":
            return not report.claude.blocked
        return True


# Smoke test
if __name__ == "__main__":
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    # Cargar .env del proyecto
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    tr = TokenTracker()
    gate = BudgetGate()

    print(f"Limites cargados:")
    print(f"  Gemini: ${gate.gemini_limit:.2f}")
    print(f"  Claude: ${gate.claude_limit:.2f}")
    print(f"  Periodo: {gate.period}")
    print(f"  Hard-stop: {gate.hard_stop}")

    # Test 1: estado inicial (todo OK)
    rep = gate.evaluate(tr)
    print(f"\n[Estado inicial] Gemini: {rep.gemini.status.value} ({rep.gemini.pct:.0%}), Claude: {rep.claude.status.value}")
    assert rep.gemini.status == BudgetStatus.OK

    # Test 2: simular gasto al 70% del budget Claude (WARN)
    target = gate.claude_limit * 0.70
    # Calculamos cuantos output_tokens necesitamos para llegar a $target
    # output Claude = $15/1M, so target/15*1M tokens output
    needed_out = int(target / 15.00 * 1_000_000)
    tr.record("claude-sonnet-4-6", output_tokens=needed_out)
    rep = gate.evaluate(tr)
    print(f"\n[Tras 70% Claude] status: {rep.claude.status.value} pct={rep.claude.pct:.0%}, color={rep.claude.status.color}")
    assert rep.claude.status == BudgetStatus.WARN

    # Test 3: simular gasto al 90% del budget Claude (ALERT)
    extra_out = int(gate.claude_limit * 0.20 / 15.00 * 1_000_000)
    tr.record("claude-sonnet-4-6", output_tokens=extra_out)
    rep = gate.evaluate(tr)
    print(f"[Tras 90% Claude] status: {rep.claude.status.value} pct={rep.claude.pct:.0%}, color={rep.claude.status.color}")
    assert rep.claude.status == BudgetStatus.ALERT

    # Test 4: simular gasto al 100% del budget Claude (BLOCKED si hard_stop)
    extra_out = int(gate.claude_limit * 0.10 / 15.00 * 1_000_000)
    tr.record("claude-sonnet-4-6", output_tokens=extra_out)
    rep = gate.evaluate(tr)
    print(f"[Tras 100% Claude] status: {rep.claude.status.value} pct={rep.claude.pct:.0%}, blocked={rep.claude.blocked}")
    if gate.hard_stop:
        assert rep.claude.status == BudgetStatus.BLOCKED
        assert not gate.can_invoke(tr, "claude")

    print("\n[OK] BudgetGate smoke test passed")
