"""
telemetry/tracker.py - Acumulador thread-safe de uso de tokens y costos.

Diseñado para ser escrito desde el thread asyncio de la sesion Gemini Y
desde el thread del reasoner Claude, mientras la UI tkinter LEE
desde el main thread cada 500ms via root.after().

Sin colas async, sin awaits — solo locks atomicos sobre dict.
La UI hace .snapshot() y obtiene una copia inmutable que renderiza.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .costs import cost_usd


@dataclass
class ModelUsage:
    """Acumulado por (model, kind). kind libre: 'audio-in', 'audio-out', etc."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    events: int = 0
    last_event_ts: float = 0.0


@dataclass
class Snapshot:
    """Vista inmutable del tracker en un instante. Para la UI."""

    by_model: dict[str, ModelUsage]
    total_cost_usd: float
    session_start_ts: float
    snapshot_ts: float

    def session_duration_s(self) -> float:
        return self.snapshot_ts - self.session_start_ts

    def cache_hit_rate(self, model: str) -> float | None:
        """Para Claude: cache_read / (cache_read + cache_write + input). 0..1."""
        u = self.by_model.get(model)
        if not u:
            return None
        denom = u.cache_read_tokens + u.cache_write_tokens + u.input_tokens
        if denom == 0:
            return None
        return u.cache_read_tokens / denom


class TokenTracker:
    """Acumulador atomico para uso multi-modelo."""

    def __init__(self) -> None:
        self._by_model: dict[str, ModelUsage] = {}
        self._lock = threading.Lock()
        self._session_start = time.time()
        self._listeners: list[Callable[[Snapshot], None]] = []

    def record(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_write_tokens: int = 0,
        cache_read_tokens: int = 0,
    ) -> float:
        """Registra un evento de uso. Retorna el costo USD del evento.

        Thread-safe. Llamar desde cualquier thread (asyncio, mic callback, etc.)
        """
        delta_cost = cost_usd(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        with self._lock:
            usage = self._by_model.setdefault(model, ModelUsage())
            usage.input_tokens += input_tokens
            usage.output_tokens += output_tokens
            usage.cache_write_tokens += cache_write_tokens
            usage.cache_read_tokens += cache_read_tokens
            usage.cost_usd += delta_cost
            usage.events += 1
            usage.last_event_ts = time.time()
        # Notificar listeners FUERA del lock para no bloquear
        if self._listeners:
            snap = self.snapshot()
            for cb in self._listeners:
                try:
                    cb(snap)
                except Exception as exc:
                    print(f"[tracker] listener fallo: {exc}")
        return delta_cost

    def snapshot(self) -> Snapshot:
        """Copia atomica del estado actual. Safe para pasar a la UI."""
        with self._lock:
            by_model_copy = {
                model: ModelUsage(
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_write_tokens=u.cache_write_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cost_usd=u.cost_usd,
                    events=u.events,
                    last_event_ts=u.last_event_ts,
                )
                for model, u in self._by_model.items()
            }
            total = sum(u.cost_usd for u in by_model_copy.values())
        return Snapshot(
            by_model=by_model_copy,
            total_cost_usd=total,
            session_start_ts=self._session_start,
            snapshot_ts=time.time(),
        )

    def cost_by_provider(self) -> dict[str, float]:
        """Suma costos agrupando por proveedor (gemini, claude)."""
        snap = self.snapshot()
        result: dict[str, float] = {"gemini": 0.0, "claude": 0.0, "other": 0.0}
        for model, u in snap.by_model.items():
            if model.startswith("gemini"):
                result["gemini"] += u.cost_usd
            elif model.startswith("claude"):
                result["claude"] += u.cost_usd
            else:
                result["other"] += u.cost_usd
        return result

    def tokens_by_provider(self) -> dict[str, int]:
        """Suma de TODOS los tokens (in+out+cache) por proveedor."""
        snap = self.snapshot()
        result: dict[str, int] = {"gemini": 0, "claude": 0, "other": 0}
        for model, u in snap.by_model.items():
            tot = u.input_tokens + u.output_tokens + u.cache_write_tokens + u.cache_read_tokens
            if model.startswith("gemini"):
                result["gemini"] += tot
            elif model.startswith("claude"):
                result["claude"] += tot
            else:
                result["other"] += tot
        return result

    def add_listener(self, cb: Callable[[Snapshot], None]) -> None:
        self._listeners.append(cb)

    def reset(self) -> None:
        """Reinicia el tracker (nueva sesion)."""
        with self._lock:
            self._by_model.clear()
            self._session_start = time.time()


# Smoke test
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    tr = TokenTracker()

    # Simular evento Claude cold
    c1 = tr.record("claude-sonnet-4-6", input_tokens=42, output_tokens=178, cache_write_tokens=2053)
    print(f"Claude cold:  ${c1:.6f}")

    # Simular evento Claude warm
    c2 = tr.record("claude-sonnet-4-6", input_tokens=47, output_tokens=145, cache_read_tokens=2053)
    print(f"Claude warm:  ${c2:.6f}")

    # Simular evento Gemini audio
    c3 = tr.record("gemini-3.1-flash-live-preview:audio-in", input_tokens=1500)
    c4 = tr.record("gemini-3.1-flash-live-preview:audio-out", output_tokens=800)
    print(f"Gemini in/out: ${c3 + c4:.6f}")

    snap = tr.snapshot()
    print(f"\n=== Snapshot ===")
    print(f"Total session cost: ${snap.total_cost_usd:.6f}")
    print(f"Duration: {snap.session_duration_s():.2f}s")
    for m, u in snap.by_model.items():
        print(f"  {m}: events={u.events}, cost=${u.cost_usd:.6f}, "
              f"in={u.input_tokens}, out={u.output_tokens}, "
              f"cache_w={u.cache_write_tokens}, cache_r={u.cache_read_tokens}")

    print(f"\nCache hit rate Sonnet: {snap.cache_hit_rate('claude-sonnet-4-6'):.1%}")
    print(f"Cost by provider: {tr.cost_by_provider()}")
    print(f"Tokens by provider: {tr.tokens_by_provider()}")
    print("\n[OK] TokenTracker smoke test passed")
