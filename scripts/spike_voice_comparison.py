"""
Mini-spike: compara las 5 voces oficiales de Gemini Live en espanol.

Genera la misma frase con cada voz, las guarda en data/voices/ y las
reproduce secuencialmente con anuncio del nombre antes de cada una.

Como ejecutar:
  & "H:\\Python311\\python.exe" scripts\\spike_voice_comparison.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv
from google import genai
from google.genai import types

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = "gemini-3.1-flash-live-preview"

# Frase de prueba: incluye tildes, numero, y dos frases para evaluar entonacion
TEST_PHRASE = (
    "Hola Isaac, soy Jarvis. Estoy listo para conversar contigo en tiempo real "
    "y ayudarte con tus proyectos. Que necesitas hoy?"
)

VOICES = ["Aoede", "Charon", "Fenrir", "Kore", "Puck"]
VOICE_DESC = {
    "Aoede": "femenina suave neutral",
    "Charon": "masculina profunda",
    "Fenrir": "masculina joven energica",
    "Kore": "femenina profesional",
    "Puck": "neutral juguetona",
}


async def synthesize(client: genai.Client, voice: str) -> bytes:
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
        system_instruction=types.Content(
            parts=[types.Part(text="Responde EXACTAMENTE con la frase que se te pida, sin agregar nada.")]
        ),
    )
    audio_chunks: list[bytes] = []
    async with client.aio.live.connect(model=MODEL, config=config) as session:
        await session.send_client_content(
            turns={
                "role": "user",
                "parts": [{"text": f"Di exactamente esto: '{TEST_PHRASE}'"}],
            },
            turn_complete=True,
        )
        async for response in session.receive():
            if response.server_content and response.server_content.model_turn:
                for part in response.server_content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        audio_chunks.append(part.inline_data.data)
            if response.server_content and response.server_content.turn_complete:
                break
    return b"".join(audio_chunks)


def save_wav(path: Path, audio_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(audio_bytes)


def play_blocking(audio_bytes: bytes) -> None:
    audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
    sd.play(audio_np, samplerate=24000)
    sd.wait()


async def main_async() -> None:
    client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1beta"})
    out_dir = ROOT / "data" / "voices"

    print("=" * 60)
    print("Comparativa de voces - Gemini Live (espanol)")
    print("=" * 60)
    print(f"Frase de prueba: {TEST_PHRASE!r}\n")

    samples: list[tuple[str, bytes]] = []
    for voice in VOICES:
        print(f"[GEN] {voice:8s} ({VOICE_DESC[voice]})...", end=" ", flush=True)
        try:
            audio = await synthesize(client, voice)
            wav_path = out_dir / f"{voice.lower()}.wav"
            save_wav(wav_path, audio)
            print(f"OK ({len(audio)/48000:.2f}s) -> {wav_path.name}")
            samples.append((voice, audio))
        except Exception as exc:
            print(f"FAIL: {type(exc).__name__}: {exc}")

    if not samples:
        print("\n[ERROR] Ninguna voz se genero correctamente.")
        return

    print(f"\n[INFO] Reproduciendo las {len(samples)} voces. Apunta cual te gusta mas.\n")
    for i, (voice, audio) in enumerate(samples, 1):
        print(f"  {i}/{len(samples)} -> {voice} ({VOICE_DESC[voice]})")
        await asyncio.sleep(0.4)
        play_blocking(audio)
        await asyncio.sleep(0.3)

    print(f"\n[OK] WAVs guardados en: {out_dir}")
    print("Podes volver a reproducir cualquiera con:")
    print(f'  & "H:\\Python311\\python.exe" -c "import sounddevice as sd, soundfile as sf; ' \
          f"d,r=sf.read(r'{out_dir}\\\\aoede.wav'); sd.play(d,r); sd.wait()\"")


def main() -> int:
    try:
        asyncio.run(main_async())
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
