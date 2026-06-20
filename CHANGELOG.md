# Changelog

Todas las versiones relevantes de JARVIS se documentan aqui.

## Unreleased

### Added

- KSI Fase 4 - auto-critica en escritura: nuevo modulo `memory/self_improvement/write_critique.py`.
  `jarvis_remember` refina de forma autonoma los `content` vagos antes de persistirlos
  (deteccion determinista bilingue ES/EN + reasoner presupuestado con JSON self-heal).
  Anexa el marcador `<!-- ksi-doubt:vague -->` cuando queda duda. Stateless y fail-safe.
  Gated por `JARVIS_KSI_WRITE_CRITIQUE` (default OFF).
- KSI Fase 3 - RAG auto-curado: el curador (`RetrievalCurator`) mide el uso real
  de cada chunk por el reasoner (atribucion por coseno respuesta<->chunk) y
  re-rankea las recuperaciones futuras con un `quality_factor` lineal acotado a
  [0.6, 1.4]. Autonomo y no destructivo (estado desechable en `data/rag_usage.json`,
  nunca toca el indice FAISS). Housekeeping de decay + purga al cierre de sesion.
  Gated por `JARVIS_RAG_CURATION`. Fail-safe en cada seam: jamas degrada la respuesta.
- Auto-mejora recursiva de conocimiento (Fase 1): al cerrar sesión, JARVIS consolida
  sus Project Memory Cards de forma aditiva (recalcula confianza con decaimiento/refuerzo,
  detecta duplicados por coseno) y propone fusiones/contradicciones vía el morning
  briefing (HITL). Modelo evento→proyección regenerable; fail-safe total; métricas de
  salud en `Jarvis Memory/self-improvement/`. Paquete `memory/self_improvement/`.
- Detección de lagunas de conocimiento (KSI Fase 2): al cerrar sesión, JARVIS detecta cards
  pobres, hechos obsoletos y contradicciones abiertas, formula preguntas naturales (reasoner
  presupuestado) y las persiste en la sección "Preguntas abiertas" de cada card. El motor de
  proactividad las pregunta de forma fluida en conversación y briefing, con anti-spam (cooldown
  /dedup por gap_id) y auto-retiro determinista al responderlas. Módulo `memory/self_improvement/gaps.py`.
- Briefing matutino hablado: al arrancar, JARVIS narra en voz un resumen con
  pendientes del vault, estado de los repos git y titulares de IA del día
  (reutiliza las notas del AI News Agent). Idempotente por proceso (no se repite
  en reconexiones), fail-safe y desactivable con `JARVIS_MORNING_BRIEF=false`.
  Calendario de Google opcional en Fase 2 (`JARVIS_BRIEF_CALENDAR=true`).
- Tool `ask_gpt55_code` y wrapper OpenAI Responses API para delegar explicitamente
  codigo, debugging, arquitectura de software y modo agentico a GPT 5.5.
- `JARVIS_AGENTIC_CODE_MODEL` y `JARVIS_OPENAI_TIMEOUT_S` documentados; la ruta
  requiere `OPENAI_API_KEY` y degrada con error claro si falta.
- Delegaciones GPT/Claude ajustadas sin recortar calidad: timeouts configurables
  por `.env`, routing mas estricto para evitar escalados innecesarios y prewarm
  local de clientes para quitar latencia de primera llamada.
- Modo informe largo para GPT/Claude: si Gemini pide muchos tokens porque Isaac
  solicito un informe/documento/analisis amplio, JARVIS usa timeouts extendidos
  configurables sin hacer lenta la conversacion normal.
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

## v1.03 - UI 100% funcional + nucleo de energia

Fecha: 2026-06-13

### Nucleo de energia

- Rediseno del orb central como reactor de plasma reactivo a la voz: centro
  blanco-incandescente que palpita, vortices de gas contrarrotantes con luz
  aditiva (`mix-blend-mode: screen` + blur), filamentos electricos y un bloom
  que se expande/contrae segun `--voice-energy` (telemetria de audio).

### Controles de la UI ahora funcionales (antes decorativos)

- Caja de texto + `Send` (Enter o boton): inyecta un turno real en la sesion
  Gemini via `sendText` -> `JarvisSession.send_text`. Se deshabilita sin conexion.
- Boton `Mic`: alterna PTT <-> LIBRE (`toggleMode` -> `_apply_listen_mode`).
- Boton `Keyboard`: enfoca la caja de mensaje.
- Boton `Settings`: popover con version, conexion, modo y "Shutdown JARVIS".
- Boton `Camera`: toggle real de captura (`toggleCamera` -> `_on_capture_camera`).
- Botones `Refresh` de System Stats y Weather: re-piden datos al backend.

### Datos reales (antes hardcodeados)

- System Stats: CPU/RAM/Disco reales via `psutil`, empujados por snapshot/SSE.
- Weather: clima real via Open-Meteo + geolocalizacion por IP (sin API key),
  en hilo daemon con timeout y fallback. Override con `JARVIS_WEATHER_LAT/LON/PLACE`
  y desactivable con `JARVIS_WEATHER=false`.

### Puente UI<->backend

- `/command` ahora acepta `args` (payload); `sendCommand(command, token, args)`.
- Dispatcher `Jarvis._on_ui_command` enruta comandos de la UI web a callbacks
  existentes; el overlay clasico Tkinter los ignora con gracia.

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
