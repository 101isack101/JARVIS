"""
Tests de audio/wakeword.py — WakeWordGate (barge-in por wake-word).

WakeWordGate envuelve openWakeWord para detectar "Hey JARVIS" mientras Jarvis
habla, y asi cortarlo sin teclado y SIN los falsos positivos del eco (el eco de
Jarvis nunca pronuncia la frase). Estos tests fijan el contrato sin cargar el
modelo ONNX real: mockeamos openwakeword.model.Model.

  - predict() devuelve el score del modelo configurado (0-1)
  - detected() respeta el umbral
  - reset() limpia el modelo y el ultimo score
  - predict(b"") -> 0.0 (borde)
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest


class _FakeOWWModel:
    """Modelo openWakeWord falso: score controlable por test via class attr."""

    next_score = 0.0

    def __init__(self, wakeword_models=None, inference_framework="onnx", **kwargs):
        self.wakeword_models = wakeword_models or ["hey_jarvis"]
        self.reset_calls = 0

    def predict(self, arr):  # noqa: ANN001
        name = self.wakeword_models[0]
        return {name: np.float32(_FakeOWWModel.next_score)}

    def reset(self):
        self.reset_calls += 1


@pytest.fixture()
def gate(monkeypatch):
    """WakeWordGate con openwakeword.model.Model mockeado."""
    fake_module = types.ModuleType("openwakeword.model")
    fake_module.Model = _FakeOWWModel
    monkeypatch.setitem(sys.modules, "openwakeword.model", fake_module)
    _FakeOWWModel.next_score = 0.0

    from audio.wakeword import WakeWordGate

    return WakeWordGate(model_name="hey_jarvis", threshold=0.5)


def _frame(n: int = 1280) -> bytes:
    return np.zeros(n, dtype=np.int16).tobytes()


def test_predict_devuelve_score(gate):
    _FakeOWWModel.next_score = 0.73
    assert abs(gate.predict(_frame()) - 0.73) < 1e-4


def test_detected_respeta_umbral(gate):
    _FakeOWWModel.next_score = 0.49
    assert gate.detected(_frame()) is False
    _FakeOWWModel.next_score = 0.51
    assert gate.detected(_frame()) is True


def test_predict_vacio_es_cero(gate):
    _FakeOWWModel.next_score = 0.99
    assert gate.predict(b"") == 0.0


def test_reset_limpia_modelo_y_score(gate):
    _FakeOWWModel.next_score = 0.8
    gate.predict(_frame())
    assert gate.last_score > 0.0
    gate.reset()
    assert gate.last_score == 0.0
    assert gate._model.reset_calls == 1


def test_last_score_se_actualiza(gate):
    _FakeOWWModel.next_score = 0.42
    gate.predict(_frame())
    assert abs(gate.last_score - 0.42) < 1e-4
