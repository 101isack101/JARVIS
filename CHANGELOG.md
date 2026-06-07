# Changelog

Todas las versiones relevantes de JARVIS se documentan aqui.

## v1.02 - Vision por camara

Fecha: 2026-06-07

- Camara on-demand con `camera_look` y hotkey `Ctrl+Shift+C`.
- Modo vision continuo con `camera_watch`, preview live y auto-stop.
- Crosshair semantico con `camera_focus`.
- FPS del modo vision configurado a 3.0 para mejor fluidez.

## v1.00 - Baseline UI/UX

Fecha: 2026-05-31

Primera version formalmente versionada de JARVIS.

- Overlay redisenado con identidad visual tipo nucleo JARVIS.
- UI web premium Tailwind agregada como superficie principal (`JARVIS_UI=web`).
- Bridge local SSE para sincronizar estado, chat, audio, aprobaciones, memoria y budget.
- Recall temporal de sesiones con `jarvis_session_recall` para recuperar "ayer" y conversaciones previas.
- Animaciones diferenciadas por estado: idle, listening, thinking, speaking y blocked.
- Reactor visual reactivo al audio real de Gemini.
- Modo compacto premium para monitoreo rapido.
- Command Center con resumen, memoria, eventos, logs y telemetria local.
- Dialogo HITL de aprobacion con jerarquia visual de riesgo.
- Telemetria de budget visible en footer.
- Memoria Obsidian/RAG, herramientas `jarvis_*`, Claude reasoner, vision/screen capture y action executor seguro como base funcional existente.
