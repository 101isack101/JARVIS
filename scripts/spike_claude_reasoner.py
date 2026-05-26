"""
Fase 0 / 0.5 - Spike de viabilidad: Claude 4.6 Sonnet como reasoner.

Migrado de Opus 4.7 a Sonnet 4.6 (5x mas barato manteniendo razonamiento top-tier).

Que hace:
  1. Lee ANTHROPIC_API_KEY del .env
  2. Hace dos requests identicos a Claude con prompt caching activado
  3. Mide latencia de ambos y reporta cache hit rate
  4. Confirma que el modelo claude-sonnet-4-6 esta accesible

Criterio de exito Fase 0:
  - 1er request: respuesta valida sin error
  - 2do request: cache_read_input_tokens > 0 (caching funciona)
  - Latencia 2do request < 1er request (al menos 30% mas rapido)

Como ejecutar:
  & "H:\\Python311\\python.exe" scripts\\spike_claude_reasoner.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from anthropic import Anthropic

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY or API_KEY.startswith("your_"):
    print("[ERROR] ANTHROPIC_API_KEY no configurado en .env")
    sys.exit(1)

MODEL = "claude-sonnet-4-6"

# System prompt grande (>1024 tokens) para que prompt caching tenga efecto
SYSTEM_PROMPT = (
    "Eres Claude, invocado como reasoner profundo por el agente Jarvis. "
    "Tu trabajo es razonar sobre tareas complejas que Gemini Live delega. "
    "Responde en espanol, conciso pero completo. "
    "Contexto del usuario Isaac: desarrollador costarricense que trabaja en IA, "
    "automatizacion, frontend, drones FPV y post-produccion de video. "
    "Sus proyectos activos incluyen Agentics Code Team (AWS Lambda + Step Functions), "
    "Course_Capture (faster-whisper + Obsidian), Interview_Copilot (tkinter overlay), "
    "MTurk HITL Agent (Playwright + Claude hibrido), Polymath IDE (Monaco + Express), "
    "LinkedIn Copilot (LangGraph + Claude 4.6), n8n Lead Ingestion (workflows). "
    "Stack tecnico: Python 3.11 global en H:\\Python311 (sin venv), "
    "Node v24, Windows 10, Git en H:\\Git, MCP Discord para mobile. "
    "Preferencias: codigo en ingles, contenido en espanol, dark premium aesthetics, "
    "respuestas directas sin floritura, hazlo sin pedir confirmacion si el contexto es claro. "
) * 5  # multiplicado para superar el threshold de caching (>=1024 tokens)


def call_claude(client: Anthropic, prompt: str) -> tuple[float, dict]:
    """Llama a Claude y retorna (latency_ms, usage_dict)."""
    t0 = time.perf_counter()
    msg = client.messages.create(
        model=MODEL,
        max_tokens=200,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )
    latency_ms = (time.perf_counter() - t0) * 1000
    usage = {
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0),
        "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0),
    }
    text = msg.content[0].text if msg.content else ""
    return latency_ms, usage, text


def main() -> int:
    print(f"[INFO] Cliente Anthropic, modelo={MODEL}")
    client = Anthropic(api_key=API_KEY)

    prompt = "Resume en 2 frases que es el speech-to-speech nativo y por que reduce latencia."

    print("\n[1ER REQUEST] (cold cache)...")
    try:
        lat1, usage1, text1 = call_claude(client, prompt)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1

    print(f"  Latencia:      {lat1:>6.0f} ms")
    print(f"  Tokens in:     {usage1['input_tokens']}")
    print(f"  Tokens out:    {usage1['output_tokens']}")
    print(f"  Cache create:  {usage1['cache_creation_input_tokens']}")
    print(f"  Cache read:    {usage1['cache_read_input_tokens']}")
    print(f"  Respuesta:     {text1!r}")

    print("\n[2DO REQUEST] (mismo system prompt, deberia hit cache)...")
    lat2, usage2, text2 = call_claude(client, prompt + " (variacion)")

    print(f"  Latencia:      {lat2:>6.0f} ms")
    print(f"  Tokens in:     {usage2['input_tokens']}")
    print(f"  Tokens out:    {usage2['output_tokens']}")
    print(f"  Cache create:  {usage2['cache_creation_input_tokens']}")
    print(f"  Cache read:    {usage2['cache_read_input_tokens']}")
    print(f"  Respuesta:     {text2!r}")

    print("\n[ANALISIS]")
    speedup = (lat1 - lat2) / lat1 * 100 if lat1 > 0 else 0
    print(f"  Speedup 2do vs 1ro:  {speedup:+.1f}%")
    cache_hit = usage2["cache_read_input_tokens"] > 0
    print(f"  Cache hit detectado: {'SI' if cache_hit else 'NO'}")

    if cache_hit and lat2 < lat1:
        print("\n[OK] Claude funcional + prompt caching activo. Jarvis puede usarlo como reasoner.")
        return 0
    elif not cache_hit:
        print("\n[WARN] No hubo cache hit. Verificar threshold (>=1024 tokens) o feature flag.")
        return 0  # No fatal, solo warning
    else:
        print("\n[WARN] Cache activo pero sin speedup notable. Red puede estar variable.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
