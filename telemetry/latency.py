"""
telemetry/latency.py - Tracker de latencia conversacional thread-safe.

Mide TTFB (time-to-first-audio-byte) y duracion de tools por turno, manteniendo
una ventana rodante para calcular p50/p95. Es ortogonal a TokenTracker (que
mide costo); aqui medimos UX percibida.

Eventos por turno:
  1. mark_user_end()      <- PTT release o VAD activity_end (usuario termino)
  2. mark_first_audio()   <- primer chunk de audio que llega de Gemini
  3. mark_turn_complete() <- server emite turn_complete (cierra el turno)

Tools que ocurren dentro del turno se registran con record_tool() y
quedan asociados al turno en curso.

Thread-safety: todos los marcadores publicos usan _lock. Los percentiles
se calculan sobre una copia inmutable para no bloquear lecturas largas.

Politica de turnos interrumpidos: si el usuario interrumpe (barge-in) antes
de que llegue first_audio, el turno se descarta. Si interrumpe despues, se
guarda con flag interrupted=True (sigue siendo dato valido de TTFB).
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


@dataclass
class TurnLatency:
    t_user_end_ms: float | None = None
    t_first_audio_ms: float | None = None
    t_turn_complete_ms: float | None = None
    tools: list[tuple[str, float]] = field(default_factory=list)
    interrupted: bool = False

    @property
    def ttfb_ms(self) -> float | None:
        if self.t_user_end_ms is None or self.t_first_audio_ms is None:
            return None
        return self.t_first_audio_ms - self.t_user_end_ms

    @property
    def speech_dur_ms(self) -> float | None:
        if self.t_first_audio_ms is None or self.t_turn_complete_ms is None:
            return None
        return self.t_turn_complete_ms - self.t_first_audio_ms

    @property
    def tools_total_ms(self) -> float:
        return sum(elapsed for _, elapsed in self.tools)


class LatencyTracker:
    """Ventana rodante de turnos con percentiles thread-safe."""

    def __init__(self, window: int = 50) -> None:
        self._lock = threading.Lock()
        self._turns: deque[TurnLatency] = deque(maxlen=window)
        self._current: TurnLatency | None = None

    # ---- Marcadores publicos (thread-safe) ----

    def mark_user_end(self) -> None:
        """Usuario termino su turno (PTT release o VAD end)."""
        with self._lock:
            # Si ya habia un turno abierto sin completar (raro: reconexion mid-turn),
            # lo descartamos. El nuevo turno arranca limpio.
            self._current = TurnLatency(t_user_end_ms=_now_ms())

    def mark_first_audio(self) -> None:
        """Primer chunk de audio de Gemini. Solo registra la primera vez por turno."""
        with self._lock:
            if self._current is None:
                return
            if self._current.t_first_audio_ms is not None:
                return  # ya marcado este turno
            self._current.t_first_audio_ms = _now_ms()

    def mark_turn_complete(self) -> TurnLatency | None:
        """Server emitio turn_complete. Cierra el turno y lo agrega a la ventana.

        Devuelve el TurnLatency cerrado (o None si no habia turno activo)
        para que el caller pueda loguearlo inmediatamente.
        """
        with self._lock:
            if self._current is None:
                return None
            self._current.t_turn_complete_ms = _now_ms()
            turn = self._current
            self._turns.append(turn)
            self._current = None
            return turn

    def mark_interrupted(self) -> None:
        """Barge-in: si llego first_audio, guardamos; si no, descartamos."""
        with self._lock:
            if self._current is None:
                return
            if self._current.t_first_audio_ms is None:
                # Interrumpido antes de oir nada: dato sin valor, descartar.
                self._current = None
                return
            self._current.interrupted = True
            self._current.t_turn_complete_ms = _now_ms()
            self._turns.append(self._current)
            self._current = None

    def record_tool(self, name: str, elapsed_ms: float) -> None:
        """Asocia un tool dispatch al turno en curso."""
        with self._lock:
            if self._current is None:
                return
            self._current.tools.append((name, float(elapsed_ms)))

    # ---- Consultas ----

    def percentiles(self) -> dict[str, float | int]:
        """p50/p95 de TTFB de los turnos completos en la ventana.

        Devuelve dict vacio-ish con n=0 si no hay datos suficientes.
        """
        with self._lock:
            snapshot = list(self._turns)
        ttfbs = [t.ttfb_ms for t in snapshot if t.ttfb_ms is not None]
        if not ttfbs:
            return {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}
        ttfbs_sorted = sorted(ttfbs)
        return {
            "n": len(ttfbs_sorted),
            "p50_ms": statistics.median(ttfbs_sorted),
            "p95_ms": _percentile(ttfbs_sorted, 0.95),
            "min_ms": ttfbs_sorted[0],
            "max_ms": ttfbs_sorted[-1],
        }

    def summary_line(self) -> str:
        """Una linea condensada para log de cierre de sesion."""
        p = self.percentiles()
        if p["n"] == 0:
            return "[LATENCY] sin turnos completos en esta sesion"
        return (
            f"[LATENCY SUMMARY] turns={p['n']} "
            f"p50={p['p50_ms']:.0f}ms p95={p['p95_ms']:.0f}ms "
            f"min={p['min_ms']:.0f}ms max={p['max_ms']:.0f}ms"
        )

    @staticmethod
    def format_turn(turn: TurnLatency) -> str:
        """Formato compacto de un turno individual para log por linea."""
        if turn.ttfb_ms is None:
            return f"[LATENCY] turn incompleto (interrupted={turn.interrupted})"
        parts = [f"TTFB={turn.ttfb_ms:.0f}ms"]
        if turn.speech_dur_ms is not None:
            parts.append(f"speech={turn.speech_dur_ms:.0f}ms")
        if turn.tools:
            tools_str = ",".join(f"{n}:{e:.0f}" for n, e in turn.tools)
            parts.append(f"tools=[{tools_str}]")
        if turn.interrupted:
            parts.append("interrupted")
        return "[LATENCY] " + " ".join(parts)


def _percentile(sorted_values: list[float], p: float) -> float:
    """Percentil simple por interpolacion lineal. Asume entrada ordenada."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)
