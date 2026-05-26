"""
Create the Jarvis Project Docs tree in Obsidian.

Usage:
  & "H:\\Python311\\python.exe" scripts\\create_jarvis_obsidian_docs.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from mcp_obsidian import ops

load_dotenv(ROOT / ".env")

FOLDER = "Jarvis Project Docs"


def note_path(title: str) -> str:
    return f"{FOLDER}/{title}.md"


def link(title: str) -> str:
    return f"[[{title}]]"


NODE_TITLES = [
    "01 - Vision y Alcance",
    "02 - Setup Desde Cero",
    "03 - Arquitectura General",
    "04 - Orquestador jarvis.py",
    "05 - Gemini Live y Voz en Tiempo Real",
    "06 - Audio PTT VAD y Playback",
    "07 - Overlay Hotkeys y UX",
    "08 - Memoria Obsidian RAG",
    "09 - Tools y Function Calling",
    "10 - Claude Reasoner",
    "11 - Vision y Screen Capture",
    "12 - Obsidian MCP",
    "13 - Actions Executor Seguro",
    "14 - Telemetry Budgets y Costos",
    "15 - Modos de Trabajo",
    "16 - Testing y Verificacion",
    "17 - Operacion Diaria",
    "18 - Troubleshooting",
    "19 - Roadmap Evolutivo",
    "20 - Changelog de Evolucion",
]


def moc_body() -> str:
    links = "\n".join(f"{i + 1}. {link(title)}" for i, title in enumerate(NODE_TITLES))
    tree = "\n".join(
        [
            "00 - Jarvis MOC (Indice Principal)",
            "├── 01 - Vision y Alcance",
            "├── 02 - Setup Desde Cero",
            "├── 03 - Arquitectura General",
            "│   ├── 04 - Orquestador jarvis.py",
            "│   ├── 05 - Gemini Live y Voz en Tiempo Real",
            "│   ├── 06 - Audio PTT VAD y Playback",
            "│   └── 07 - Overlay Hotkeys y UX",
            "├── 08 - Memoria Obsidian RAG",
            "│   ├── 09 - Tools y Function Calling",
            "│   └── 12 - Obsidian MCP",
            "├── 10 - Claude Reasoner",
            "├── 11 - Vision y Screen Capture",
            "├── 13 - Actions Executor Seguro",
            "├── 14 - Telemetry Budgets y Costos",
            "├── 15 - Modos de Trabajo",
            "├── 16 - Testing y Verificacion",
            "├── 17 - Operacion Diaria",
            "├── 18 - Troubleshooting",
            "├── 19 - Roadmap Evolutivo",
            "└── 20 - Changelog de Evolucion",
        ]
    )
    return (
        "# 00 - Jarvis MOC (Indice Principal)\n\n"
        "Este es el mapa central del proyecto [[Jarvis]]. Funciona como nodo principal "
        "para navegar todo el sistema: vision, setup desde cero, arquitectura, voz, "
        "memoria, tools, Obsidian MCP, acciones seguras, telemetry, testing y roadmap.\n\n"
        "## Lectura recomendada\n\n"
        f"{links}\n\n"
        "## Arbol de nodos\n\n"
        "```text\n"
        f"{tree}\n"
        "```\n\n"
        "## Estado actual\n\n"
        "Jarvis ya es un copiloto local funcional:\n\n"
        "- Voz bidireccional en tiempo real con Gemini Live.\n"
        "- Overlay tkinter con estados, hotkeys y footer de costos.\n"
        "- Memoria Obsidian con FAISS, embeddings e indexacion incremental.\n"
        "- Tools autonomas para recordar, guardar, listar y linkear notas.\n"
        "- Claude como reasoner profundo via `ask_claude_deep`, con timeout.\n"
        "- Vision por screenshot con `Ctrl+Shift+S` y tool `screen_look`.\n"
        "- Executor seguro para abrir navegador y comandos read-only.\n"
        "- MCP local para operar Obsidian: crear notas, carpetas, mover, append y linkear.\n"
        "- Telemetry con budget session/daily/weekly via SQLite.\n"
        "- Tests de regresion para las piezas criticas.\n\n"
        "## Principio rector\n\n"
        "Jarvis debe sentirse rapida y presente. Toda tool pesada debe tener timeout, "
        "fallback y degradacion elegante. La voz en tiempo real tiene prioridad sobre "
        "cualquier automatizacion profunda.\n\n"
        f"Relacionado: {link('01 - Vision y Alcance')}, "
        f"{link('19 - Roadmap Evolutivo')}, {link('18 - Troubleshooting')}"
    )


NOTES = {
    "00 - Jarvis MOC (Indice Principal)": moc_body(),
    "01 - Vision y Alcance": dedent(
        """
        # 01 - Vision y Alcance

        Jarvis es el asistente personal local de Isaac: una interfaz de voz en tiempo real que combina conversacion natural, memoria persistente, vision de pantalla, razonamiento profundo y acciones seguras sobre Windows y Obsidian.

        ## Objetivo principal

        Construir un copiloto operativo que pueda conversar, recordar, ver pantalla, razonar, crear conocimiento en Obsidian y ejecutar acciones controladas sin romper el flujo de trabajo.

        ## Principios

        - Latencia primero: respuestas simples salen directo desde Gemini.
        - Profundidad bajo demanda: Claude se usa para tareas complejas.
        - Memoria verificable: lo durable se guarda en Obsidian.
        - Acciones seguras: dry-run, allowlists y kill-switch.
        - Interrupcion natural: Isaac puede cortar o redirigir.

        ## Capacidades actuales

        - [[05 - Gemini Live y Voz en Tiempo Real]]
        - [[08 - Memoria Obsidian RAG]]
        - [[10 - Claude Reasoner]]
        - [[11 - Vision y Screen Capture]]
        - [[12 - Obsidian MCP]]
        - [[13 - Actions Executor Seguro]]

        Relacionado: [[00 - Jarvis MOC (Indice Principal)]], [[19 - Roadmap Evolutivo]]
        """
    ).strip(),
    "02 - Setup Desde Cero": dedent(
        """
        # 02 - Setup Desde Cero

        Guia para reconstruir Jarvis desde cero en la maquina de Isaac.

        ## Requisitos

        - Windows 10/11.
        - Python global en `H:\\Python311`.
        - Proyecto en `C:\\Users\\Isaac\\Desktop\\PROYECTOS\\Jarvis`.
        - Obsidian vault en `H:\\Obsidian ClaudeCode\\Obsidian Claude Code`.
        - API key de Gemini.
        - API key de Anthropic si se quiere activar Claude.
        - Microfono y salida de audio funcionales.

        ## Instalar dependencias

        ```powershell
        cd C:\\Users\\Isaac\\Desktop\\PROYECTOS\\Jarvis
        & "H:\\Python311\\python.exe" -m pip install -r requirements.txt
        ```

        ## Configurar `.env`

        ```powershell
        Copy-Item .env.example .env
        ```

        Variables esenciales:

        ```env
        GEMINI_API_KEY=...
        ANTHROPIC_API_KEY=...
        JARVIS_MODE=dev
        GEMINI_VOICE=Aoede
        JARVIS_BUDGET_PERIOD=session
        JARVIS_OBSIDIAN_VAULT=H:\\Obsidian ClaudeCode\\Obsidian Claude Code
        JARVIS_OBSIDIAN_MEMORY_FOLDER=Jarvis Memory
        JARVIS_OBSIDIAN_READ_ALL=true
        JARVIS_OBSIDIAN_MCP_ALLOW_DELETE=false
        ```

        Guardar `.env` como UTF-8 sin BOM.

        ## Verificacion

        ```powershell
        & "H:\\Python311\\python.exe" -m compileall -q .
        & "H:\\Python311\\python.exe" -m pytest -q
        & "H:\\Python311\\python.exe" scripts\\spike_gemini_live.py
        ```

        ## Arranque

        ```powershell
        .\\jarvis_run.bat
        ```

        Ver logs:

        ```powershell
        Get-Content data\\jarvis.log -Wait -Tail 80
        ```

        Relacionado: [[17 - Operacion Diaria]], [[18 - Troubleshooting]]
        """
    ).strip(),
    "03 - Arquitectura General": dedent(
        """
        # 03 - Arquitectura General

        Jarvis es un sistema local modular. El orquestador conecta UI, audio, Gemini Live, memoria, tools, telemetry y MCP.

        ## Vista de alto nivel

        ```text
        Isaac
          │ voz / hotkeys / pantalla
          ▼
        Overlay + AudioCapture + HotkeyListener
          ▼
        Jarvis Orchestrator (jarvis.py)
          ├── Gemini Live Session
          ├── ToolDispatcher
          │   ├── Memoria RAG
          │   ├── Claude Reasoner
          │   ├── Screen Capture
          │   ├── Actions Executor
          │   └── Obsidian MCP
          ├── Telemetry + BudgetGate
          └── Obsidian Vault + FAISS
        ```

        ## Contextos de ejecucion

        - Main thread: tkinter.
        - Audio callback thread: sounddevice.
        - Audio worker: procesa chunks fuera del callback.
        - Gemini thread: asyncio loop.
        - Hotkey thread: keyboard.
        - Indexer thread: watchdog de Obsidian.
        - MCP subprocess: servidor stdio para Obsidian.

        ## Regla critica

        Nada pesado debe bloquear audio ni Gemini sin timeout.

        Relacionado: [[04 - Orquestador jarvis.py]], [[09 - Tools y Function Calling]], [[16 - Testing y Verificacion]]
        """
    ).strip(),
    "04 - Orquestador jarvis.py": dedent(
        """
        # 04 - Orquestador jarvis.py

        `jarvis.py` es el entrypoint y coordinador principal.

        ## Responsabilidades

        - Cargar `.env`.
        - Inicializar telemetry, budgets y persistencia.
        - Inicializar vault Obsidian, RAG e indexer.
        - Precalentar embeddings.
        - Construir `ToolContext` y `ToolDispatcher`.
        - Inicializar audio, overlay, hotkeys y Gemini.
        - Manejar lifecycle: `start`, `run`, `stop`.
        - Guardar memoria episodica al cerrar.

        ## Componentes

        `TokenTracker`, `UsagePersistence`, `BudgetGate`, `ModeManager`, `SafeActionExecutor`, `ScreenCapture`, `ClaudeReasoner`, `ObsidianMCPClient`, `ObsidianVault`, `VaultRAG`, `IncrementalIndexer`, `AudioPlayer`, `AudioCapture`, `JarvisOverlay`, `HotkeyListener`, `JarvisSession`.

        ## Memoria episodica

        Al cerrar, Jarvis crea una nota `Jarvis session YYYY-MM-DD HHMM <id>` en `Jarvis Memory`, guarda resumen/transcript y la indexa en RAG.

        ## Watchdog

        Si una tool deja el overlay en `thinking` mas de 15s, Jarvis restaura el estado y avisa.

        Relacionado: [[03 - Arquitectura General]], [[17 - Operacion Diaria]]
        """
    ).strip(),
    "05 - Gemini Live y Voz en Tiempo Real": dedent(
        """
        # 05 - Gemini Live y Voz en Tiempo Real

        Gemini Live es el motor conversacional principal de Jarvis.

        ## Archivo

        `gemini/session.py`

        ## Modelo

        `gemini-3.1-flash-live-preview`

        ## Responsabilidades

        - Conectar a Live API.
        - Enviar audio PCM 16kHz.
        - Recibir audio PCM 24kHz.
        - Procesar transcripts.
        - Manejar interrupciones.
        - Procesar function calling.
        - Reportar usage metadata.
        - Mantener session resumption.

        ## Flujo PTT

        ```text
        Ctrl press -> activity_start
        audio chunks -> send_realtime_input(audio)
        Ctrl release -> activity_end
        Gemini -> audio response
        turn_complete -> overlay idle/listening
        ```

        ## Fix importante

        `turn_complete` no debe tratarse como cierre de WebSocket. Jarvis sigue escuchando despues de cada turno.

        ## Tool timeout

        Cada tool tiene timeout para evitar congelamientos cuando Claude/MCP/API tardan.

        Relacionado: [[06 - Audio PTT VAD y Playback]], [[09 - Tools y Function Calling]]
        """
    ).strip(),
    "06 - Audio PTT VAD y Playback": dedent(
        """
        # 06 - Audio PTT VAD y Playback

        Jarvis usa audio local con `sounddevice` y VAD opcional con Silero.

        ## Archivos

        - `audio/capture.py`
        - `audio/playback.py`
        - `audio/vad.py`

        ## Entrada

        Gemini espera PCM 16kHz mono int16. `AudioCapture` usa blocksize 1600, alrededor de 100ms.

        ## Worker de captura

        El callback de audio solo mete bytes en una queue. Un worker procesa los chunks para no generar dropouts.

        ## Modo PTT

        Mantener `Ctrl` para hablar. Soltar `Ctrl` para cerrar turno.

        ## Modo libre

        `Ctrl+Shift+M` activa VAD. Solo se envia audio cuando hay voz.

        ## Playback

        Gemini devuelve PCM 24kHz mono int16. `AudioPlayer` usa `deque` con lock y permite interrupcion.

        Relacionado: [[05 - Gemini Live y Voz en Tiempo Real]], [[07 - Overlay Hotkeys y UX]]
        """
    ).strip(),
    "07 - Overlay Hotkeys y UX": dedent(
        """
        # 07 - Overlay Hotkeys y UX

        El overlay es la presencia visible de Jarvis.

        ## Archivos

        - `overlay/window.py`
        - `overlay/hotkeys.py`
        - `overlay/telemetry_footer.py`

        ## Estados

        - `idle`: esperando.
        - `listening`: escuchando.
        - `thinking`: procesando.
        - `speaking`: respondiendo.
        - `blocked`: budget o provider bloqueado.

        ## Hotkeys

        - `Ctrl`: push-to-talk.
        - `Ctrl+Shift+M`: modo libre.
        - `Ctrl+Shift+S`: capturar pantalla.
        - `Ctrl+Alt+Q`: kill switch.

        ## Footer

        Muestra tokens y costo por Gemini/Claude.

        ## Captura invisible

        `JARVIS_HIDE_FROM_CAPTURE=true` oculta overlay de capturas/OBS/Zoom/Teams.

        Relacionado: [[14 - Telemetry Budgets y Costos]], [[18 - Troubleshooting]]
        """
    ).strip(),
    "08 - Memoria Obsidian RAG": dedent(
        """
        # 08 - Memoria Obsidian RAG

        La memoria de Jarvis vive en Obsidian y se recupera con RAG local.

        ## Archivos

        - `memory/obsidian_vault.py`
        - `memory/notes.py`
        - `memory/rag.py`
        - `memory/indexer.py`
        - `memory/tools.py`

        ## Embeddings

        `sentence-transformers/all-MiniLM-L6-v2`

        ## Persistencia

        - `data/rag/vault.faiss`
        - `data/rag/manifest.json`
        - `data/rag/chunks.json`

        ## Indexacion incremental

        `watchdog` observa el vault y reindexa notas creadas, modificadas, movidas o borradas.

        ## Thread safety

        `VaultRAG` usa lock para proteger FAISS, chunks y manifest.

        ## RAG vs MCP

        RAG responde contexto. MCP actua sobre Obsidian.

        Relacionado: [[09 - Tools y Function Calling]], [[12 - Obsidian MCP]]
        """
    ).strip(),
    "09 - Tools y Function Calling": dedent(
        """
        # 09 - Tools y Function Calling

        Jarvis expone funciones a Gemini Live mediante function calling.

        ## Archivo

        `memory/tools.py`

        ## Tools

        - `jarvis_recall`
        - `jarvis_remember`
        - `jarvis_browse`
        - `jarvis_link`
        - `ask_claude_deep`
        - `screen_look`
        - `jarvis_run_safe_command`
        - `jarvis_open_url`
        - `jarvis_set_mode`
        - `jarvis_get_mode`
        - `obsidian_mcp`

        ## ToolContext

        Reune vault, RAG, reasoner, tracker, gate, screen capture, actions, modes y MCP client.

        ## Dispatcher

        `ToolDispatcher.call(name, args)` invoca la tool y captura errores.

        ## Regla de latencia

        No usar tools si Gemini puede responder directo. Toda tool pesada debe tener timeout.

        Relacionado: [[05 - Gemini Live y Voz en Tiempo Real]], [[12 - Obsidian MCP]]
        """
    ).strip(),
    "10 - Claude Reasoner": dedent(
        """
        # 10 - Claude Reasoner

        Claude funciona como reasoner profundo para tareas complejas.

        ## Archivo

        `claude/reasoner.py`

        ## Tool

        `ask_claude_deep(prompt, context_extra, max_tokens)`

        ## Usos correctos

        - Arquitectura.
        - Codigo largo.
        - Debugging complejo.
        - Planning multi-paso.
        - Documentacion larga.

        ## Guardrails

        - Budget gate antes de invocar.
        - Timeout por tool.
        - No llamar varias veces para la misma tarea.
        - Si Claude esta sin tokens, Jarvis debe degradar a modo rapido.

        Relacionado: [[14 - Telemetry Budgets y Costos]], [[18 - Troubleshooting]]
        """
    ).strip(),
    "11 - Vision y Screen Capture": dedent(
        """
        # 11 - Vision y Screen Capture

        Jarvis puede ver la pantalla cuando Isaac lo pide.

        ## Archivo

        `vision/screen.py`

        ## Hotkey

        `Ctrl+Shift+S`

        ## Tool

        `screen_look(reason)`

        ## Flujo

        1. Captura pantalla con `ImageGrab`.
        2. Reduce imagen a max side 1280.
        3. Guarda PNG en `data/screenshots`.
        4. Envia imagen + prompt a Gemini con `end_of_turn=True`.

        ## Usos

        - Leer errores.
        - Revisar UI.
        - Analizar codigo visible.
        - Ayudar a navegar herramientas.

        Relacionado: [[05 - Gemini Live y Voz en Tiempo Real]], [[13 - Actions Executor Seguro]]
        """
    ).strip(),
    "12 - Obsidian MCP": dedent(
        """
        # 12 - Obsidian MCP

        Jarvis tiene un puente MCP local para operar Obsidian.

        ## Archivos

        - `mcp_obsidian/server.py`
        - `mcp_obsidian/client.py`
        - `mcp_obsidian/ops.py`

        ## Tool

        `obsidian_mcp(operation, ...)`

        ## Operaciones

        - `list_folder`
        - `read_note`
        - `create_folder`
        - `create_note`
        - `update_note`
        - `append_note`
        - `move_path`
        - `link_notes`
        - `delete_path`

        ## Guardrails

        - Todo path debe quedar dentro del vault.
        - Bloquea `.obsidian`, `.trash`, `.git` y paths ocultos.
        - No sobrescribe salvo `overwrite=true`.
        - Delete desactivado por defecto.

        ## Ejemplos

        Crear carpeta:

        ```json
        {\"operation\":\"create_folder\",\"path\":\"Jarvis Project Docs\"}
        ```

        Crear nota:

        ```json
        {\"operation\":\"create_note\",\"path\":\"Jarvis Project Docs/Idea.md\",\"content\":\"# Idea\"}
        ```

        Relacionado: [[08 - Memoria Obsidian RAG]], [[09 - Tools y Function Calling]]
        """
    ).strip(),
    "13 - Actions Executor Seguro": dedent(
        """
        # 13 - Actions Executor Seguro

        Jarvis tiene acciones locales no destructivas.

        ## Archivo

        `actions/executor.py`

        ## Modo

        `JARVIS_MODE=dev` hace dry-run para comandos. Abrir URL esta permitido porque es reversible.

        ## Tools

        - `jarvis_open_url(url)`
        - `jarvis_run_safe_command(command, cwd)`

        ## Allowlist read-only

        - `pwd`
        - `Get-ChildItem`
        - `Get-Content`
        - `Select-String`
        - `rg`
        - `git status`
        - `git diff --stat`
        - `git log`

        ## Bloqueos

        Borrado, mover, copiar, redirecciones, pipes, separadores y comandos destructivos.

        Relacionado: [[11 - Vision y Screen Capture]], [[19 - Roadmap Evolutivo]]
        """
    ).strip(),
    "14 - Telemetry Budgets y Costos": dedent(
        """
        # 14 - Telemetry Budgets y Costos

        Jarvis mide tokens y costos para Gemini y Claude.

        ## Archivos

        - `telemetry/tracker.py`
        - `telemetry/costs.py`
        - `telemetry/budgets.py`
        - `telemetry/persistence.py`
        - `overlay/telemetry_footer.py`

        ## TokenTracker

        Acumula input, output, cache write/read, costo y eventos por modelo.

        ## BudgetGate

        Estados: OK, WARN, ALERT, BLOCKED.

        Variables:

        ```env
        JARVIS_BUDGET_GEMINI_USD=2.00
        JARVIS_BUDGET_CLAUDE_USD=1.00
        JARVIS_BUDGET_PERIOD=session
        JARVIS_BUDGET_HARD_STOP=true
        ```

        ## SQLite

        `data/usage.db` guarda deltas para budgets daily/weekly sin sobrecontar.

        Relacionado: [[07 - Overlay Hotkeys y UX]], [[10 - Claude Reasoner]]
        """
    ).strip(),
    "15 - Modos de Trabajo": dedent(
        """
        # 15 - Modos de Trabajo

        Jarvis adapta su comportamiento por contexto.

        ## Archivo

        `runtime_modes.py`

        ## Modos

        - `general`: conversacion normal.
        - `coding`: codigo, debugging y arquitectura.
        - `debugging`: logs, errores y reproduccion.
        - `meeting`: escucha, resumen y tareas.
        - `planning`: pasos, riesgos y dependencias.

        ## Tools

        - `jarvis_set_mode(mode)`
        - `jarvis_get_mode()`

        Relacionado: [[09 - Tools y Function Calling]], [[17 - Operacion Diaria]]
        """
    ).strip(),
    "16 - Testing y Verificacion": dedent(
        """
        # 16 - Testing y Verificacion

        Jarvis tiene tests de regresion para bugs reales.

        ## Comandos

        ```powershell
        & "H:\\Python311\\python.exe" -m compileall -q .
        & "H:\\Python311\\python.exe" -m pytest -q
        ```

        ## Areas cubiertas

        - Gemini session y `turn_complete`.
        - Timeout de tools.
        - Memoria notes/browse.
        - Tools extendidas.
        - MCP Obsidian.
        - Actions executor.
        - Runtime modes.
        - Telemetry persistence.
        - Costos.

        ## Filosofia

        Cada test protege una falla observada o una frontera peligrosa.

        Relacionado: [[03 - Arquitectura General]], [[18 - Troubleshooting]]
        """
    ).strip(),
    "17 - Operacion Diaria": dedent(
        """
        # 17 - Operacion Diaria

        Guia practica para usar Jarvis.

        ## Arrancar

        ```powershell
        cd C:\\Users\\Isaac\\Desktop\\PROYECTOS\\Jarvis
        .\\jarvis_run.bat
        ```

        ## Ver logs

        ```powershell
        Get-Content data\\jarvis.log -Wait -Tail 80
        ```

        ## Voz

        Mantener `Ctrl`, hablar, soltar `Ctrl`.

        ## Pantalla

        `Ctrl+Shift+S` o decir \"mira mi pantalla\".

        ## Browser

        Decir \"abre el navegador\" o \"abre google.com\".

        ## Obsidian

        Pedir crear carpetas, notas, mover nodos, agregar secciones o linkear notas.

        ## Cerrar

        `Ctrl+Alt+Q` o cerrar overlay. Jarvis guarda memoria episodica.

        Relacionado: [[02 - Setup Desde Cero]], [[18 - Troubleshooting]]
        """
    ).strip(),
    "18 - Troubleshooting": dedent(
        """
        # 18 - Troubleshooting

        ## Jarvis se queda pensando

        Causas:

        - Claude sin tokens o rate-limited.
        - Tool lenta.
        - MCP tardando.
        - Gemini esperando tool response.

        Revisar:

        ```powershell
        Get-Content data\\jarvis.log -Tail 120
        ```

        Buscar: `tool_call`, `tool_response`, `ERROR`, `WATCHDOG`.

        ## Claude sin tokens

        Jarvis debe degradar a modo rapido. Evitar tareas largas con Claude hasta recargar tokens.

        ## Gemini no responde

        Probar:

        ```powershell
        & "H:\\Python311\\python.exe" scripts\\spike_gemini_live.py
        ```

        ## MCP falla

        Probar:

        ```powershell
        & "H:\\Python311\\python.exe" -m mcp_obsidian.server
        ```

        ## Audio falla

        Probar `audio/capture.py` y `audio/playback.py`.

        Relacionado: [[16 - Testing y Verificacion]], [[14 - Telemetry Budgets y Costos]]
        """
    ).strip(),
    "19 - Roadmap Evolutivo": dedent(
        """
        # 19 - Roadmap Evolutivo

        Roadmap para hacer Jarvis mas poderosa sin perder velocidad.

        ## Fase A - Estabilidad

        - Provider unavailable para Claude.
        - Estado visual de Claude sin tokens.
        - Mejor feedback de tool timeout.

        ## Fase B - Obsidian avanzado

        - Plantillas de notas.
        - MOCs automaticos.
        - Deteccion de duplicados.
        - Mapas por proyecto.

        ## Fase C - Browser automation

        - Playwright controlado.
        - Captura + click con confirmacion.
        - Browser tasks con allowlist.

        ## Fase D - Computer use

        - Modo suggest.
        - Modo assist con confirmacion.
        - Autopilot por scope.

        ## Fase E - Dashboard

        - Visor de logs.
        - Estado Gemini/Claude/MCP.
        - Historial de tools.
        - Costos por sesion.

        Relacionado: [[01 - Vision y Alcance]], [[20 - Changelog de Evolucion]]
        """
    ).strip(),
    "20 - Changelog de Evolucion": dedent(
        """
        # 20 - Changelog de Evolucion

        ## Fase 0 - Spikes

        Gemini Live, Claude reasoner y comparativa de voces.

        ## Fase 1 - MVP voz

        Orquestador, overlay, audio, PTT y VAD.

        ## Fase 2 - Memoria

        Obsidian vault, notas, FAISS RAG, embeddings e indexer.

        ## Fase 3 - Telemetry

        Costos, budgets, footer y SQLite.

        ## Fase 4 - Estabilizacion

        Fix de `turn_complete`, session resumption, submit seguro, tool timeout y watchdog.

        ## Fase 5 - Potenciacion

        Claude tool, vision, actions executor, modos, memoria episodica y navegador.

        ## Fase 6 - Obsidian MCP

        Servidor MCP stdio, cliente MCP y operaciones seguras sobre el vault.

        ## Fase 7 - Documentacion estructurada

        Creacion de `Jarvis Project Docs`, MOC central y nodos numerados.

        Relacionado: [[00 - Jarvis MOC (Indice Principal)]], [[19 - Roadmap Evolutivo]]
        """
    ).strip(),
}


def main() -> int:
    print(ops.create_folder(FOLDER))
    for title, body in NOTES.items():
        result = ops.create_note(
            note_path(title),
            body,
            tags=["jarvis-docs", "moc" if title.startswith("00") else "jarvis-node"],
            overwrite=True,
        )
        print(result)

    for title in NODE_TITLES:
        result = ops.link_notes(note_path("00 - Jarvis MOC (Indice Principal)"), title)
        print(result)

    listed = ops.list_folder(FOLDER, limit=50)
    print(f"created_count={len(listed.get('items', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
