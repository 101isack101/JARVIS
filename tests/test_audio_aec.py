"""
Tests de audio/aec.py — EchoCanceller (PBFDAF) y AECStream.

Validan el algoritmo OFFLINE con eco sintetico (sin hardware):
  - ERLE: en eco-puro cancela bien (>15 dB tras converger).
  - Double-talk: el caso real del barge-in (Isaac habla mientras Jarvis habla).
    Tras converger en eco-puro, cuando entra la voz cercana el AEC debe quitar
    el eco y PRESERVAR la voz de Isaac (no cancelarla).
  - AECStream: streaming int16 thread-safe, reduce eco end-to-end.
"""
from __future__ import annotations

import numpy as np
import pytest

from audio.aec import EchoCanceller, AECStream


def _make_echo_path(n: int, far: np.ndarray) -> np.ndarray:
    """Eco sintetico: retardo + decaimiento (respuesta impulso de sala)."""
    delay = 120
    ir = np.zeros(400)
    ir[delay] = 0.8
    ir[delay + 40] = 0.4
    ir[delay + 120] = 0.2
    ir *= np.exp(-np.arange(400) / 150.0)
    return np.convolve(far, ir, mode="full")[:n]


def _run(aec: EchoCanceller, near: np.ndarray, far: np.ndarray) -> np.ndarray:
    N = aec.N
    out = np.zeros(len(near), dtype=np.float64)
    for i in range(0, len(near) - N, N):
        out[i : i + N] = aec.process(near[i : i + N], far[i : i + N])
    return out


def test_erle_eco_puro():
    rng = np.random.default_rng(1)
    n = 16000 * 6
    far = np.convolve(rng.standard_normal(n), np.ones(8) / 8, mode="same")
    far /= np.max(np.abs(far))
    near = _make_echo_path(n, far)

    aec = EchoCanceller(block=512, partitions=8, mu=0.4)
    cleaned = _run(aec, near, far)

    half = n // 2
    erle = 10 * np.log10(np.mean(near[half:] ** 2) / (np.mean(cleaned[half:] ** 2) + 1e-12))
    assert erle > 15.0, f"ERLE bajo: {erle:.1f} dB"


def test_double_talk_preserva_voz_cercana():
    """Tras converger en eco-puro, al entrar la voz cercana el AEC debe dejar
    pasar la voz (recuperarla) y seguir quitando el eco."""
    rng = np.random.default_rng(2)
    fs = 16000
    n = fs * 8
    far = np.convolve(rng.standard_normal(n), np.ones(8) / 8, mode="same")
    far /= np.max(np.abs(far))
    echo = _make_echo_path(n, far)

    # Voz cercana (independiente) SOLO en la 2da mitad (Isaac entra tarde).
    near_voice = np.zeros(n)
    start = n // 2
    v = np.convolve(rng.standard_normal(n - start), np.ones(5) / 5, mode="same")
    near_voice[start:] = 0.5 * v / np.max(np.abs(v))

    near = echo + near_voice

    aec = EchoCanceller(block=512, partitions=8, mu=0.3)
    cleaned = _run(aec, near, far)

    # En la zona de double-talk (ya convergido), el cleaned debe parecerse mucho
    # mas a la voz cercana que el near sucio. Medimos error contra near_voice.
    seg = slice(start + fs, n - 512)  # 1s despues de que entra la voz
    err_in = np.mean((near[seg] - near_voice[seg]) ** 2)      # = eco presente
    err_out = np.mean((cleaned[seg] - near_voice[seg]) ** 2)  # residual tras AEC
    improvement_db = 10 * np.log10(err_in / (err_out + 1e-12))
    # Con DTD el filtro converge en eco-puro y se congela al entrar la voz,
    # logrando ~20 dB de reduccion de eco sin cancelar a Isaac.
    assert improvement_db > 15.0, f"poca mejora en double-talk: {improvement_db:.1f} dB"

    # Y la energia de la voz cercana debe conservarse (no cancelada): el cleaned
    # no debe ser mucho mas debil que la voz real.
    voz_pow = np.mean(near_voice[seg] ** 2)
    clean_pow = np.mean(cleaned[seg] ** 2)
    ratio = clean_pow / voz_pow
    assert 0.5 < ratio < 2.0, f"voz cercana distorsionada en energia: ratio={ratio:.2f}"


def test_aecstream_int16_reduce_eco():
    rng = np.random.default_rng(3)
    n = 16000 * 4
    far = np.convolve(rng.standard_normal(n), np.ones(8) / 8, mode="same")
    far = (far / np.max(np.abs(far))).astype(np.float32)
    echo = _make_echo_path(n, far).astype(np.float32)
    near_i16 = (np.clip(echo, -1, 1) * 32767).astype(np.int16)
    far_i16 = (np.clip(far, -1, 1) * 32767).astype(np.int16)

    stream = AECStream(block=512, partitions=8, mu=0.4)
    # Empujar far-end y procesar near en chunks de 1600 (como el mic real).
    out = bytearray()
    chunk = 1600
    for i in range(0, n - chunk, chunk):
        stream.push_far(far[i : i + chunk])
        out += stream.process_near(near_i16[i : i + chunk].tobytes())

    cleaned = np.frombuffer(bytes(out), dtype=np.int16).astype(np.float32) / 32768.0
    # Comparar potencia de la 2da mitad (convergido) contra el eco original.
    half = len(cleaned) // 2
    near_f = echo[: len(cleaned)]
    erle = 10 * np.log10(np.mean(near_f[half:] ** 2) / (np.mean(cleaned[half:] ** 2) + 1e-12))
    assert erle > 12.0, f"AECStream ERLE bajo: {erle:.1f} dB"


def test_reset_limpia_estado():
    aec = EchoCanceller(block=256, partitions=4)
    rng = np.random.default_rng(4)
    far = rng.standard_normal(4096)
    aec.process(far[:256], far[:256])
    assert np.any(aec._W != 0)
    aec.reset()
    assert np.all(aec._W == 0)
    assert aec.last_erle_db == 0.0
