"""
audio/capture.py - Captura de microfono en streaming.

Gemini Live espera audio de entrada como PCM 16kHz mono int16. Este modulo
abre un InputStream de sounddevice con callback y publica chunks de bytes
a un callable que registra el consumidor (la sesion Gemini).

Soporta dos modos de operacion:
  - record(): captura continua entre start_recording() y stop_recording().
    Usado por PTT mientras Ctrl este presionado.
  - streaming continuo: ideal para modo escucha libre con VAD.
"""

from __future__ import annotations

import threading
import queue
from typing import Callable

import numpy as np
import sounddevice as sd

GEMINI_INPUT_RATE = 16000   # Gemini Live espera 16kHz mono int16
CHANNELS = 1
DTYPE = "int16"
BLOCKSIZE = 1600            # 100ms @ 16kHz = balance latencia/payload


class AudioCapture:
    """Captura streaming del microfono.

    Uso:
        cap = AudioCapture(on_chunk=lambda pcm: print(len(pcm)))
        cap.start()                # abre el stream (no graba aun)
        cap.start_recording()      # empieza a publicar chunks
        ...
        cap.stop_recording()       # deja de publicar pero mantiene stream
        cap.stop()                 # cierra stream
    """

    def __init__(self, on_chunk: Callable[[bytes], None]) -> None:
        self._on_chunk = on_chunk
        self._stream: sd.InputStream | None = None
        self._recording = False
        self._lock = threading.Lock()
        self._device: int | str | None = None
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=50)
        self._worker: threading.Thread | None = None
        self._stop_worker = threading.Event()
        self._dropped_chunks = 0

    def set_device(self, device: int | str | None) -> None:
        """Override del input device. None = default."""
        self._device = device

    def _callback(self, indata: np.ndarray, _frames: int, _time_info, status) -> None:
        if status:
            print(f"[capture] status: {status}")
        with self._lock:
            if not self._recording:
                return
        pcm_bytes = indata.tobytes()
        try:
            self._queue.put_nowait(pcm_bytes)
        except queue.Full:
            self._dropped_chunks += 1
            if self._dropped_chunks == 1 or self._dropped_chunks % 50 == 0:
                print(f"[capture] queue llena, chunks descartados={self._dropped_chunks}")

    def start(self) -> None:
        if self._stream is not None:
            return
        self._stop_worker.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="JarvisAudioCaptureWorker", daemon=True
        )
        self._worker.start()
        self._stream = sd.InputStream(
            samplerate=GEMINI_INPUT_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            blocksize=BLOCKSIZE,
            device=self._device,
            callback=self._callback,
        )
        self._stream.start()

    def _worker_loop(self) -> None:
        while not self._stop_worker.is_set():
            try:
                pcm_bytes = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._on_chunk(pcm_bytes)
            except Exception as exc:
                print(f"[capture] on_chunk fallo: {exc}")

    def start_recording(self) -> None:
        with self._lock:
            self._recording = True

    def stop_recording(self) -> None:
        with self._lock:
            self._recording = False

    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def stop(self) -> None:
        self.stop_recording()
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        self._stop_worker.set()
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


# Smoke test: graba 3s y reporta nivel RMS
if __name__ == "__main__":
    import sys
    import time

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    received: list[bytes] = []
    cap = AudioCapture(on_chunk=lambda b: received.append(b))
    cap.start()
    cap.start_recording()

    print("Grabando 3 segundos... (habla algo)")
    time.sleep(3)
    cap.stop_recording()
    cap.stop()

    total_bytes = sum(len(b) for b in received)
    samples = total_bytes // 2  # int16 = 2 bytes
    duration_s = samples / GEMINI_INPUT_RATE

    full_pcm = b"".join(received)
    arr = np.frombuffer(full_pcm, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(arr ** 2))) if len(arr) else 0.0
    rms_db = 20 * np.log10(max(rms, 1e-10))

    print(f"[OK] {len(received)} chunks, {total_bytes} bytes ({duration_s:.2f}s)")
    print(f"[OK] RMS: {rms:.4f} ({rms_db:.1f} dBFS) - {'OK' if rms > 0.001 else 'SILENCIO'}")
