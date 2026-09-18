# JARVIS — Agente Conversacional en Tiempo Real

Asistente estilo Gemini Live con voz bidireccional, visión de pantalla, computer use sobre Windows, GPT-5.6 Terra para codigo/modo agentico complejo y Grok 4.6 como reasoner profundo general.

> **Status:** Copiloto local funcional. Voz bidireccional, overlay, hotkeys, memoria RAG, Claude tool, vision/screen capture, action executor seguro, modos, telemetry y budgets ya existen.

> **Version actual:** v1.10 — V&V de writes locales, REPEAT_CAP, L1 canario documentado

---

## Lanzar JARVIS

**Doble clic en el icono del escritorio** (`JARVIS.lnk` → `jarvis-desktop.exe`).

Esto arranca el shell Tauri que:

1. Spawnea `jarvis.py` con `JARVIS_UI=web JARVIS_SUPERVISED=1`
2. Espera hasta 45 s a que el bridge HTTP levante en `127.0.0.1:8765`
3. Abre la ventana React (1380×860) apuntando al bridge

> **Primera carga:** ~30 segundos mientras JARVIS carga modelos (VAD, embeddings, Obsidian). La ventana aparece cuando el backend está listo.

### Modo legacy (tkinter)

```powershell
& "H:\Python311\python.exe" jarvis.py
# o doble clic en jarvis_run.bat (sin JARVIS_UI=web)
```

---

## Arquitectura corta

- **Gemini Live** (`google-genai`): voz speech-to-speech nativo, barge-in, vision
- **GPT-5.6 Terra** (`openai`, `reasoning.effort=high`): tool `ask_terra_code` para código, debugging, arquitectura y modo agentico complejo. Gemini mantiene la conversación: da una respuesta inicial y Terra mejora el resultado en segundo plano.
- **Grok 4.6** (`openai` + `https://api.x.ai/v1`): tool `ask_grok_deep` para razonamiento profundo general y fallback
- **Audio hibrido C++/Python**: AEC/resample de far-end puede usar `audio/native/bin/jarvis_audio_native.dll` y cae a Python si no esta compilado
- **Vision hibrida C++/Python**: captura de camara puede usar `vision/native/bin/jarvis_vision_sidecar.exe` para liberar Python en modo vision continuo, con fallback OpenCV
- **UI Tauri + React** (`jarvis-desktop.exe`): ventana nativa con React 19 + Tailwind v4 + framer-motion, SSE bridge headless
- **Overlay tkinter** (legacy): interfaz clasica, invisible a Zoom/Teams via `WDA_EXCLUDEFROMCAPTURE`
- **Vision**: `Ctrl+Shift+S`, `Ctrl+Alt+S` y tool `screen_look` capturan pantalla/region para Gemini
- **Computer use seguro**: `actions/` con operaciones read-only estructuradas y validacion de rutas
- **File Organizer seguro**: planifica y mueve archivos locales con whitelist de roots, manifiesto y aprobacion HITL
- **Obsidian MCP**: `mcp_obsidian/` expone operaciones seguras para editar, mover y organizar notas/carpetas
- **English Practice**: tool `english_practice` activa/desactiva practica conversacional de ingles con correcciones breves

---

## Setup (primera vez)

### 1. Instalar dependencias en Python global

```powershell
# Recomendado: venv local
powershell -ExecutionPolicy Bypass -File scripts\setup_venv.ps1

# Alternativa: Python global de Isaac
& "H:\Python311\python.exe" -m pip install -r requirements.txt
```

> Isaac usa Python global en `H:\Python311` (sin venv). Esto puede tardar 5-15 min la primera vez por `torch` (~150 MB).

### 2. Configurar `.env`

```powershell
Copy-Item .env.example .env
```

