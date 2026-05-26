"""
Reproduce las 5 voces ya generadas en data/voices/.
No llama a la API, solo lee los WAV locales. Cero costo.

Uso:
  & "H:\\Python311\\python.exe" scripts\\replay_voices.py
  & "H:\\Python311\\python.exe" scripts\\replay_voices.py aoede     # solo una
  & "H:\\Python311\\python.exe" scripts\\replay_voices.py aoede kore # varias
"""

import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
VOICES_DIR = ROOT / "data" / "voices"

VOICE_DESC = {
    "aoede": "femenina suave neutral",
    "charon": "masculina profunda",
    "fenrir": "masculina joven energica",
    "kore": "femenina profesional",
    "puck": "neutral juguetona",
}


def play(path: Path) -> None:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16)
    sd.play(audio, samplerate=rate)
    sd.wait()


def main() -> int:
    if not VOICES_DIR.exists():
        print(f"[ERROR] No existe {VOICES_DIR}. Corre primero spike_voice_comparison.py")
        return 1

    requested = [a.lower() for a in sys.argv[1:]]
    if requested:
        names = [n for n in requested if n in VOICE_DESC]
        invalid = [n for n in requested if n not in VOICE_DESC]
        if invalid:
            print(f"[WARN] No reconocidas: {invalid}. Validas: {list(VOICE_DESC)}")
    else:
        names = list(VOICE_DESC.keys())

    print(f"Reproduciendo {len(names)} voz(es)...\n")
    for i, name in enumerate(names, 1):
        path = VOICES_DIR / f"{name}.wav"
        if not path.exists():
            print(f"  [SKIP] {name} - falta {path.name}")
            continue
        print(f"  {i}/{len(names)} -> {name.capitalize():8s} ({VOICE_DESC[name]})")
        time.sleep(0.5)
        play(path)
        time.sleep(0.4)

    print("\n[OK] Fin de reproduccion.")
    print("Para repetir una sola: replay_voices.py charon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
