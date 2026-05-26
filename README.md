# JARVIS — Agente Conversacional en Tiempo Real

Asistente estilo Gemini Live con voz bidireccional, visión de pantalla, computer use sobre Windows, y Claude 4.6 Sonnet como reasoner profundo.

> **Status:** Copiloto local funcional. Voz bidireccional, overlay, hotkeys, memoria RAG, Claude tool, vision/screen capture, action executor seguro, modos, telemetry y budgets ya existen.

---

## Arquitectura corta

- **Gemini Live** (`google-genai`): voz speech-to-speech nativo, barge-in, vision
- **Claude 4.6 Sonnet** (`anthropic`): tool `ask_claude_deep` para razonamiento profundo
- **Overlay tkinter**: invisible a Zoom/Teams via `WDA_EXCLUDEFROMCAPTURE`
- **Vision**: `Ctrl+Shift+S`, `Ctrl+Alt+S` y tool `screen_look` capturan pantalla/region para Gemini
- **Computer use seguro**: `actions/` con allowlist read-only, dry-run en `JARVIS_MODE=dev`
- **Obsidian MCP**: `mcp_obsidian/` expone operaciones seguras para editar, mover y organizar notas/carpetas

---

## Setup (primera vez)

### 1. Instalar dependencias en Python global

```powershell
& "H:\Python311\python.exe" -m pip install -r requirements.txt
```

> Isaac usa Python global en `H:\Python311` (sin venv). Esto puede tardar 5-15 min la primera vez por `torch` (~150 MB).

### 2. Configurar `.env`

```powershell
Copy-Item .env.example .env
```

Edita `.env` y rellena:
- **`GEMINI_API_KEY`** — obtener en [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- **`ANTHROPIC_API_KEY`** — ya tienes una en `Interview_Copilot\.env`, copiala

> **Importante:** `.env` debe ser UTF-8 **sin BOM**. NO uses `Set-Content -Encoding utf8` (añade BOM). Usar:
> ```powershell
> $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
> [System.IO.File]::WriteAllBytes(".env", $bytes)
> ```

---

## Fase 0 — Smoke tests

Ejecutar los dos spikes en orden. Ambos deberían terminar en <30s.

### Spike 1: Claude reasoner (más rápido, sin audio)

```powershell
& "H:\Python311\python.exe" scripts\spike_claude_reasoner.py
```

**Éxito esperado:**
- 1er request: respuesta en español sin error
- 2do request: `cache_read_input_tokens > 0` → prompt caching funciona
- Latencia 2do < latencia 1ro

### Spike 2: Gemini Live WebSocket + audio

```powershell
& "H:\Python311\python.exe" scripts\spike_gemini_live.py
```

**Éxito esperado:**
- Conexión WS sin error de auth/region
- TTFB (Time To First Byte audio) < 1500 ms
- Audio reproducible en speakers sin glitches
- Archivo `data\spike_response.wav` creado

**Si falla:**
- `403 / 401`: API key inválida o tier free no soporta Live
- `Region not supported`: tu cuenta no tiene acceso al modelo Live preview. Solicitar acceso en AI Studio o cambiar región.
- `Model not found`: el ID del modelo cambió. Verificar en docs Google AI.

---

## Estado / proximos pasos

- **Hecho:** MVP voz bidireccional con overlay tkinter + PTT + escucha libre
- **Hecho:** Memoria Obsidian con FAISS, indexacion incremental y tools `jarvis_*`
- **Hecho:** Telemetry con budget session/daily/weekly via SQLite
- **Hecho:** Claude como tool callable desde Gemini
- **Hecho:** Screen capture + vision por hotkey/tool
- **Hecho:** Action executor read-only con allowlist
- **Hecho:** MCP local para Obsidian (`obsidian_mcp`) con crear/editar/mover/listar/linkear
- **Hecho:** Reconexión limpia ante `go_away` de Gemini Live y estado visible de conexión en overlay
- **Hecho:** Preferencias persistentes en `data/preferences.json` (notas granulares por tema; no forzar respuestas más cortas)
- **Hecho:** Retención automática de screenshots vía `JARVIS_SCREENSHOT_RETENTION_HOURS`
- **Pendiente:** Autopilot con confirmaciones y acciones de escritura controladas
- **Pendiente:** Dashboard/log viewer para inspeccionar decisiones de Jarvis

---

## Seguridad operativa

- **HITL:** comandos no-read-only y operaciones MCP de escritura piden aprobacion visual en tkinter. Sin broker/UI, fallan cerrado.
- **Sandbox de rutas:** `SafeActionExecutor` solo trabaja dentro del root permitido de Jarvis; rutas fuera se bloquean con `Path.resolve()` + `relative_to()`.
- **Secretos:** RAG/indexer omiten paths sensibles (`.env`, `.pem`, keys, credenciales) y redactan patrones de API keys antes de indexar o responder.
- **Logs:** tool arguments y mensajes largos se redactan/truncan antes de escribirse en `data/jarvis.log`.
- **Screenshots:** las capturas en `data/screenshots` se limpian automáticamente según `JARVIS_SCREENSHOT_RETENTION_HOURS` (24h por defecto).
- **Kill-switch:** `Ctrl+Alt+Q` usa salida dura (`os._exit(130)`) para matar Jarvis sin esperar threads/asyncio.
- **Borrado Obsidian:** `delete_path` requiere `JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=true` y, desde Jarvis, aprobacion HITL. Mantenerlo en `false` por defecto.

---

## Hotkeys (planeadas)

| Hotkey | Acción |
|---|---|
| `Ctrl` (hold) | Push-to-talk |
| `Ctrl+Shift+M` | Toggle modo escucha libre (VAD) |
| `Ctrl+Shift+S` | Capturar pantalla completa -> Gemini |
| `Ctrl+Alt+S` | Capturar region seleccionada -> Gemini |
| `Ctrl+Alt+P` | Pausar acciones (voz sigue) |
| `Ctrl+Alt+Q` | **Kill-switch** — aborta todo y cierra |

---

## Obsidian MCP

Jarvis lanza un servidor MCP local por stdio cuando necesita operar el vault.
La tool conversacional es `obsidian_mcp(operation, ...)`.

Operaciones disponibles:
- `list_folder`, `read_note`
- `create_folder`, `create_note`, `update_note`, `append_note`
- `move_path` para mover o renombrar notas/carpetas
- `link_notes` para conectar nodos
- `delete_path`, desactivada salvo `JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=true`

Smoke test manual:

```powershell
& "H:\Python311\python.exe" -m mcp_obsidian.server
```

Normalmente no hay que correrlo a mano; Jarvis lo levanta como subproceso MCP.

---

## Estructura

```
Jarvis/
├── jarvis.py                    # Entry point (Fase 1+)
├── overlay/                    # tkinter UI
├── audio/                      # capture, playback, VAD
├── gemini/                     # GeminiLiveSession + tools + system prompt
├── claude/                     # ask_claude_deep wrapper
├── actions/                    # Computer use con allowlist
├── vision/                     # screen capture
├── mcp_obsidian/                # MCP stdio server/client para Obsidian
├── memory/                     # SQLite + FAISS RAG
├── tests/                      # pytest
├── scripts/                    # Spikes de Fase 0
└── data/                       # SQLite, FAISS index, WAVs de debug
```
