"""Whisper transcription for OBS memory."""

from __future__ import annotations

from pathlib import Path


class WhisperTranscriber:
    _models: dict[tuple[str, str, str], object] = {}

    def __init__(self, model_size: str, device: str, compute_type: str) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type

    def _model(self):
        key = (self.model_size, self.device, self.compute_type)
        if key in self._models:
            return self._models[key]
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError(
                "Falta faster-whisper. Instala requirements.txt para transcribir OBS."
            ) from exc
        try:
            model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception:
            if self.device == "cpu":
                raise
            model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
        self._models[key] = model
        return model

    def transcribe(self, wav_path: Path) -> str:
        segments, _info = self._model().transcribe(
            str(wav_path),
            language="es",
            vad_filter=True,
            beam_size=5,
        )
        return "\n".join(seg.text.strip() for seg in segments if seg.text.strip())
