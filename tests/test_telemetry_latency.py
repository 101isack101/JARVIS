"""
Tests de telemetry/latency.py.

Cubren el contrato del LatencyTracker:
- happy path: user_end -> first_audio -> turn_complete calcula TTFB
- only-first guard: multiples mark_first_audio() solo cuentan el primero
- interrumpidos antes de first_audio: se descartan (no contaminan p50/p95)
- interrumpidos despues de first_audio: se guardan con flag interrupted
- ventana rodante: maxlen=N descarta turnos viejos
- percentiles vacios no rompen
- thread-safety: marcadores concurrentes no corrompen estado
"""
from __future__ import annotations

import threading
import time

import pytest

from telemetry.latency import LatencyTracker, TurnLatency


def _record_turn(tracker: LatencyTracker, ttfb_target_ms: float = 50.0) -> TurnLatency | None:
    """Helper: simula un turno completo con TTFB aproximado."""
    tracker.mark_user_end()
    time.sleep(ttfb_target_ms / 1000.0)
    tracker.mark_first_audio()
    time.sleep(0.01)
    return tracker.mark_turn_complete()


def test_happy_path_calcula_ttfb():
    tr = LatencyTracker()
    turn = _record_turn(tr, ttfb_target_ms=80)
    assert turn is not None
    assert turn.ttfb_ms is not None
    # Tolerancia: time.sleep no es preciso, esperamos +/- 30ms del target
    assert 50 < turn.ttfb_ms < 200, f"TTFB fuera de rango: {turn.ttfb_ms}ms"
    assert not turn.interrupted


def test_mark_first_audio_solo_cuenta_primera():
    tr = LatencyTracker()
    tr.mark_user_end()
    time.sleep(0.02)
    tr.mark_first_audio()
    first_ts = tr._current.t_first_audio_ms  # type: ignore[union-attr]
    time.sleep(0.05)
    tr.mark_first_audio()  # segundo chunk: NO debe sobrescribir
    assert tr._current.t_first_audio_ms == first_ts  # type: ignore[union-attr]


def test_interrumpido_antes_de_first_audio_se_descarta():
    tr = LatencyTracker()
    tr.mark_user_end()
    tr.mark_interrupted()  # sin first_audio
    assert tr._current is None
    p = tr.percentiles()
    assert p["n"] == 0


def test_interrumpido_despues_de_first_audio_se_guarda():
    tr = LatencyTracker()
    tr.mark_user_end()
    time.sleep(0.01)
    tr.mark_first_audio()
    tr.mark_interrupted()
    p = tr.percentiles()
    assert p["n"] == 1


def test_record_tool_asocia_al_turno_en_curso():
    tr = LatencyTracker()
    tr.mark_user_end()
    tr.record_tool("ask_claude_deep", 8420.0)
    tr.record_tool("jarvis_recall", 320.0)
    time.sleep(0.01)
    tr.mark_first_audio()
    turn = tr.mark_turn_complete()
    assert turn is not None
    assert len(turn.tools) == 2
    assert turn.tools[0] == ("ask_claude_deep", 8420.0)
    assert turn.tools_total_ms == pytest.approx(8740.0)


def test_record_tool_sin_turno_activo_no_lanza():
    tr = LatencyTracker()
    # No hay turno abierto: debe ser no-op silencioso
    tr.record_tool("ask_claude_deep", 100.0)
    # Y no debe crear turno fantasma
    assert tr._current is None


def test_ventana_rodante_descarta_turnos_viejos():
    tr = LatencyTracker(window=3)
    for _ in range(5):
        _record_turn(tr, ttfb_target_ms=20)
    p = tr.percentiles()
    assert p["n"] == 3  # solo los ultimos 3 sobreviven


def test_percentiles_vacios_devuelven_ceros():
    tr = LatencyTracker()
    p = tr.percentiles()
    assert p == {"n": 0, "p50_ms": 0.0, "p95_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0}


def test_percentiles_con_un_turno():
    tr = LatencyTracker()
    _record_turn(tr, ttfb_target_ms=50)
    p = tr.percentiles()
    assert p["n"] == 1
    # min == max == p50 con un solo dato
    assert p["min_ms"] == p["max_ms"] == p["p50_ms"]


def test_summary_line_sin_datos_es_descriptiva():
    tr = LatencyTracker()
    line = tr.summary_line()
    assert "sin turnos" in line.lower()


def test_summary_line_con_datos_incluye_metricas_clave():
    tr = LatencyTracker()
    _record_turn(tr, ttfb_target_ms=30)
    _record_turn(tr, ttfb_target_ms=60)
    line = tr.summary_line()
    assert "turns=2" in line
    assert "p50=" in line
    assert "p95=" in line


def test_format_turn_compacto_con_tools():
    turn = TurnLatency(
        t_user_end_ms=0.0,
        t_first_audio_ms=1500.0,
        t_turn_complete_ms=3000.0,
        tools=[("ask_claude_deep", 8420.0)],
    )
    s = LatencyTracker.format_turn(turn)
    assert "TTFB=1500ms" in s
    assert "speech=1500ms" in s
    assert "ask_claude_deep:8420" in s
    assert "interrupted" not in s


def test_format_turn_marca_interrumpidos():
    turn = TurnLatency(
        t_user_end_ms=0.0,
        t_first_audio_ms=1500.0,
        t_turn_complete_ms=2000.0,
        interrupted=True,
    )
    assert "interrupted" in LatencyTracker.format_turn(turn)


def test_mark_user_end_descarta_turno_previo_huerfano():
    """Si un turno se quedo a mitad (reconexion), arrancar uno nuevo lo descarta."""
    tr = LatencyTracker()
    tr.mark_user_end()
    tr.mark_first_audio()
    # Sin turn_complete, llega nuevo mark_user_end (raro pero posible)
    tr.mark_user_end()
    # El nuevo turno debe estar limpio
    assert tr._current is not None
    assert tr._current.t_first_audio_ms is None


def test_thread_safety_intra_turn_concurrent_records():
    """Dentro de un turno: mark_first_audio + record_tool concurrentes no corrompen.

    Refleja el invariante real de JARVIS: el lifecycle (mark_user_end /
    mark_turn_complete) es producido por un solo thread serializado (hotkey OR
    VAD, nunca ambos). PERO dentro del turno, el audio callback de sounddevice
    y los tool callbacks del asyncio loop disparan mark_first_audio y record_tool
    concurrentemente. Esos sí deben ser thread-safe.
    """
    tr = LatencyTracker(window=200)
    completed_turns = 0

    def audio_producer(stop_event):
        while not stop_event.is_set():
            tr.mark_first_audio()
            time.sleep(0.0001)

    def tool_producer(stop_event):
        i = 0
        while not stop_event.is_set():
            tr.record_tool(f"tool_{i % 3}", float(i))
            i += 1
            time.sleep(0.0001)

    stop = threading.Event()
    threads = [
        threading.Thread(target=audio_producer, args=(stop,)),
        threading.Thread(target=tool_producer, args=(stop,)),
    ]
    for t in threads:
        t.start()

    # Driver serializado: 30 turnos secuenciales mientras los productores
    # bombardean concurrentemente.
    for _ in range(30):
        tr.mark_user_end()
        time.sleep(0.005)  # ventana donde producers golpean
        turn = tr.mark_turn_complete()
        if turn is not None and turn.ttfb_ms is not None:
            completed_turns += 1

    stop.set()
    for t in threads:
        t.join(timeout=1.0)

    # Todos los turnos deberian estar completos (first_audio llego concurrente).
    p = tr.percentiles()
    assert p["n"] == 30
    assert completed_turns == 30
