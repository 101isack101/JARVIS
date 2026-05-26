"""
audio/vad.py - Wrapper de Silero VAD para deteccion local de voz.

Silero VAD procesa chunks de 512 samples @ 16kHz (32ms) y emite probabilidad
de voz 0-1. Usado en modo "escucha libre" para:
  - Feedback visual en overlay (indicador 'estas hablando ahora')
  - Gating local (no enviar audio silencioso a Gemini -> ahorra API)
  - Disparar barge-in mas rapido que esperar al VAD del servidor

NO sustituye al VAD del lado de Gemini Live (que sigue siendo la fuente
autoritativa para endpointing), solo lo complementa para UX y costos.

Silero VAD v5+ API: load_silero_vad() devuelve un modulo torch.
Llamarlo con (audio_tensor_float32, sample_rate=16000) -> tensor[1] con prob.
"""

from __future__ import annotations

import numpy as np
import torch
from silero_vad import load_silero_vad

VAD_SAMPLE_RATE = 16000
VAD_WINDOW_SAMPLES = 512   # 32ms @ 16kHz - hardcoded por Silero
DEFAULT_THRESHOLD = 0.5
# 1.92s de silencio = "termino de hablar". 800ms cortaba pausas naturales
# de pensamiento ("eh", "y entonces..."). 1.92s es un sweet spot conversacional
# y se compensa con cooldown anti-rebote en jarvis.py para no congelar la UX.
SILENCE_FRAMES_TO_END = 60


class VADGate:
    """Detector de voz con histeresis simple.

    Uso:
        vad = VADGate(threshold=0.5)
        for chunk_pcm in mic_stream:        # chunks de 1600 bytes (100ms)
            events = vad.feed(chunk_pcm)    # split internamente en ventanas de 512
            for ev in events:
                if ev.kind == 'start': ...
                if ev.kind == 'end': ...
    """

    class Event:
        __slots__ = ("kind", "prob")

        def __init__(self, kind: str, prob: float) -> None:
            self.kind = kind  # 'start' | 'end'
            self.prob = prob

        def __repr__(self) -> str:
            return f"VADEvent(kind={self.kind!r}, prob={self.prob:.2f})"

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        silence_frames_to_end: int = SILENCE_FRAMES_TO_END,
    ) -> None:
        self.threshold = threshold
        self.silence_frames_to_end = silence_frames_to_end
        self._model = load_silero_vad()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._silence_streak = 0
        self._last_prob = 0.0

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._in_speech = False
        self._silence_streak = 0
        self._last_prob = 0.0
        try:
            self._model.reset_states()
        except AttributeError:
            pass

    def feed(self, pcm_bytes: bytes) -> list["VADGate.Event"]:
        """Procesa un chunk PCM int16 @ 16kHz y devuelve eventos detectados."""
        if not pcm_bytes:
            return []
        new = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._buffer = np.concatenate([self._buffer, new])

        events: list[VADGate.Event] = []
        while len(self._buffer) >= VAD_WINDOW_SAMPLES:
            window = self._buffer[:VAD_WINDOW_SAMPLES]
            self._buffer = self._buffer[VAD_WINDOW_SAMPLES:]

            with torch.no_grad():
                tensor = torch.from_numpy(window)
                prob = float(self._model(tensor, VAD_SAMPLE_RATE).item())
            self._last_prob = prob

            if prob >= self.threshold:
                if not self._in_speech:
                    self._in_speech = True
                    events.append(self.Event("start", prob))
                self._silence_streak = 0
            else:
                if self._in_speech:
                    self._silence_streak += 1
                    if self._silence_streak >= self.silence_frames_to_end:
                        self._in_speech = False
                        events.append(self.Event("end", prob))
                        self._silence_streak = 0
        return events

    @property
    def is_speech(self) -> bool:
        return self._in_speech

    @property
    def last_prob(self) -> float:
        return self._last_prob


# Smoke test: feed silencio sintetico + tono y verifica que detecta voz
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print("Cargando Silero VAD...")
    vad = VADGate(threshold=0.5)
    print("[OK] Modelo cargado")

    # 1) Silencio puro - no deberia disparar 'start'
    silence = (np.zeros(16000, dtype=np.int16)).tobytes()
    events = vad.feed(silence)
    print(f"1s silencio  -> {len(events)} eventos, last_prob={vad.last_prob:.2f}")
    assert not vad.is_speech, "VAD no deberia marcar silencio como voz"

    # 2) Ruido pseudo-voz - generamos algo con energia formanteada (mezcla de 200-600Hz)
    sr = 16000
    t = np.arange(sr * 2) / sr
    voice_like = (
        np.sin(2 * np.pi * 220 * t)
        + 0.5 * np.sin(2 * np.pi * 440 * t)
        + 0.3 * np.sin(2 * np.pi * 880 * t)
    )
    voice_like = (voice_like / np.max(np.abs(voice_like)) * 0.3 * 32767).astype(np.int16)
    events = vad.feed(voice_like.tobytes())
    print(f"2s tono mix  -> {len(events)} eventos, last_prob={vad.last_prob:.2f}")
    # No assertamos start aqui porque Silero esta entrenado con voz humana real,
    # tonos sinteticos pueden no disparar. Solo reportamos.

    print("[OK] VADGate importa, instancia, y procesa sin error")
