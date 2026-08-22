"""grok/reasoner.py - Wrapper de Grok 4.6 como reasoner profundo de JARVIS.

Lo invoca Gemini Live como function tool `ask_grok_deep` (alias `ask_claude_deep`).
API xAI OpenAI-compatible: https://api.x.ai/v1

Implementa:
  - Retry con backoff sobre 429 / 5xx
  - Reporte automatico al TokenTracker (input/output/cache_read)
  - Cliente async para que asyncio.wait_for cancele la HTTP de verdad
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telemetry.logger import get_logger
from telemetry.tracker import TokenTracker

log = get_logger("reasoner")

DEFAULT_MODEL = "grok-4.6"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_BASE_URL = "https://api.x.ai/v1"
MAX_RETRIES = 3
_GROK_AUTH_PATH = Path.home() / ".grok" / "auth.json"


def grok_cli_access_token(auth_path: Path | None = None) -> str | None:
    """Token vivo de la sesion Grok CLI (~/.grok/auth.json)."""
    path = auth_path or Path(os.environ.get("JARVIS_GROK_AUTH_PATH") or _GROK_AUTH_PATH)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    now = datetime.now(timezone.utc)
    best: str | None = None
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        token = str(entry.get("key") or "").strip()
        if not token:
            continue
        expires = str(entry.get("expires_at") or "").strip()
        if expires:
            try:
                exp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if exp <= now:
                    continue
            except ValueError:
                pass
        best = token
        if str(entry.get("auth_mode") or "") == "oidc":
            return token
    return best


def resolve_xai_api_key(explicit: str | None = None) -> str | None:
    """Orden: key durable `xai-` en env, sesion Grok CLI, cualquier XAI_API_KEY."""
    env_key = (explicit if explicit is not None else os.environ.get("XAI_API_KEY") or "").strip()
    if env_key.startswith("xai-"):
        return env_key
    cli = grok_cli_access_token()
    if cli:
        return cli
    return env_key or None


@dataclass
class ReasonerResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    latency_ms: float


class GrokReasoner:
    """Cliente Grok 4.6 (xAI) con token tracking."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        tracker: TokenTracker | None = None,
        system_prompt: str | None = None,
        client: Any | None = None,
        timeout_s: float | None = None,
        base_url: str | None = None,
    ) -> None:
        self._explicit_api_key = api_key
        self.api_key = resolve_xai_api_key(api_key)
        self.model = model or os.environ.get("JARVIS_GROK_MODEL", DEFAULT_MODEL)
        self.tracker = tracker
        self.client = client
        self.async_client = None
        self.base_url = (base_url or os.environ.get("JARVIS_GROK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_s = float(timeout_s or os.environ.get("JARVIS_GROK_TIMEOUT_S", "60"))
        self.system_prompt = system_prompt or self._default_system_prompt()
        if not self.api_key and self.client is None:
            raise ValueError("XAI_API_KEY no configurada")

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.client is not None)

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "Eres Grok 4.6, invocado como reasoner profundo por JARVIS, "
            "un agente conversacional en tiempo real para Isaac. "
            "JARVIS usa Gemini Live para voz y te delega cuando necesita razonamiento profundo: "
            "analisis, arquitectura, planning multi-paso, decisiones tecnicas. "
            "Responde SIEMPRE en espanol neutro latinoamericano formal. "
            "Trata a Isaac como 'senor' cuando aplique naturalmente. "
            "Mantén un tono sereno, preciso y tecnicamente riguroso. "
            "Conciso pero completo. Usa formato markdown para listas y codigo. "
            "Cuando devuelvas codigo, hazlo en bloques con lenguaje especificado. "
            "Si no sabes algo, dilo claro: 'no dispongo de esa informacion', 'no me consta'. "
            "Tu respuesta sera leida en voz alta por JARVIS, asi que evita "
            "tablas markdown anchas (no se renderizan en audio). Listas y "
            "parrafos cortos funcionan mejor para TTS."
        )

    def _refresh_api_key(self) -> str:
        key = resolve_xai_api_key(self._explicit_api_key)
        if not key:
            raise ValueError("XAI_API_KEY no configurada")
        self.api_key = key
        return key

    def _ensure_client(self):
        if self.client is not None:
            return self.client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Paquete 'openai' no instalado; ejecuta pip install -r requirements.txt"
            ) from exc
        self.client = OpenAI(
            api_key=self._refresh_api_key(),
            base_url=self.base_url,
            timeout=self.timeout_s,
        )
        return self.client

    def _ensure_async_client(self):
        if self.async_client is not None:
            return self.async_client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Paquete 'openai' no instalado; ejecuta pip install -r requirements.txt"
            ) from exc
        self.async_client = AsyncOpenAI(
            api_key=self._refresh_api_key(),
            base_url=self.base_url,
            timeout=self.timeout_s,
        )
        return self.async_client

    def warmup(self) -> None:
        if not self.configured:
            return
        self._ensure_client()
        if self.api_key:
            self._ensure_async_client()

    def ask(
        self,
        prompt: str,
        context_extra: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ReasonerResponse:
        max_tokens = max(128, min(int(max_tokens or DEFAULT_MAX_TOKENS), 8192))
        client = self._ensure_client()
        t0 = time.perf_counter()
        msg = self._call_with_retry(client, prompt, context_extra, max_tokens)
        return self._to_response(msg, (time.perf_counter() - t0) * 1000)

    async def ask_async(
        self,
        prompt: str,
        context_extra: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> ReasonerResponse:
        max_tokens = max(128, min(int(max_tokens or DEFAULT_MAX_TOKENS), 8192))
        client = self._ensure_async_client()
        t0 = time.perf_counter()
        msg = await self._call_with_retry_async(client, prompt, context_extra, max_tokens)
        return self._to_response(msg, (time.perf_counter() - t0) * 1000)

    def _messages(self, prompt: str, context_extra: str | None) -> list[dict[str, str]]:
        system = self.system_prompt
        if context_extra and context_extra.strip():
            system = f"{system}\n\nCONTEXTO ADICIONAL:\n{context_extra.strip()}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    def _should_retry(self, exc: BaseException) -> bool:
        name = type(exc).__name__
        status = getattr(exc, "status_code", None)
        if name in {"RateLimitError", "APITimeoutError", "InternalServerError"}:
            return True
        if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
            return True
        return False

    def _call_with_retry(self, client: Any, prompt: str, context_extra: str | None, max_tokens: int):
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return client.chat.completions.create(
                    model=self.model,
                    messages=self._messages(prompt, context_extra),
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                last_exc = exc
                if self._should_retry(exc) and attempt + 1 < MAX_RETRIES:
                    delay = 1.5 ** attempt
                    log.warning(
                        "[reasoner] retry {}/{} en {:.1f}s: {}",
                        attempt + 1,
                        MAX_RETRIES,
                        delay,
                        type(exc).__name__,
                    )
                    time.sleep(delay)
                    continue
                raise
        raise last_exc or RuntimeError("retries exhausted")

    async def _call_with_retry_async(self, client: Any, prompt: str, context_extra: str | None, max_tokens: int):
        last_exc: BaseException | None = None
        for attempt in range(MAX_RETRIES):
            try:
                return await client.chat.completions.create(
                    model=self.model,
                    messages=self._messages(prompt, context_extra),
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                last_exc = exc
                if self._should_retry(exc) and attempt + 1 < MAX_RETRIES:
                    delay = 1.5 ** attempt
                    log.warning(
                        "[reasoner] retry async {}/{} en {:.1f}s: {}",
                        attempt + 1,
                        MAX_RETRIES,
                        delay,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        raise last_exc or RuntimeError("retries exhausted")

    def _to_response(self, msg: Any, latency_ms: float) -> ReasonerResponse:
        text = _message_text(msg)
        in_tok, out_tok, cache_r = _usage_from_completion(msg)
        cost = 0.0
        if self.tracker:
            cost = self.tracker.record(
                self.model,
                input_tokens=max(0, in_tok - cache_r),
                output_tokens=out_tok,
                cache_read_tokens=cache_r,
            )
        return ReasonerResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_creation_tokens=0,
            cache_read_tokens=cache_r,
            cost_usd=cost,
            latency_ms=latency_ms,
        )


def _message_text(msg: Any) -> str:
    choices = getattr(msg, "choices", None) or []
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None) if message is not None else None
        if content:
            return str(content)
    direct = getattr(msg, "output_text", None)
    return str(direct or "")


def _usage_from_completion(msg: Any) -> tuple[int, int, int]:
    usage = getattr(msg, "usage", None)
    if usage is None:
        return 0, 0, 0
    if isinstance(usage, dict):
        in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        cache_r = int((details or {}).get("cached_tokens") or 0)
        return in_tok, out_tok, cache_r
    in_tok = int(getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0)
    out_tok = int(getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None) or 0)
    details = getattr(usage, "prompt_tokens_details", None) or getattr(usage, "input_tokens_details", None)
    cache_r = 0
    if isinstance(details, dict):
        cache_r = int(details.get("cached_tokens") or 0)
    elif details is not None:
        cache_r = int(getattr(details, "cached_tokens", 0) or 0)
    return in_tok, out_tok, cache_r


# Compatibilidad: el nombre historico sigue importable.
ClaudeReasoner = GrokReasoner


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")

    tracker = TokenTracker()
    reasoner = GrokReasoner(tracker=tracker)

    print(f"Modelo: {reasoner.model}")
    print(f"System prompt length: {len(reasoner.system_prompt)} chars\n")

    print("=== Pregunta 1 ===")
    r1 = reasoner.ask("En 2 frases: por que Grok 4.6 es buena eleccion para un reasoner conversacional?")
    print(f"Respuesta: {r1.text}")
    print(f"  Tokens: in={r1.input_tokens} out={r1.output_tokens} cache_r={r1.cache_read_tokens}")
    print(f"  Latencia: {r1.latency_ms:.0f}ms, Costo: ${r1.cost_usd:.6f}")

    print("\n[OK] GrokReasoner smoke test passed")
