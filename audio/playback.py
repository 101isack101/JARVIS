"""
audio/playback.py - Reproductor de audio con cola interrumpible.

Gemini Live emite chunks PCM 24kHz int16 mono. Los acumulamos en una cola
y los enviamos al device de salida via sounddevice.OutputStream con un
callback que pulla de la cola.

Para BARGE-IN: cuando Isaac empieza a hablar (server emite interrupted=True
o usuario presiona PTT), llamamos interrupt() que:
  1. Vacia la cola pendiente
  2. Detiene el chunk actual mid-playback

Sample rate fijo 24000 Hz (output de Gemini Live).
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd

GEMINI_OUTPUT_RATE = 24000  # Gemini Live siempre devuelve 24kHz mono int16
BLOCKSIZE = 1024            # ~42ms @ 24kHz — balance latencia/CPU
CHANNELS = 1
DTYPE = "int16"
# Bloques vacios consecutivos antes de declarar "playback completo". El jitter de
# red entre chunks de Gemini vacia la cola momentaneamente; sin este grace, cada
# hueco transitorio dispararia on_underflow -> en LIBRE eso reseteaba VAD/AEC y
# parpadeaba speaking<->listening a mitad de frase. 3 bloques ~= 128ms: tolera el
# jitter sin retrasar de forma perceptible la reapertura del mic al terminar.
UNDERFLOW_GRACE_BLOCKS = 3


class AudioPlayer:
    """Player con cola FIFO interrumpible.

    Uso:
        player = AudioPlayer()
        player.start()
        player.push(pcm_bytes)   # llamar varias veces conforme llegan chunks
        player.interrupt()       # vacia cola y para playback inmediatamente
        player.stop()            # cleanup
    """

    def __init__(
        self,
        on_underflow: Callable[[], None] | None = None,
        on_playback: Callable[[bytes], None] | None = None,
    ) -> None:
        self._queue: deque[np.ndarray] = deque()
        self._stream: sd.OutputStream | None = None
        self._on_underflow = on_underflow
        # Callback con lo que REALMENTE sale al parlante (int16 24kHz). Es la
        # referencia (far-end) para el AEC, tomada aqui —no al encolar— para que
        # quede alineada en tiempo con lo que el mic captura como eco.
        self._on_playback = on_playback
        self._lock = threading.Lock()
        self._playing = False
        # Cuenta de callbacks consecutivos con la cola vacia (ver UNDERFLOW_GRACE_BLOCKS).
        self._empty_streak = 0

    def _callback(self, outdata: np.ndarray, frames: int, _time_info, _status) -> None:
        """sounddevice llama esto desde su thread interno."""
        with self._lock:
            chunk = self._queue.popleft() if self._queue else None
        if chunk is None:
            outdata.fill(0)
            # Solo declaramos fin tras varios bloques vacios seguidos: un hueco
            # transitorio por jitter de red NO es fin de turno (evita flapping).
            if self._playing:
                self._empty_streak += 1
                if self._empty_streak >= UNDERFLOW_GRACE_BLOCKS:
                    self._playing = False
                    self._empty_streak = 0
                    if self._on_underflow:
                        self._on_underflow()
            return

        n = min(len(chunk), frames)
        outdata[:n, 0] = chunk[:n]
        if n < frames:
            outdata[n:, 0] = 0
        elif len(chunk) > frames:
            leftover = chunk[frames:]
            with self._lock:
                self._queue.appendleft(leftover)
        self._playing = True
        self._empty_streak = 0
        # Emitir la referencia (solo las muestras reales emitidas). Debe ser
        # rapido: el consumidor (AEC) solo resamplea y encola.
        if self._on_playback is not None:
            try:
                self._on_playback(outdata[:n, 0].tobytes())
            except Exception:
                pass

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(
            samplerate=GEMINI_OUTPUT_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            callback=self._callback,
        )
        self._stream.start()

    def push(self, pcm_bytes: bytes) -> None:
        """Encola un chunk de audio PCM int16 mono @ 24kHz."""
        if not pcm_bytes:
            return
        arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        with self._lock:
            self._queue.append(arr)

    def interrupt(self) -> int:
        """Vacia la cola y devuelve cuantos chunks descarto."""
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
        return n

    def is_playing(self) -> bool:
        with self._lock:
            has_queue = bool(self._queue)
        return has_queue or self._playing

    def stop(self) -> None:
        self.interrupt()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None


# Smoke test: reproduce el WAV de spike_response y prueba interrupt
if __name__ == "__main__":
    import sys
    import time
    import wave
    from pathlib import Path

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    wav_path = Path(__file__).resolve().parent.parent / "data" / "spike_response.wav"
    if not wav_path.exists():
        print(f"[ERROR] Falta {wav_path}. Corre primero spike_gemini_live.py")
        sys.exit(1)

    with wave.open(str(wav_path), "rb") as wf:
        frames = wf.readframes(wf.getnframes())

    chunks = [frames[i : i + 4096] for i in range(0, len(frames), 4096)]
    print(f"Smoke test: {len(chunks)} chunks de 4096 bytes")

    player = AudioPlayer(on_underflow=lambda: print("  [underflow]"))
    player.start()

    print("  Reproduciendo primer mitad...")
    for c in chunks[: len(chunks) // 2]:
        player.push(c)

    time.sleep(1.5)
    print("  INTERRUMPIENDO...")
    discarded = player.interrupt()
    print(f"  Chunks descartados: {discarded}")

    time.sleep(0.5)
    print("  Reproduciendo segunda mitad...")
    for c in chunks[len(chunks) // 2 :]:
        player.push(c)

    while player.is_playing():
        time.sleep(0.1)
    player.stop()
    print("[OK] AudioPlayer smoke test passed")
