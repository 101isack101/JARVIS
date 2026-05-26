"""
Fase 0 - Spike de viabilidad: Gemini Live API.

Que hace:
  1. Conecta al WS de Gemini Live con la API key del .env
  2. Envia un mensaje de texto en espanol
  3. Recibe respuesta de audio (PCM 16kHz) + transcript
  4. Reproduce el audio en speakers
  5. Mide y reporta latencia end-to-end

Criterio de exito de Fase 0:
  - Conexion establecida sin error de auth/region
  - Latencia round-trip < 1500ms (relajado para spike sin barge-in)
  - Audio reproducible sin glitches

Como ejecutar:
  & "H:\\Python311\\python.exe" scripts\\spike_gemini_live.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Asegurar UTF-8 en consola Windows (Regla 1 de feedback Isaac)
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# .env esta un nivel arriba de scripts/
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY or API_KEY.startswith("your_"):
    print("[ERROR] GEMINI_API_KEY no configurado en .env")
    print("Obtener en: https://aistudio.google.com/app/apikey")
    sys.exit(1)

MODEL = "gemini-3.1-flash-live-preview"  # Live API multimodal (Gemini 3.1, mas reciente)
# Fallback estable: "gemini-2.5-flash-native-audio-latest"
VOICE = os.environ.get("GEMINI_VOICE", "Aoede")
TEST_MESSAGE = "Hola Jarvis. Di una frase corta para probarte. Solo dos segundos."


async def run_spike() -> None:
    client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1beta"})

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text="Eres Jarvis, un asistente en espanol. Responde corto.")]
        ),
    )

    print(f"[INFO] Conectando a {MODEL} (voz: {VOICE})...")
    t_connect_start = time.perf_counter()

    async with client.aio.live.connect(model=MODEL, config=config) as session:
        t_connected = time.perf_counter()
        print(f"[OK] Conectado en {(t_connected - t_connect_start)*1000:.0f}ms")

        print(f"[INFO] Enviando: {TEST_MESSAGE!r}")
        t_send = time.perf_counter()
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": TEST_MESSAGE}]},
            turn_complete=True,
        )

        # Acumular audio PCM 24kHz mono int16 (formato de salida de Gemini Live)
        audio_chunks: list[bytes] = []
        transcript_parts: list[str] = []
        t_first_audio: float | None = None

        async for response in session.receive():
            if response.server_content and response.server_content.model_turn:
                for part in response.server_content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        if t_first_audio is None:
                            t_first_audio = time.perf_counter()
                            print(
                                f"[OK] Primer chunk audio en "
                                f"{(t_first_audio - t_send)*1000:.0f}ms (TTFB)"
                            )
                        audio_chunks.append(part.inline_data.data)
                    if part.text:
                        transcript_parts.append(part.text)
            if response.server_content and response.server_content.turn_complete:
                break

        t_done = time.perf_counter()
        total_ms = (t_done - t_send) * 1000
        ttfb_ms = (t_first_audio - t_send) * 1000 if t_first_audio else float("nan")
        n_bytes = sum(len(c) for c in audio_chunks)

        print(f"\n[METRICAS]")
        print(f"  TTFB (Time To First Byte):  {ttfb_ms:>6.0f} ms")
        print(f"  Total round-trip:           {total_ms:>6.0f} ms")
        print(f"  Audio recibido:             {n_bytes} bytes ({n_bytes/48000:.2f}s @ 24kHz)")
        if transcript_parts:
            print(f"  Transcript:                 {''.join(transcript_parts)!r}")

        if audio_chunks:
            audio_bytes = b"".join(audio_chunks)
            audio_np = np.frombuffer(audio_bytes, dtype=np.int16)

            wav_path = ROOT / "data" / "spike_response.wav"
            wav_path.parent.mkdir(exist_ok=True)
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes(audio_bytes)
            print(f"  Audio guardado en:          {wav_path}")

            print("\n[INFO] Reproduciendo respuesta...")
            sd.play(audio_np, samplerate=24000)
            sd.wait()
            print("[OK] Spike completo.")
        else:
            print("[WARN] No se recibio audio. Revisar config de modalities.")


def main() -> int:
    try:
        asyncio.run(run_spike())
        return 0
    except KeyboardInterrupt:
        print("\n[ABORT] Interrumpido por usuario")
        return 130
    except Exception as exc:
        print(f"\n[ERROR] {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
