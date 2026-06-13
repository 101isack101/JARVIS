"""
Tests de audio/playback.py — AudioPlayer, foco en el "grace" anti-flapping.

Validan SIN hardware llamando _callback directamente con un buffer falso:
  - on_underflow NO dispara en un hueco transitorio (< UNDERFLOW_GRACE_BLOCKS).
    Antes, cada vaciado momentaneo de la cola por jitter de red disparaba
    on_underflow -> en LIBRE eso reseteaba VAD/AEC y parpadeaba el estado.
  - on_underflow dispara UNA sola vez tras grace bloques vacios consecutivos.
  - un chunk en medio reinicia la racha (el hueco no cuenta como fin de turno).
"""
from __future__ import annotations

import numpy as np

from audio.playback import AudioPlayer, BLOCKSIZE, UNDERFLOW_GRACE_BLOCKS


def _fresh_outdata() -> np.ndarray:
    return np.zeros((BLOCKSIZE, 1), dtype=np.int16)


def _feed_one_chunk(player: AudioPlayer) -> None:
    """Empuja un chunk y lo 'reproduce' una vez via _callback (pone _playing=True)."""
    player.push(np.full(BLOCKSIZE, 1000, dtype=np.int16).tobytes())
    player._callback(_fresh_outdata(), BLOCKSIZE, None, None)


def test_underflow_no_dispara_en_hueco_transitorio() -> None:
    calls: list[int] = []
    player = AudioPlayer(on_underflow=lambda: calls.append(1))
    _feed_one_chunk(player)  # _playing = True

    # Huecos vacios por debajo del umbral: NO debe declararse fin de turno.
    for _ in range(UNDERFLOW_GRACE_BLOCKS - 1):
        player._callback(_fresh_outdata(), BLOCKSIZE, None, None)

    assert calls == [], "on_underflow no debe dispararse en un hueco transitorio"


def test_underflow_dispara_una_vez_tras_grace() -> None:
    calls: list[int] = []
    player = AudioPlayer(on_underflow=lambda: calls.append(1))
    _feed_one_chunk(player)

    # Justo al alcanzar el grace, dispara exactamente una vez.
    for _ in range(UNDERFLOW_GRACE_BLOCKS):
        player._callback(_fresh_outdata(), BLOCKSIZE, None, None)
    assert calls == [1], "debe dispararse una sola vez al alcanzar el grace"

    # Bloques vacios extra ya no re-disparan (no quedo _playing).
    for _ in range(UNDERFLOW_GRACE_BLOCKS * 2):
        player._callback(_fresh_outdata(), BLOCKSIZE, None, None)
    assert calls == [1], "no debe re-dispararse mientras no haya nuevo audio"


def test_chunk_intermedio_reinicia_la_racha() -> None:
    calls: list[int] = []
    player = AudioPlayer(on_underflow=lambda: calls.append(1))
    _feed_one_chunk(player)

    # Casi llegamos al grace...
    for _ in range(UNDERFLOW_GRACE_BLOCKS - 1):
        player._callback(_fresh_outdata(), BLOCKSIZE, None, None)
    # ...pero llega un chunk: la racha se reinicia (no fue fin de turno).
    _feed_one_chunk(player)
    for _ in range(UNDERFLOW_GRACE_BLOCKS - 1):
        player._callback(_fresh_outdata(), BLOCKSIZE, None, None)

    assert calls == [], "un chunk intermedio debe reiniciar la racha de vacios"


def test_on_playback_emite_far_end_real() -> None:
    """El callback far-end (referencia del AEC) recibe SOLO las muestras emitidas.

    Regresion para C1: el consumidor (jarvis._on_player_output) necesita estos
    bytes para alimentar el AEC. Aqui validamos que el player los emite bien;
    el bug de C1 era que el consumidor los tiraba por un NameError de numpy.
    """
    emitted: list[bytes] = []
    player = AudioPlayer(on_playback=lambda b: emitted.append(b))
    player.push(np.full(BLOCKSIZE, 1234, dtype=np.int16).tobytes())
    player._callback(_fresh_outdata(), BLOCKSIZE, None, None)

    assert len(emitted) == 1
    arr = np.frombuffer(emitted[0], dtype=np.int16)
    assert len(arr) == BLOCKSIZE
    assert int(arr[0]) == 1234
