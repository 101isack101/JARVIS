# Changelog

Todas las versiones relevantes de JARVIS se documentan aqui.

## Unreleased

- Tool `ask_gpt55_code` y wrapper OpenAI Responses API para delegar explicitamente
  codigo, debugging, arquitectura de software y modo agentico a GPT 5.5.
- `JARVIS_AGENTIC_CODE_MODEL` y `JARVIS_OPENAI_TIMEOUT_S` documentados; la ruta
  requiere `OPENAI_API_KEY` y degrada con error claro si falta.
- Skills runtime con `jarvis_skill(list|get|activate|deactivate|status|reload)`,
  catálogo builtin y carga de skills JSON locales desde `skills/local/`.
- Importacion automatica de skills documentadas tipo Codex desde
  `JARVIS_SKILL_IMPORT_DIRS` (`*/SKILL.md`), incluyendo las skills personales de
  `C:\Users\Isaac\.codex\skills`.
- Fix de estabilidad en sesiones largas: errores Gemini Live 1007 ahora limpian
  el `session_resumption_handle` y fuerzan reconexion limpia con backoff, evitando
  bucles de reconexion con estado corrupto.
- File Organizer seguro con `file_organizer(status|scan|plan|preview|apply)`.
- Whitelist configurable de roots via `JARVIS_ORGANIZER_ROOTS`.
- Modo independiente `JARVIS_ORGANIZER_MODE` para permitir movimientos reales sin cambiar el modo global de Jarvis.
- Accion `preview` para crear una carpeta visible de vista previa sin mover archivos originales.
- Soporte para iconos del escritorio: accesos directos `.lnk`/`.url` y carpetas top-level con `include_folders=true`.
- Tool `desktop_icons` para reacomodar visualmente posiciones de iconos en el escritorio de Windows.
- Planes persistidos en `data/file_organizer/plans/` y aplicacion con aprobacion HITL.
- Sin borrado, sin overwrite, bloqueo de secretos/directorios internos y movimientos cross-volume.

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
