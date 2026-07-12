# Jarvis Native Audio

This folder contains the optional C++ audio backend used by `audio/aec.py`.

Build on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File audio\native\build.ps1
```

The script writes:

```text
audio\native\bin\jarvis_audio_native.dll
```

Runtime flags:

```env
JARVIS_AUDIO_NATIVE=auto      # default: try DLL, fall back to Python
JARVIS_AUDIO_NATIVE=off       # force Python audio DSP
JARVIS_AUDIO_NATIVE=required  # fail if DLL is missing
JARVIS_AUDIO_NATIVE_LIB=...   # optional custom DLL path
JARVIS_AUDIO_NATIVE_TAPS=1024 # optional NLMS tail length (~64 ms @16k)
```

The first native target is AEC for wake-word barge-in. Python remains the
orchestrator and safety layer; the DLL only handles hot audio DSP.
