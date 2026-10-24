# 00 — Reconocimiento (Hermes, solo lectura)

Fecha de la pasada: 2026-10-23. Alcance: `C:\Users\Isaac\Desktop\PROYECTOS\JARVIS`.
Supuesto: la raíz es esa carpeta (única coincidencia bajo `PROYECTOS`). No se leyó `.env` ni `data/`.

## Repo

- Rama `main`, sigue a `origin/main`. Árbol limpio al empezar. No se tocó.
- Lenguaje principal: Python 3.11 (`requirements.txt`). UI de escritorio: Tauri + React (`desktop/src-tauri`, React 19). Sidecars nativos: C++ (`audio/native`, `vision/native`).
- Gestor: pip (`requirements.txt`) y Cargo para el shell. No se instaló nada.

## Puntos de entrada

- `jarvis.py` (legacy tkinter y modo web).
- `jarvis-desktop.exe` / `JARVIS.lnk`: spawnea `jarvis.py` con `JARVIS_UI=web` y `JARVIS_SUPERVISED=1`, espera el bridge en `127.0.0.1:8765`.
- `jarvis_run.bat`, `scripts/setup_venv.ps1`, spikes en `scripts/` y `grok/reasoner.py`.

## Superficie

- HTTP local: bridge en `127.0.0.1:8765` (SSE) para la ventana React. No se probó el puerto.
- Voz: Gemini Live (WebSocket), wake word, VAD, AEC.
- Visión: captura de pantalla y cámara (`vision/`, sidecar nativo). Hotkeys `Ctrl+Shift+S`, `Ctrl+Alt+S`.
- Computer use / acciones: `actions/`, `agent_runtime/tools.py`. README dice que Gemini no tiene shell libre; operaciones estructuradas (`list_dir`, `read_file`, `search_text`, `git_status`).
- Escrituras: nivel L2 en `MissionPolicy` (`WRITE_OPERATIONS`). L1 bloquea writes. File organizer y MCP Obsidian mueven/editan con HITL.
- MCP: `mcp_obsidian/` (crear, editar, mover, borrar si `JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=true`).
- Agentes/LLM: Gemini conversa; tools `ask_terra_code` (OpenAI), `ask_grok_deep` (api.x.ai), Claude como tool. Prompts en `vision/prompts.py` y el runtime.
- Datos: `data/` (SQLite de telemetría, logs, screenshots, `preferences.json`, posible `google_token.json`). Ignorado por git.
- Secretos esperados en `.env`: `GEMINI_API_KEY`, `XAI_API_KEY`, `OPENAI_API_KEY`. Plantilla: `.env.example` (sí va a git). El `.env` real está en `.gitignore` (`.env`, `.env*`, `*.env`). No se imprimió.

## Herramientas del agente (según código y README)

- Lectura: operaciones en `READ_ONLY_OPERATIONS`, rutas normalizadas con `resolve()` + `relative_to()`.
- Escritura L2: contenido hasta 20 000 caracteres, paths de bóveda relativos.
- Kill-switch: `Ctrl+Alt+Q` → `os._exit(130)`.
- Redacción: `security/secret_filter.py` (excepción explícita del ignore `*secret*`), RAG omite `.env`/pem/keys.

## Fuera de esta pasada

- No se ejecutó JARVIS ni se llamó a APIs.
- No se auditó el historial de git todavía (Fase 3).
- `grok --help` no se capturó: la sesión ya estaba en la TUI. Ruta de Fase 2: A (terminal que ya tenía `grok`).
