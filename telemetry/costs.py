"""
telemetry/costs.py - Tabla de precios de modelos.

Precios en USD por 1M tokens. Editable en caliente — cuando Anthropic/Google
ajusten precios, actualizo esta tabla sin tocar la logica del tracker.

DECISION DE MODELO (Isaac, 2026-05-15):
- Reasoner canonico de Jarvis: `claude-sonnet-4-6`. NO migrar a 4.7.
  Esta eleccion balancea calidad/costo/latencia para sesiones largas con voz.
  Otros proyectos pueden usar otro modelo; aqui mantener 4.6.

Snapshot 2026-05-15. Si la API cambia precios, regenerar comparando con:
- Anthropic: https://www.anthropic.com/pricing
- Google AI: https://ai.google.dev/pricing
"""

from __future__ import annotations

from dataclasses import dataclass

# Fecha del ultimo snapshot manual de precios. Usar para detectar tablas stale.
PRICING_LAST_UPDATED = "2026-05-15"

# Modelo canonico de razonamiento profundo en Jarvis. Cambios deliberados solo.
JARVIS_REASONER_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class ModelPricing:
    """Precios por 1M tokens en USD."""

    input: float
    output: float
    cache_write: float = 0.0  # 0 si el modelo no soporta caching
    cache_read: float = 0.0


# Tabla maestra. Key = model_id (string usado en API calls).
PRICING: dict[str, ModelPricing] = {
    # === Claude (Anthropic) ===
    "claude-sonnet-4-6": ModelPricing(
        input=3.00,
        output=15.00,
        cache_write=3.75,
        cache_read=0.30,
    ),
    "claude-opus-4-7": ModelPricing(
        input=15.00,
        output=75.00,
        cache_write=18.75,
        cache_read=1.50,
    ),
    "claude-haiku-4-5": ModelPricing(
        input=1.00,
        output=5.00,
        cache_write=1.25,
        cache_read=0.10,
    ),
    # === Gemini Live (Google) ===
    # Gemini Live cobra audio + texto + vision con tarifas distintas.
    # Lo trato como modelos virtuales con sufijo para granularidad.
    "gemini-3.1-flash-live-preview:audio-in": ModelPricing(input=0.10, output=0.0),
    "gemini-3.1-flash-live-preview:audio-out": ModelPricing(input=0.0, output=0.40),
    "gemini-3.1-flash-live-preview:text-in": ModelPricing(input=0.10, output=0.0),
    "gemini-3.1-flash-live-preview:text-out": ModelPricing(input=0.0, output=0.40),
    "gemini-3.1-flash-live-preview:vision-in": ModelPricing(input=0.15, output=0.0),
    "gemini-3.1-flash-live-preview:vision": ModelPricing(input=0.15, output=0.0),
    # Fallback estable
    "gemini-2.5-flash-native-audio-latest:audio-in": ModelPricing(input=0.075, output=0.0),
    "gemini-2.5-flash-native-audio-latest:audio-out": ModelPricing(input=0.0, output=0.30),
    # Deteccion one-shot para crosshair (camera_focus). generate_content, no Live.
    "gemini-3.1-flash:vision-in": ModelPricing(input=0.15, output=0.0),
    "gemini-3.1-flash:text-out": ModelPricing(input=0.0, output=0.40),
}


def cost_usd(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float:
    """Calcula el costo en USD para un evento de uso.

    Devuelve 0.0 si el modelo no esta en la tabla (con un warning silencioso).
    """
    pricing = PRICING.get(model)
    if pricing is None:
        return 0.0
    return (
        pricing.input * input_tokens
        + pricing.output * output_tokens
        + pricing.cache_write * cache_write_tokens
        + pricing.cache_read * cache_read_tokens
    ) / 1_000_000


def list_models() -> list[str]:
    """Lista los model_ids registrados en la tabla de precios."""
    return list(PRICING.keys())


def has_pricing(model: str) -> bool:
    """True si el model_id tiene tarifa registrada."""
    return model in PRICING


# Smoke test
if __name__ == "__main__":
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    print("=== Costos de muestra ===\n")
    cases = [
        ("claude-sonnet-4-6 cold", "claude-sonnet-4-6", 1383, 112, 0, 0),
        ("claude-sonnet-4-6 warm", "claude-sonnet-4-6", 0, 92, 0, 1383),
        ("claude-opus-4-7 cold (referencia)", "claude-opus-4-7", 1383, 112, 0, 0),
        ("Gemini audio in 60s ~= 1500 tokens", "gemini-3.1-flash-live-preview:audio-in", 1500, 0, 0, 0),
        ("Gemini audio out 8s ~= 800 tokens", "gemini-3.1-flash-live-preview:audio-out", 0, 800, 0, 0),
    ]
    for label, model, ti, to, cw, cr in cases:
        c = cost_usd(model, ti, to, cw, cr)
        print(f"  {label:<48s} ${c:>10.6f}")

    print("\n=== Comparativa Sonnet vs Opus para 1M input + 200k output ===")
    print(f"  Sonnet 4.6: ${cost_usd('claude-sonnet-4-6', 1_000_000, 200_000):.2f}")
    print(f"  Opus 4.7  : ${cost_usd('claude-opus-4-7', 1_000_000, 200_000):.2f}")
    print(f"  Ahorro    : {(cost_usd('claude-opus-4-7', 1_000_000, 200_000) / cost_usd('claude-sonnet-4-6', 1_000_000, 200_000)):.1f}x mas barato Sonnet")