Edita `.env` y rellena:
- **`GEMINI_API_KEY`** — obtener en [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- **`XAI_API_KEY`** — https://console.x.ai (Grok 4.6)
- **`OPENAI_API_KEY`** — habilita `ask_terra_code`
- **`JARVIS_TERRA_MODEL`** — default `gpt-5.6-terra`
- **`JARVIS_TERRA_REASONING_EFFORT`** — default `high`; ajusta el esfuerzo de razonamiento de Terra.
- **`JARVIS_TERRA_TIMEOUT_S`**, **`JARVIS_ASK_TERRA_TIMEOUT_S`**, **`JARVIS_ASK_CLAUDE_TIMEOUT_S`** — caps de latencia para delegaciones normales. Gemini no espera a Terra: responde primero y recibe su resultado después.
- **`JARVIS_ASK_TERRA_LONG_TIMEOUT_S`**, **`JARVIS_ASK_CLAUDE_LONG_TIMEOUT_S`** — caps automaticos para informes largos cuando la tool pide muchos tokens.

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
& "H:\Python311\python.exe" grok\reasoner.py
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
- **Hecho:** Action executor read-only estructurado, sin shell libre expuesto a Gemini
- **Hecho:** File Organizer local con plan/apply, sin borrado ni overwrite, protegido por HITL
- **Hecho:** MCP local para Obsidian (`obsidian_mcp`) con crear/editar/mover/listar/linkear
- **Hecho:** Reconexión limpia ante `go_away` de Gemini Live y estado visible de conexión en overlay
- **Hecho:** Preferencias persistentes en `data/preferences.json` (notas granulares por tema; no forzar respuestas más cortas)
- **Hecho:** Recall temporal de sesiones (`jarvis_session_recall`) para "ayer", "anoche" y conversaciones previas
- **Hecho:** Retención automática de screenshots vía `JARVIS_SCREENSHOT_RETENTION_HOURS`
- **Hecho:** English Practice Mode activable por voz con `english_practice`
- **Hecho:** UI Tauri + React 19 como modo principal (`jarvis-desktop.exe`): SSE bridge headless, watchdog anti-zombie, ApprovalModal HITL, CameraPanel live, Transcript markdown, Telemetry SVG
- **Hecho:** Agent Runtime v0 con misiones read-only persistentes, ejecución background, cancelación, recovery, evidencia verificada y Mission Control en vivo
- **Hecho:** V&V de outcome en `file_organizer.apply` y writes MCP; tope `REPEAT_CAP` de writes repetidas
- **Pendiente:** Autopilot con confirmaciones y acciones de escritura controladas
- **Pendiente:** Historial ampliado de errores y artefactos en el dashboard

---

## Seguridad operativa

- **HITL:** operaciones MCP de escritura y acciones sensibles piden aprobacion visual en la UI. Sin broker/UI, fallan cerrado.
- **Sandbox de rutas:** `SafeActionExecutor` solo trabaja dentro del root permitido de Jarvis; rutas fuera se bloquean con `Path.resolve()` + `relative_to()`.
- **Comandos:** Gemini solo ve operaciones estructuradas (`list_dir`, `read_file`, `search_text`, `git_status`, etc.); PowerShell libre no se expone como tool conversacional.
- **Organizacion de archivos:** `file_organizer` solo opera sobre roots permitidos (`JARVIS_ORGANIZER_ROOTS` o carpetas de usuario por defecto), crea un plan revisable y solo aplica movimientos con aprobacion HITL. No borra, no sobrescribe, bloquea secretos/directorios internos y evita movimientos entre discos.
- **Secretos:** RAG/indexer omiten paths sensibles (`.env`, `.pem`, keys, credenciales) y redactan patrones de API keys antes de indexar o responder.
- **Logs:** tool arguments y mensajes largos se redactan/truncan antes de escribirse en `data/jarvis.log`; errores operativos quedan además en `data/error_journal.jsonl` para trazabilidad y bug hunt.
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

## English Practice Mode

Se activa por voz con frases como:

- "Activa modo ingles"
- "Vamos a practicar ingles"
- "Hagamos una entrevista en ingles"
- "Haz shadowing conmigo"

Se desactiva con:

- "Desactiva modo ingles"
- "Termina practica de ingles"
- "Volvamos a espanol"

Internamente usa la tool `english_practice(action, level, focus, correction_style)` y cambia el runtime mode a `english`. Mientras esta activo, Jarvis conversa principalmente en ingles, mantiene el flujo y luego da feedback corto: correccion, version natural y una frase para repetir.

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

## File Organizer

Jarvis puede organizar archivos locales con la tool conversacional `file_organizer`.
El flujo es deliberadamente de dos pasos:

1. `status` / `scan` para inspeccionar roots permitidos.
2. `plan` para guardar un manifiesto en `data/file_organizer/plans/`.
3. `preview` para crear una carpeta visible con subcarpetas vacias y `MOVE_PLAN.md`, sin mover originales.
4. `apply` para mover archivos reales, siempre con aprobacion visual.

Para organizar iconos del escritorio, usa `include_folders=true`: incluye
archivos, accesos directos (`.lnk`, `.url`) y carpetas top-level. Para programas,
Jarvis mueve el acceso directo, no la instalacion real en `Program Files` o
`Windows`.

Para reacomodar la posicion visual de los iconos en la pantalla del escritorio,
Jarvis usa la tool `desktop_icons(action="arrange")`. Esta opera sobre el
ListView del escritorio de Windows: no mueve archivos a carpetas, solo cambia
coordenadas visuales. Tambien requiere aprobacion HITL.

En `JARVIS_ORGANIZER_MODE=dev`, `apply` hace dry-run. Si esa variable se omite,
hereda `JARVIS_MODE`. Para ampliar permisos, define `JARVIS_ORGANIZER_ROOTS` en
`.env` con carpetas concretas separadas por `;`.
No apuntes esa variable a `C:\`, `Windows`, `Program Files`, `ProgramData` ni
`AppData`.

---

## Agent Runtime v0

El runtime de misiones permite que JARVIS investigue objetivos locales de varios pasos
sin bloquear la conversación. Está desactivado por defecto; para probarlo, configura:

```dotenv
JARVIS_AGENT_RUNTIME_ENABLED=true
```

Ejemplos naturales:

```text
evalúa el estado técnico de JARVIS y prioriza lo siguiente
cómo va la misión
lista mis misiones recientes
cancela la misión msn_...
```

Gemini usa internamente `jarvis_task(create|status|list|cancel)`. `create` devuelve un
`mission_id` inmediatamente y el worker continúa en background. El estado durable se
guarda en `data/agent_runtime/agent_runtime.db`; la evidencia redactada vive en
`data/agent_runtime/artifacts/`. Tras un cierre duro, el siguiente arranque reconcilia los
steps sin repetir artefactos confirmados.

### Mission Control

El icono de JARVIS en la barra superior —o una misión en el panel lateral— abre el
Mission Control. Muestra el plan, pasos y verificaciones, timeline de hasta 50 eventos,
duración y deltas de tokens/costo por proveedor. Mientras está abierto refresca el detalle
de la misión seleccionada cada 1.5 s desde el bridge local; no transmite el contenido crudo
de artefactos. El costo se etiqueta como *ventana de misión / sesión*: es un delta del
tracker de sesión y puede incluir actividad concurrente de conversación.

Límites de seguridad de v0:

- Solo nivel L1 read-only y root del proyecto `JARVIS`.
- Solo `list_dir`, `read_file`, `search_text`, `git_status`, `git_diff_stat` y `git_log`.
- Sin PowerShell libre, escritura, tests, instalaciones, navegador ni otros proyectos.
- Una misión activa, máximo ocho pasos, deadline y outputs acotados.
- `completed` requiere postcondiciones, artefactos con hash e informe con referencias
  `[step:sN]`.

La configuración completa está en `.env.example`. Constitución, spec y plan:
`docs/agent_runtime/constitution.md`,
`docs/superpowers/specs/2026-07-11-agent-runtime-v0-design.md` y
`docs/superpowers/plans/2026-07-11-agent-runtime-v0.md`.

---

## Skills Runtime

Jarvis tiene skills runtime activables con la tool conversacional `jarvis_skill`.
Una skill es un perfil operativo con triggers, tools recomendadas e instrucciones
de comportamiento. No concede permisos por si misma: toda accion sensible sigue
pasando por las politicas Python y aprobacion HITL.

Skills incluidas:

- `desktop_operator`
- `study_capture`
- `obs_memory`
- `english_coach`
- `deep_reasoner`

Ademas, Jarvis importa automaticamente skills documentadas tipo Codex desde
`JARVIS_SKILL_IMPORT_DIRS` (por defecto `C:\Users\Isaac\.codex\skills`). Cada
carpeta con `SKILL.md` se convierte en una skill runtime disponible para
`jarvis_skill`. Esto incluye skills como `agentics-aws`, `faiss-rag`,
`n8n-specialist`, `spec-kit-flow`, `senior-data-analyst`,
`playwright-automation`, `isaac-memory`, `harness-router`, `pdf` y `doc` si
estan presentes en esa ruta.

Comandos naturales:

```text
que skills tienes
activa la skill desktop_operator
estado de skills
desactiva la skill actual
```

Para agregar una skill local sin tocar codigo, crea un JSON en `skills/local/`:

```json
{
  "name": "mi_skill",
  "title": "Mi Skill",
  "description": "Que hace esta skill.",
  "triggers": ["frase que la activa"],
  "tools": ["jarvis_recall"],
  "risk": "low",
  "instructions": "Instrucciones concretas que Jarvis debe seguir mientras esta activa."
}
```

Despues di: "Jarvis, recarga skills".

Para importar skills desde otra carpeta documentada, agrega esa ruta a
`JARVIS_SKILL_IMPORT_DIRS` separandola con `;`.

---

## Estructura

```
Jarvis/
├── jarvis.py                    # Entry point (Fase 1+)
├── overlay/                    # tkinter UI + web UI opcional
├── audio/                      # capture, playback, VAD
├── gemini/                     # GeminiLiveSession + tools + system prompt
├── grok/                       # ask_grok_deep wrapper (Grok 4.6)
├── claude/                     # alias de compatibilidad hacia grok/
├── actions/                    # Computer use read-only estructurado
├── agent_runtime/              # Misiones durables, policy, planner y verifier
├── vision/                     # screen capture
├── mcp_obsidian/                # MCP stdio server/client para Obsidian
├── memory/                     # SQLite + FAISS RAG
├── tests/                      # pytest
├── scripts/                    # Spikes de Fase 0
└── data/                       # SQLite, FAISS index, WAVs de debug
```
