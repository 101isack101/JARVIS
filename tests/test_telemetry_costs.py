"""
Tests de la tabla de precios telemetry/costs.py.

Garantizan:
- Modelos esperados existen con precios > 0.
- Grok 4.6 es el reasoner canonico de Jarvis.
- has_pricing() distingue conocido vs desconocido sin lanzar.
- PRICING_LAST_UPDATED es un string valido (formato YYYY-MM-DD).
"""
import re

import pytest

from telemetry.costs import (
    JARVIS_REASONER_MODEL,
    PRICING,
    PRICING_LAST_UPDATED,
    cost_usd,
    has_pricing,
    list_models,
)


def test_gemini_vision_in_virtual_model_has_pricing():
    assert cost_usd("gemini-3.1-flash-live-preview:vision-in", input_tokens=1_000) > 0


def test_reasoner_canonico_es_grok_47():
    """Isaac: analisis, agentico y complejo van a Grok 4.7. Gemini queda en la voz."""
    assert JARVIS_REASONER_MODEL == "grok-4.7"
    assert has_pricing(JARVIS_REASONER_MODEL)
    assert has_pricing("grok-4.6")
    assert cost_usd(JARVIS_REASONER_MODEL, input_tokens=1_000, output_tokens=200) > 0


def test_modelos_retirados_conservan_tarifa():
    """La telemetría histórica guarda el model_id: sin tarifa el costo pasado
    se reportaría como 0. Las entradas viejas no se borran al migrar."""
    assert has_pricing("claude-sonnet-4-6")


def test_pricing_last_updated_formato_valido():
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", PRICING_LAST_UPDATED), (
        f"PRICING_LAST_UPDATED debe ser YYYY-MM-DD, no '{PRICING_LAST_UPDATED}'"
    )


def test_has_pricing_distingue_conocido_y_desconocido():
    assert has_pricing("claude-sonnet-4-6")
    assert not has_pricing("modelo-que-no-existe-2099")


def test_cost_usd_devuelve_cero_para_modelo_desconocido_sin_lanzar():
    # No raises — el contrato es que stale models no rompen Jarvis en runtime
    assert cost_usd("modelo-que-no-existe-2099", input_tokens=1_000) == 0.0


def test_todos_los_modelos_listados_tienen_pricing_positivo():
    for model_id in list_models():
        pricing = PRICING[model_id]
        # Al menos uno de input/output debe ser > 0 (no modelos vacios en la tabla)
        assert pricing.input > 0 or pricing.output > 0, (
            f"Modelo {model_id} esta en PRICING con tarifas en cero"
        )


@pytest.mark.parametrize("model_id", [
    "grok-4.7",
    "grok-4.6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-haiku-4-5",
])
def test_modelos_reasoner_esperados_existen(model_id):
    assert has_pricing(model_id), f"Falta tarifa para {model_id}"
