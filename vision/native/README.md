# JARVIS vision native sidecar

Sidecar C++ opcional para capturar webcam fuera de Python. Usa Windows Media
Foundation para leer la camara y Windows Imaging Component para emitir JPEG.

## Build

```powershell
powershell -ExecutionPolicy Bypass -File vision\native\build.ps1 -Clean
```

El binario queda en `vision/native/bin/jarvis_vision_sidecar.exe`.

## Runtime

```env
JARVIS_VISION_NATIVE=auto
# off       -> siempre OpenCV/Python
# auto      -> intenta sidecar y cae a OpenCV si falla
# required  -> falla si el sidecar no esta disponible

JARVIS_VISION_NATIVE_EXE=
```

El protocolo stdout es binario:

```text
magic[4] = "JVF1"
uint32 width
uint32 height
uint32 jpeg_payload_len
uint64 timestamp_ns
bytes  jpeg
```
