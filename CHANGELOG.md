# Changelog

Todas las versiones relevantes de JARVIS se documentan aqui.

## Unreleased

## 1.10 - 2026-09-18

### Security

- MCP paths pasan por `assert_inside_root`.
- `file_organizer.apply` no marca éxito sin destino en disco.
- MCP create/append/move devuelven `verified` + `sha256`.
- Dispatcher corta la misma write 4ª vez (`REPEAT_CAP`).

### Changed

- `.env.example` documenta L1 canario ON; default de código sigue OFF.

## 1.09 - 2026-08-22

Snapshot git de lo que estaba en working tree desde v1.07: incluye el corte
1.08 (19 ago) que no se llego a commitear, mas los follow-ups de voz/tools.

### Changed

- Version unica 1.09 (Python `VERSION`, Tauri, React).

### Voice / tools

- Voz: `ask_grok_deep` ya no bloquea Gemini Live (mismo patron diferido que Terra).
  Follow-ups se entregan solo cuando Isaac no esta en un turno (idle-gated).
- Tools Live se filtran por modo de trabajo; `jarvis_task` disponible en `general`.
- Watchdog de thinking no pone el HUD en idle si hay Grok/Terra/mision en curso.
- Planner L1 tiene plantilla determinista si Terra no esta configurado.
- Telemetria de turno incluye `reason=` (model/blocking_tool/watchdog) y
  `latencySummary` en `/state`.

## 1.08 - 2026-08-19

### Changed

- Reasoner profundo: Claude Sonnet -> **Grok 4.6** (`XAI_API_KEY`, tool `ask_grok_deep`).
  `ask_claude_deep` queda como alias. Telemetria historica de Claude se conserva.
- UI por defecto: `JARVIS_UI=web`. Tkinter sigue como fallback (`JARVIS_UI=tk`).
- Version unica 1.08 (Python, Tauri, React). No hubo commit git; el corte
  viaja dentro de 1.09.

### Security

- El bridge ya no expone `uiToken` en GET `/state`. Token por env `JARVIS_UI_TOKEN`,
  cookie HttpOnly o header. `/state` y `/events` requieren auth. Origin loopback en POST.
- Tauri genera el token, lo pasa al backend y ya no lee `/state` para apagar.
- Alias de PowerShell (`ri`, `sc`, `mi`, ...) clasificados como write/destructive.

### Added

- Tools por modo de trabajo (`memory/tool_sets.py`): computer-use, camara y misiones
  se rechazan en modo `general`.
- `scripts/setup_venv.ps1` para entorno reproducible (`.venv`).
- Tauri resuelve `JARVIS_DIR` / `JARVIS_PYTHON` / `.venv` y avisa si el backend no levanta.

## 1.07 - 2026-07-26

### Added

- GPT-5.6 Terra reemplaza a GPT-5.5 como especialista de codigo y modo agentico:
  `TerraCodeReasoner` (`openai_code/reasoner.py`) sobre la Responses API con
  `reasoning.effort` configurable y variante async. Nueva tool `ask_terra_code`.
  `GPT55CodeReasoner` queda como alias de compatibilidad.
  Config: `JARVIS_TERRA_MODEL`, `JARVIS_TERRA_REASONING_EFFORT` (default high),
  `JARVIS_TERRA_TIMEOUT_S`, `JARVIS_ASK_TERRA_TIMEOUT_S/_LONG_TIMEOUT_S`.
- Tools diferidas en Gemini Live (`gemini/session.py`): `ask_terra_code` devuelve
  acuse inmediato y completa en segundo plano; el resultado se reinyecta como
  follow-up. Gemini responde primero, Terra razona sin bloquear la voz. Los
  resultados sobreviven a reconexiones de Live y solo se cancelan en stop
  explicito de sesion. Solo se difieren tools idempotentes y read-only.
- Telemetria por mision en el Agent Runtime: costo, tokens y duracion medidos
  como delta contra un baseline tomado cuando el worker toma la mision, no
  cuando entra a la cola, para que el tiempo en FIFO no absorba uso ajeno.
  Se congela al terminar. Expuesta en `/state` y en el panel React de misiones.
