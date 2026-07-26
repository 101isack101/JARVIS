"""GPT-5.6 Terra reasoner for JARVIS complex technical work."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from telemetry.tracker import TokenTracker

DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_MAX_OUTPUT_TOKENS = 1600
_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}


@dataclass
class TerraCodeResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: float
    latency_ms: float


class TerraCodeReasoner:
    """OpenAI Responses API wrapper for GPT-5.6 Terra, lazy at startup."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        tracker: TokenTracker | None = None,
        client: Any | None = None,
        timeout_s: float | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model or os.environ.get("JARVIS_TERRA_MODEL", DEFAULT_MODEL)
        self.tracker = tracker
        self.client = client
        self.timeout_s = float(
            timeout_s
            or os.environ.get("JARVIS_TERRA_TIMEOUT_S")
            or os.environ.get("JARVIS_OPENAI_TIMEOUT_S", "90")
        )
        candidate_effort = (
            reasoning_effort or os.environ.get("JARVIS_TERRA_REASONING_EFFORT", DEFAULT_REASONING_EFFORT)
        ).strip().lower()
        self.reasoning_effort = (
            candidate_effort if candidate_effort in _REASONING_EFFORTS else DEFAULT_REASONING_EFFORT
        )
        self.system_prompt = self._default_system_prompt()

    @property
    def configured(self) -> bool:
        return bool(self.api_key or self.client is not None)

    @staticmethod
    def _default_system_prompt() -> str:
        return (
            "Eres GPT-5.6 Terra, invocado por JARVIS como especialista en codigo y "
            "modo agentico para Isaac. Tu trabajo es producir planes ejecutables, "
            "codigo robusto, debugging preciso y pasos verificables. Responde en "
            "espanol latinoamericano, con tono tecnico y conciso. Cuando propongas "
            "cambios de codigo, prioriza seguridad, pruebas, diffs pequenos y "
            "compatibilidad con el repositorio. No inventes capacidades del sistema: "
            "si falta una herramienta, una credencial o acceso al filesystem, dilo."
        )

    def _ensure_client(self):
        if self.client is not None:
            return self.client
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY no configurada")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Paquete 'openai' no instalado; ejecuta pip install -r requirements.txt"
            ) from exc
        self.client = OpenAI(api_key=self.api_key, timeout=self.timeout_s)
        return self.client

    def ask(
        self,
        prompt: str,
        context_extra: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> TerraCodeResponse:
        max_output_tokens = max(256, min(int(max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS), 8192))
        client = self._ensure_client()
        user_text = prompt
        if context_extra and context_extra.strip():
            user_text = f"{prompt}\n\nCONTEXTO ADICIONAL:\n{context_extra.strip()}"

        t0 = time.perf_counter()
        response = client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=user_text,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": self.reasoning_effort},
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        text = self._extract_text(response)
        in_tok, out_tok, cache_read_tok = self._extract_usage(response)
        cost = 0.0
        if self.tracker:
            cost = self.tracker.record(
                self.model,
                input_tokens=max(0, in_tok - cache_read_tok),
                output_tokens=out_tok,
                cache_read_tokens=cache_read_tok,
            )
        return TerraCodeResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    async def ask_async(
        self,
        prompt: str,
        context_extra: str | None = None,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> TerraCodeResponse:
        max_output_tokens = max(256, min(int(max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS), 8192))
        client = self._ensure_async_client()
        user_text = prompt
        if context_extra and context_extra.strip():
            user_text = f"{prompt}\n\nCONTEXTO ADICIONAL:\n{context_extra.strip()}"

        t0 = time.perf_counter()
        response = await client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=user_text,
            max_output_tokens=max_output_tokens,
            reasoning={"effort": self.reasoning_effort},
        )
        latency_ms = (time.perf_counter() - t0) * 1000

        text = self._extract_text(response)
        in_tok, out_tok, cache_read_tok = self._extract_usage(response)
        cost = 0.0
        if self.tracker:
            cost = self.tracker.record(
                self.model,
                input_tokens=max(0, in_tok - cache_read_tok),
                output_tokens=out_tok,
                cache_read_tokens=cache_read_tok,
            )
        return TerraCodeResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cache_read_tokens=cache_read_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
        )

    def _ensure_async_client(self):
        async_client = getattr(self, "async_client", None)
        if async_client is not None:
            return async_client
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY no configurada")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Paquete 'openai' no instalado; ejecuta pip install -r requirements.txt"
            ) from exc
        self.async_client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout_s)
        return self.async_client

    def warmup(self) -> None:
        """Importa y construye clientes sin hacer requests de red."""
        if not self.configured:
            return
        self._ensure_client()
        if self.api_key or getattr(self, "async_client", None) is not None:
            self._ensure_async_client()

    @staticmethod
    def _extract_text(response: Any) -> str:
        direct = getattr(response, "output_text", None)
        if direct:
            return str(direct)
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_usage(response: Any) -> tuple[int, int, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        input_details = getattr(usage, "input_tokens_details", None)
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", input_tokens)
            output_tokens = usage.get("output_tokens", output_tokens)
            input_details = usage.get("input_tokens_details", input_details)
        cached_tokens = getattr(input_details, "cached_tokens", None)
        if isinstance(input_details, dict):
            cached_tokens = input_details.get("cached_tokens", cached_tokens)
        return int(input_tokens or 0), int(output_tokens or 0), int(cached_tokens or 0)


# Alias de compatibilidad para integraciones externas que aun importen el nombre
# anterior. JARVIS ya usa Terra internamente y no lee el slug GPT-5.5.
GPT55CodeReasoner = TerraCodeReasoner
GPT55CodeResponse = TerraCodeResponse
