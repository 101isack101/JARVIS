"""
audio/wakeword.py - Deteccion de wake-word para barge-in en modo LIBRE.

Problema que resuelve: en parlantes, el eco de la propia voz de Jarvis vuelve
al microfono con la misma energia que la voz de Isaac, asi que un VAD/umbral de
energia no los puede separar (lo confirmamos con datos: picos de eco ~0.08, el
mismo rango que una voz humana). La solucion robusta es cambiar la pregunta: en
vez de "¿hay voz fuerte?" preguntamos "¿se dijo ESTA frase?". El eco de Jarvis
jamas pronuncia "Hey JARVIS", de modo que el wake-word elimina los falsos
positivos de raiz y mantiene el manos-libres.

Usa openWakeWord (CPU, ONNX) con el modelo pre-entrenado `hey_jarvis`. La
dependencia es OPCIONAL: si no esta instalada, el orquestador degrada con gracia
(barge-in desactivado) en vez de crashear — por eso el import es perezoso.

openWakeWord espera audio PCM 16kHz mono int16 (igual que el mic de Jarvis), y
predict() acepta chunks de longitud variable (probamos 1280 y 1600 samples).
"""

from __future__ import annotations

import numpy as np

DEFAULT_MODEL = "hey_jarvis"
DEFAULT_THRESHOLD = 0.5


class WakeWordGate:
    """Detector de wake-word sobre el stream del microfono.

    Uso:
        ww = WakeWordGate(model_name="hey_jarvis", threshold=0.5)
        for chunk_pcm in mic_stream:          # PCM int16 @ 16kHz
            if ww.detected(chunk_pcm):
                ...barge-in...
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        # Import perezoso: la dependencia es opcional. Si falta, el caller
        # captura la excepcion y desactiva el barge-in para la sesion.
        from openwakeword.model import Model

        self.model_name = model_name
        self.threshold = threshold
        self._model = self._load_model(Model, model_name)
        self._last_score = 0.0

    @staticmethod
    def _load_model(Model, model_name: str):  # noqa: ANN001
        """Carga el modelo ONNX; si faltan los archivos (instalacion fresca),
        los descarga una vez y reintenta. openWakeWord no auto-descarga."""
        try:
            return Model(wakeword_models=[model_name], inference_framework="onnx")
        except Exception:
            from openwakeword.utils import download_models

            download_models(model_names=[model_name])
            return Model(wakeword_models=[model_name], inference_framework="onnx")

    def predict(self, pcm_bytes: bytes) -> float:
        """Devuelve el score (0-1) de la wake-word para este chunk."""
        if not pcm_bytes:
            return 0.0
        arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        if arr.size == 0:
            return 0.0
        scores = self._model.predict(arr)
        score = float(scores.get(self.model_name, 0.0))
        self._last_score = score
        return score

    def detected(self, pcm_bytes: bytes) -> bool:
        """True si el score de este chunk supera el umbral."""
        return self.predict(pcm_bytes) >= self.threshold

    def reset(self) -> None:
        """Limpia el buffer interno del modelo (entre turnos)."""
        try:
            self._model.reset()
        except Exception:
            pass
        self._last_score = 0.0

    @property
    def last_score(self) -> float:
        return self._last_score


# Smoke test: carga el modelo real y verifica que el silencio da score ~0.
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print("Cargando openWakeWord (hey_jarvis)...")
    ww = WakeWordGate()
    print("[OK] Modelo cargado")

    silence = np.zeros(1280, dtype=np.int16).tobytes()
    last = 0.0
    for _ in range(13):  # ~1s de silencio
        last = ww.predict(silence)
    print(f"1s silencio -> score={last:.3f} (deberia ser ~0)")
    assert last < 0.3, "silencio no deberia disparar la wake-word"
    print("[OK] WakeWordGate importa, instancia y predice sin error")