- Briefing matutino narra el estado de la LLM Wiki (patron Karpathy) del vault:
  nueva fuente fail-safe en `proactivity/morning_brief.py` que lee
  `Wiki/Meta/hot.md`, descarta titulo/frontmatter/instrucciones, aplana
  wikilinks para voz y corta en palabra completa (max 900 chars).
  Config: `JARVIS_BRIEF_WIKI` (default true), `JARVIS_BRIEF_WIKI_HOT` (ruta).
- Precio de `gpt-5.6-terra` en `telemetry/costs.py`; snapshot de precios al
  2026-07-22.

### Security

- Bridge web local rechaza DNS rebinding: `_host_allowed` valida el header
  `Host` contra loopback y la bind address antes de servir cualquier endpoint
  (`overlay/web_overlay.py`). Sin esto, una web maliciosa podia apuntar su
  dominio a 127.0.0.1, volverse same-origin, leer `/state` —que expone
  `uiToken`, transcripts y frames de camara— y luego postear `/command`
  con ese token, llegando a `sendText` y al control del agente.
- OAuth de Spotify valida el parametro `state` con `compare_digest` y escapa el
  `error` del callback en el HTML (`actions/spotify_login_capture.py`). Antes
  aceptaba cualquier callback y reflejaba el error sin escapar.
- `actions/executor.py` endurece los comandos read-only: bloquea `$`, backtick,
  `&` y multilinea, providers de PowerShell (`env:`, `hklm:`, `registry:`...),
  globs en rutas y flags tipo `--no-ignore`/`--hidden` que revelarian archivos
  ignorados. Limites de recursos en lecturas estructuradas.
- El envelope `<terra_result>` marca la salida de la tool como dato no confiable
  y evita que el texto devuelto forje sus propios delimitadores.

### Changed

- `memory/tools.py` se parte: las declaraciones de function calling salen a
  `memory/tool_declarations.py` (1012 lineas menos en el modulo de dispatch).
- Registry de skills apunta a `ask_terra_code` en lugar de `ask_gpt55_code`.

### Removed

- Dependencias muertas: `soundcard`, `pyautogui`, `mss`, `imagehash`.

## 1.06 - 2026-07-11

### Added

- Agent Runtime v0 para misiones persistentes L1 read-only: planner estructurado,
  policy Python con catálogo cerrado, executor background, cancelación cooperativa,
  recovery después de restart/kill, artefactos redactados con SHA-256 y verificación
  obligatoria antes de `completed`.
- Tool `jarvis_task(create|status|list|cancel)` para crear y observar trabajo largo sin
  bloquear Gemini Live. Estado SQLite en `data/agent_runtime/agent_runtime.db`.
- Timeline SSE y panel React de misiones con progreso, estado, errores y cancelación.
- Constitución, especificación, contratos, schema y plan trazable del Agent Runtime en
  `docs/agent_runtime/` y `docs/superpowers/`.

## 1.04 - 2026-06-19

### Added

- Recall de la sesion EN CURSO: `jarvis_current_session_recall` + `build_current_session_block`
  (`memory/session_summary.py`) leen el journal append-only de la sesion abierta. Cubren el hueco
  cuando Gemini Live reconecta o comprime contexto antes de que la sesion se sintetice en nota
  durable. Reinyeccion automatica del contexto vivo al reconectar via `dynamic_context_provider`
  en `gemini/session.py`. Determinista, acotado (turnos <=20, chars <=12000) y fail-safe.
  Config: `JARVIS_CURRENT_SESSION_RECALL_TURNS`/`_CHARS`.
- Study mode con flush en segundo plano: `StudyModeController.flush_background`/`stop_background`
  + worker dedicado vuelcan la evidencia acumulada a la nota sin bloquear la conversacion.
- Umbrales de "tool largo" y prewarm de GPT 5.5: `LONG_TOOL_TOKEN_THRESHOLDS` +
  `_is_long_tool_request` detectan peticiones de informe largo y aplican timeouts extendidos;
  el cliente OpenAI admite inyeccion para warmup sin `api_key` (testeable).
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
