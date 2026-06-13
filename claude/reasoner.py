"""
claude/reasoner.py - Wrapper de Claude 4.6 Sonnet como reasoner profundo.

Lo invoca Gemini Live como function tool 'ask_claude_deep' (registrado en Fase 2).
Implementa:
  - Prompt caching agresivo (system prompt grande con cache_control ephemeral)
  - Reporte automatico al TokenTracker (input/output/cache_w/cache_r)
  - Retry con backoff exponencial sobre 429 / 5xx (lift de Interview_Copilot)

Uso:
  reasoner = ClaudeReasoner(api_key=..., tracker=tracker)
  response = reasoner.ask("diseña una arquitectura para X", context_extra="...")
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import asyncio

from anthropic import (
    Anthropic,
    APIError,
    APIStatusError,
    AsyncAnthropic,
    RateLimitError,
)

from telemetry.tracker import TokenTracker
from telemetry.logger import get_logger

log = get_logger("reasoner")

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 1024
MAX_RETRIES = 3


@dataclass
class ReasonerResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    latency_ms: float


class ClaudeReasoner:
    """Cliente Claude con prompt caching + token tracking."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        tracker: TokenTracker | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY no configurada")
        self.model = model
        self.client = Anthropic(api_key=self.api_key)
        # Cliente async: necesario para que asyncio.wait_for(timeout=...) cancele
        # la request HTTP de verdad cuando expira (con el sync, asyncio.to_thread
        # solo cancela el wait pero el thread sigue consumiendo cuota Anthropic).
        self.async_client = AsyncAnthropic(api_key=self.api_key)
        self.tracker = tracker
        self.system_prompt = system_prompt or self._default_system_prompt()

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "Eres Claude 4.6 Sonnet, invocado como reasoner profundo por JARVIS, "
            "un agente conversacional en tiempo real para Isaac. "
            "JARVIS usa Gemini Live para voz y te delega cuando necesita razonamiento profundo: "
            "codigo, arquitectura, planning multi-paso, decisiones tecnicas. "
            "Responde SIEMPRE en espanol neutro latinoamericano formal. "
            "Trata a Isaac como 'señor' cuando aplique naturalmente. "
            "Mantén un tono sereno, preciso y tecnicamente riguroso. "
            "Conciso pero completo. Usa formato markdown para listas y codigo. "
            "Cuando devuelvas codigo, hazlo en bloques con lenguaje especificado. "
            "Si no sabes algo, dilo claro: 'no dispongo de esa informacion', 'no me consta'. "
            "Tu respuesta sera leida en voz alta por JARVIS, asi que evita "
            "tablas markdown anchas (no se renderizan en audio). Listas y "
            "parrafos cortos funcionan mejor para TTS."
        )

    def ask(
        self,
        prompt: str,
        context_extra: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ReasonerResponse:
        """Ejecuta una llamada a Claude con caching activo."""
        # Sistema = (prompt base) + opcionalmente (contexto extra). Ambos cacheables.
        system_blocks = [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if context_extra:
            system_blocks.append({
                "type": "text",
                "text": context_extra,
                "cache_control": {"type": "ephemeral"},
            })

        t0 = time.perf_counter()
        msg = self._call_with_retry(
            messages=[{"role": "user", "content": prompt}],
            system=system_blocks,
            max_tokens=max_tokens,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        usage = msg.usage
        in_tok = usage.input_tokens
        out_tok = usage.output_tokens
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0

        cost = 0.0
        if self.tracker:
            cost = self.tracker.record(
                self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_write_tokens=cache_w,
                cache_read_tokens=cache_r,
            )

        text = msg.content[0].text if msg.content else ""
        return ReasonerResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_tokens=cache_w,
            cache_read_tokens=cache_r,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    def _call_with_retry(self, **kwargs):
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return self.client.messages.create(model=self.model, **kwargs)
            except (RateLimitError, APIStatusError) as e:
                last_exc = e
                # 5xx -> retry. 4xx (except 429) -> raise.
                status = getattr(e, "status_code", None)
                if isinstance(e, RateLimitError) or (status and status >= 500):
                    delay = 1.5 ** attempt
                    log.warning("[reasoner] retry {}/{} en {:.1f}s: {}", attempt + 1, MAX_RETRIES, delay, type(e).__name__)
                    time.sleep(delay)
                    continue
                raise
            except APIError as e:
                last_exc = e
                raise
        raise last_exc or RuntimeError("retries exhausted")

    async def ask_async(
        self,
        prompt: str,
        context_extra: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ReasonerResponse:
        """Version async de ask(). Permite que asyncio.wait_for cancele de verdad
        la request HTTP cuando se acaba el timeout (sin dejar threads zombies)."""
        system_blocks = [
            {
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if context_extra:
            system_blocks.append({
                "type": "text",
                "text": context_extra,
                "cache_control": {"type": "ephemeral"},
            })

        t0 = time.perf_counter()
        msg = await self._call_with_retry_async(
            messages=[{"role": "user", "content": prompt}],
            system=system_blocks,
            max_tokens=max_tokens,
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        usage = msg.usage
        in_tok = usage.input_tokens
        out_tok = usage.output_tokens
        cache_w = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_r = getattr(usage, "cache_read_input_tokens", 0) or 0

        cost = 0.0
        if self.tracker:
            cost = self.tracker.record(
                self.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_write_tokens=cache_w,
                cache_read_tokens=cache_r,
            )

        text = msg.content[0].text if msg.content else ""
        return ReasonerResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_tokens=cache_w,
            cache_read_tokens=cache_r,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    async def _call_with_retry_async(self, **kwargs):
        last_exc = None
        for attempt in range(MAX_RETRIES):
            try:
                return await self.async_client.messages.create(model=self.model, **kwargs)
            except (RateLimitError, APIStatusError) as e:
                last_exc = e
                status = getattr(e, "status_code", None)
                if isinstance(e, RateLimitError) or (status and status >= 500):
                    delay = 1.5 ** attempt
                    log.warning("[reasoner] retry async {}/{} en {:.1f}s: {}", attempt + 1, MAX_RETRIES, delay, type(e).__name__)
                    await asyncio.sleep(delay)
                    continue
                raise
            except APIError as e:
                last_exc = e
                raise
        raise last_exc or RuntimeError("retries exhausted")


# Smoke test
if __name__ == "__main__":
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    tracker = TokenTracker()
    reasoner = ClaudeReasoner(tracker=tracker)

    print(f"Modelo: {reasoner.model}")
    print(f"System prompt length: {len(reasoner.system_prompt)} chars\n")

    # Pregunta 1 (cold cache)
    print("=== Pregunta 1 (cold) ===")
    r1 = reasoner.ask("En 2 frases: por que Sonnet 4.6 es buena eleccion para un reasoner conversacional?")
    print(f"Respuesta: {r1.text}")
    print(f"  Tokens: in={r1.input_tokens} out={r1.output_tokens} cache_w={r1.cache_creation_tokens} cache_r={r1.cache_read_tokens}")
    print(f"  Latencia: {r1.latency_ms:.0f}ms, Costo: ${r1.cost_usd:.6f}")

    # Pregunta 2 (warm cache esperado)
    print("\n=== Pregunta 2 (warm cache esperado) ===")
    r2 = reasoner.ask("En 1 frase: cual es la diferencia clave entre Sonnet y Opus?")
    print(f"Respuesta: {r2.text}")
    print(f"  Tokens: in={r2.input_tokens} out={r2.output_tokens} cache_w={r2.cache_creation_tokens} cache_r={r2.cache_read_tokens}")
    print(f"  Latencia: {r2.latency_ms:.0f}ms, Costo: ${r2.cost_usd:.6f}")

    snap = tracker.snapshot()
    print(f"\n=== Tracker snapshot ===")
    print(f"  Total cost: ${snap.total_cost_usd:.6f}")
    print(f"  Cache hit rate: {snap.cache_hit_rate(reasoner.model):.1%}")

    print("\n[OK] ClaudeReasoner smoke test passed")
