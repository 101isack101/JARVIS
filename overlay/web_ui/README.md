# JARVIS Web UI

Interfaz web premium para JARVIS v1.00, construida con Tailwind CSS, Space Grotesk, Inter y JetBrains Mono.

## Abrir demo

Abrir `overlay/web_ui/index.html?demo=1` en el navegador.

La demo corre sin backend. En runtime real, `overlay.web_overlay.WebJarvisOverlay`
sirve esta carpeta y conecta el navegador por `/state`, `/events`, `/approval`
y `/command`.

## Usar en Jarvis

Por defecto `jarvis.py` usa el overlay Tkinter clasico. La UI web queda como
opcion visual experimental y debe activarse explicitamente.

```powershell
& "H:\Python311\python.exe" jarvis.py
```

Variables utiles:

- `JARVIS_UI=tk` usa el overlay Tkinter clasico.
- `JARVIS_UI=web` usa la interfaz web premium.
- `JARVIS_WEB_UI_PORT=8765` define el puerto preferido; si esta ocupado, Jarvis intenta los siguientes.
- `JARVIS_WEB_UI_OPEN_BROWSER=false` no abre el navegador automaticamente.
- `JARVIS_WEB_UI_AUDIO_FPS=30` limita las actualizaciones visuales de audio para no competir con la respuesta de Jarvis.
- `JARVIS_SESSION_RECENT_LIMIT=5` define cuantas sesiones recientes se inyectan como mapa temporal al arrancar.
- Si necesitas ocultar la ventana de capturas/OBS/Zoom, usa `JARVIS_UI=tk`;
  los navegadores externos no heredan `WDA_EXCLUDEFROMCAPTURE`.

## API de puente

Funciones disponibles:

- `JARVIS_UI.setState("idle" | "listening" | "thinking" | "speaking" | "blocked")`
- `JARVIS_UI.setMode("PTT" | "LIBRE")`
- `JARVIS_UI.setConnectionStatus(status, detail)`
- `JARVIS_UI.appendInput(text)`
- `JARVIS_UI.appendOutput(text)`
- `JARVIS_UI.clearTranscripts()`
- `JARVIS_UI.logEvent(message, level)`
- `JARVIS_UI.feedAudioLevel(level)`
- `JARVIS_UI.feedAudioPcm(samples)`
- `JARVIS_UI.showApproval(action)`
- `JARVIS_UI.hideApproval(approved)`
- `JARVIS_UI.toggleCompact()`
- `JARVIS_UI.updateMemoryStats(stats)`
- `JARVIS_UI.updateBudget(budget)`
- `JARVIS_UI.applySnapshot(snapshot)`

## Integracion actual

`WebJarvisOverlay` conserva los metodos usados por `jarvis.py`:
`set_state`, `set_mode`, `set_connection_status`, `append_input`,
`append_output`, `feed_voice_audio`, `log_event`, `show_approval`,
`record_memory_tool_start`, `record_memory_tool_end`, `reset_transcripts`,
`close` y `run`.

El audio de Gemini se convierte a nivel RMS en Python y se envia al navegador
como `feedAudioLevel(level)`, por eso las ondas del nucleo reaccionan a la voz
real sin mandar chunks PCM grandes por JSON. Ademas, esa salida visual se
limita a FPS configurable para que la UI no aumente la latencia de respuesta.
