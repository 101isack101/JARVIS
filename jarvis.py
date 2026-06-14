"""
jarvis.py - Entry point orquestador de Jarvis.

Une todos los modulos:
  - Overlay UI (web premium by default, tkinter fallback)
  - HotkeyListener (Ctrl PTT + toggles, su propio thread)
  - JarvisSession (Gemini Live, su propio thread asyncio)
  - AudioCapture + AudioPlayer (sounddevice, callbacks de audio thread)
  - VAD (Silero, lazy en modo libre)
  - VaultRAG + IncrementalIndexer (Obsidian-backed, su propio thread)
  - TokenTracker + BudgetGate (compartidos, thread-safe)

Uso:
  & "H:\\Python311\\python.exe" jarvis.py
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

# Local imports
from actions.executor import SafeActionExecutor
from audio.capture import AudioCapture
from audio.playback import AudioPlayer
from audio.aec import AECStream, resample_24k_to_16k
from audio.vad import VADGate
from audio.wakeword import WakeWordGate
from claude.reasoner import ClaudeReasoner
from gemini.session import JarvisSession, SessionCallbacks, SessionConfig
from gemini.system_prompt import SYSTEM_PROMPT
from jarvis_version import JARVIS_VERSION_LABEL
from memory.indexer import IncrementalIndexer
from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault
from memory.rag import VaultRAG
from memory.semantic import SemanticMemoryIndex, SourceRegistry
from memory.session_journal import SessionJournal
from memory.session_summary import (
    synthesize_and_save,
    load_last_summary,
    load_recent_summaries,
    build_recall_block,
    build_recent_recall_block,
)
from memory.tools import ToolContext, ToolDispatcher, make_tool_object
from overlay.ui_thread import UiThread
from openai_code.reasoner import GPT55CodeReasoner
from proactivity.config import ProactivityConfig
from proactivity.engine import ProactivityEngine
from proactivity.morning_brief import (
    MorningBriefConfig,
    collect_morning_brief,
    render_brief_prompt,
)
from mcp_obsidian.client import ObsidianMCPClient
from obs_memory import OBSMemoryController
from overlay.factory import create_overlay
from overlay.hotkeys import HotkeyCallbacks, HotkeyListener
from runtime_modes import ModeManager
from runtime_preferences import ensure_runtime_preferences, preferences_prompt_block
from security.approvals import ApprovalBroker
from security.kill_switch import hard_exit
from skills.registry import active_skill_prompt_block
from telemetry.budgets import BudgetGate
from telemetry.error_journal import record_error
from telemetry.latency import LatencyTracker
from telemetry.logger import configure_logger, get_logger
from telemetry.persistence import UsagePersistence
from telemetry.tracker import TokenTracker
from vision.region_selector import RegionSelector
from vision.prompts import visual_capture_prompt
from vision.screen import ScreenCapture

# Configurar logger global lo antes posible, antes de que cualquier subsystema
# emita logs. configure_logger es idempotente.
configure_logger()
log = get_logger("jarvis")


def _install_crash_capture() -> None:
    """Red de captura de crashes duros que loguru NO ve.

    Tres mecanismos complementarios, todos vuelcan a data/jarvis_crash.log:

    - faulthandler: si el proceso recibe una senal fatal (segfault de una libreria
      nativa: PIL, sounddevice, torch, ffmpeg via subprocess no, pero si bindings C)
      vuelca el traceback Python de TODOS los threads antes de morir. Tambien
      permite disparar un dump manual si el proceso se cuelga.
    - sys.excepthook: excepciones no capturadas en el thread principal.
    - threading.excepthook: excepciones no capturadas en threads worker
      (audio, asyncio, RAG, to_thread). Sin esto, un worker que revienta puede
      tumbar el proceso sin dejar rastro en el log de loguru.

    Nota: un OOM-kill del SO (Windows mata el proceso por falta de RAM) NO deja
    traceback ni siquiera aqui — pero entonces la AUSENCIA de crash.log junto a un
    cierre subito ES la evidencia: descarta excepcion Python y apunta a memoria.
    """
    import faulthandler
    import threading
    import traceback as _tb

    crash_path = ROOT / "data" / "jarvis_crash.log"
    crash_path.parent.mkdir(parents=True, exist_ok=True)
    # Handle persistente: faulthandler exige que el archivo siga abierto.
    crash_fh = open(crash_path, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(file=crash_fh, all_threads=True)

    def _dump(header: str, exc_type, exc_value, exc_tb) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        crash_fh.write(f"\n{'=' * 70}\n{stamp} | {header}\n{'=' * 70}\n")
        _tb.print_exception(exc_type, exc_value, exc_tb, file=crash_fh)
        crash_fh.flush()
        try:
            record_error(
                "jarvis.crash_capture",
                exc=exc_value,
                severity="critical",
                context={"header": header},
            )
            log.error(f"[CRASH] {header}: {exc_type.__name__}: {exc_value}")
        except Exception:
            pass

    def _main_hook(exc_type, exc_value, exc_tb):
        _dump("Uncaught exception (main thread)", exc_type, exc_value, exc_tb)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    def _thread_hook(args):
        _dump(
            f"Uncaught exception (thread '{args.thread.name}')",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook

    # --- Watchdog de profundidad de stack del MAIN thread ---
    # Muestrea la pila del hilo principal desde un thread aparte. Si cruza un
    # umbral (recursion descontrolada en tkinter), vuelca el CICLO *antes* de que
    # el limite de recursion (1000) reviente el proceso. Lo hace desde una pila
    # limpia, evitando el problema de "no hay stack para diagnosticar". La funcion
    # que aparece cientos de veces en el volcado ES el disparador de la recursion.
    import time as _time

    main_ident = threading.main_thread().ident
    DEPTH_ALERT = 650

    def _stack_watchdog() -> None:
        import collections as _collections

        last_dump = 0.0
        while True:
            _time.sleep(0.3)
            try:
                frame = sys._current_frames().get(main_ident)
                if frame is None:
                    continue
                depth = 0
                f = frame
                while f is not None and depth <= DEPTH_ALERT + 4:
                    depth += 1
                    f = f.f_back
                if depth <= DEPTH_ALERT:
                    continue
                now = _time.time()
                if now - last_dump < 8.0:
                    continue
                last_dump = now
                counts: dict = _collections.Counter()
                top: list[str] = []
                f = frame
                d = 0
                while f is not None:
                    code = f.f_code
                    counts[(code.co_name, code.co_filename)] += 1
                    if d < 60:
                        top.append(f"  {code.co_name}  {code.co_filename}:{f.f_lineno}")
                    d += 1
                    f = f.f_back
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                crash_fh.write(
                    f"\n{'=' * 70}\n{stamp} | STACK WATCHDOG: main thread a {d} frames\n{'=' * 70}\n"
                )
                crash_fh.write("Funciones mas repetidas (el top es el CICLO de recursion):\n")
                for (name, filename), n in counts.most_common(10):
                    crash_fh.write(f"  {n:>5}x  {name}  ({filename})\n")
                crash_fh.write("\nPrimeros 60 frames desde el tope de la pila:\n")
                for line in top:
                    crash_fh.write(line + "\n")
                crash_fh.flush()
                try:
                    log.error(f"[STACK WATCHDOG] main thread a {d} frames; ciclo volcado a crash log")
                except Exception:
                    pass
            except Exception:
                pass

    threading.Thread(target=_stack_watchdog, name="JarvisStackWatchdog", daemon=True).start()

    log.info(f"Crash capture instalado -> {crash_path}")


USAGE_FLUSH_MS = 10_000
THINKING_WATCHDOG_MS = 15_000
# Cooldown despues de activity_end: ignora nuevos activity_start del VAD
# por este tiempo. Evita que un "uhm" inmediato dispare otra activity y
# auto-interrumpa la respuesta de Gemini antes de que pueda articularla.
LIBRE_ACTIVITY_COOLDOWN_MS = 600

# Barge-in en LIBRE: cortar a Jarvis hablandole encima, sin tocar el teclado.
# Se dispara con una WAKE-WORD ("Hey JARVIS") en vez de energia/VAD: el eco de
# la propia voz de Jarvis tiene la misma energia que tu voz (medido: picos ~0.08)
# y un umbral no los separa, pero el eco NUNCA pronuncia la frase clave. Asi el
# wake-word elimina los falsos positivos del eco y mantiene el manos-libres.
# Ver audio/wakeword.py. Dependencia opcional (openWakeWord); si falta, el
# barge-in se desactiva con gracia.
LIBRE_BARGE_IN_DEFAULT = True
WAKEWORD_MODEL_DEFAULT = "hey_jarvis"
WAKEWORD_THRESHOLD_DEFAULT = 0.5

# Hints humanos para mostrar en el overlay cuando arranca un tool.
# Solo se muestran tools "lentos" (>1s tipicos) — los rapidos no necesitan
# feedback porque Jarvis responde casi inmediatamente despues.
TOOL_HINTS: dict[str, str] = {
    "ask_claude_deep": "Consultando con Claude...",
    "screen_look": "Mirando tu pantalla...",
    "chrome_read_page": "Leyendo Chrome...",
    "study_mode": "Study Mode trabajando...",
    "obs_memory": "Procesando memoria OBS...",
    "jarvis_recall": "Buscando en tus notas...",
    "jarvis_session_recall": "Buscando en sesiones anteriores...",
    "jarvis_browse": "Listando notas...",
    "jarvis_remember": "Guardando en tu vault...",
    "obsidian_mcp": "Operando en Obsidian...",
    "jarvis_open_obsidian": "Abriendo Obsidian...",
    "ask_gpt55_code": "Consultando GPT 5.5...",
    "jarvis_run_safe_command": "Ejecutando comando...",
    "spotify_control": "Controlando Spotify...",
}

# Per-tool timeouts. ask_claude_deep tipicamente tarda 8-15s en prompts
# complejos; el default global (12s) caia justo en el limite. 30s da margen
# real, y como ahora cancela la HTTP de verdad (ask_async), no hay budget
# desperdiciado si en algun caso se pasa.
TOOL_TIMEOUTS_S: dict[str, float] = {
    "ask_gpt55_code": 60.0,
    "ask_claude_deep": 30.0,
    "study_mode": 60.0,
    "obs_memory": 900.0,
    "chrome_read_page": 20.0,
}


class Jarvis:
    """Orquestador. Lifecycle: build -> run -> stop."""

    def __init__(self) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self._stopping = False
        self._input_transcript: list[str] = []
        self._output_transcript: list[str] = []
        # Marshalling thread-safe hacia el thread del mainloop de tkinter.
        # Cualquier thread (asyncio, workers de to_thread durante aprobaciones)
        # encola en _ui_thread; un pump en el main thread lo drena. Evita que un
        # worker toque Tcl y aborte el proceso (Tcl_AsyncDelete wrong thread).
        self._ui_thread = UiThread()

        # Fase 1 — Continuidad entre sesiones.
        self.session_continuity_enabled = (
            os.environ.get("JARVIS_SESSION_JOURNAL_ENABLED", "true").lower() == "true"
        )
        self.session_min_turns = int(os.environ.get("JARVIS_SESSION_MIN_TURNS", "3"))
        self.session_recall_max_chars = int(
            os.environ.get("JARVIS_SESSION_RECALL_MAX_CHARS", "1000")
        )
        self.session_recent_limit = int(os.environ.get("JARVIS_SESSION_RECENT_LIMIT", "5"))
        self.session_journal = SessionJournal(ROOT / "data" / "session_journal.jsonl")
        self._session_saved = False  # guard idempotente para síntesis en stop()
        self._startup_notices: list[tuple[str, str]] = []
        # Índice del último volcado a journal por turno (delta, no acumulado).
        self._journal_input_idx = 0
        self._journal_output_idx = 0

        self._thinking_since_ms: int | None = None
        # Timestamp del ultimo activity_end disparado por VAD en LIBRE.
        # Usado para suprimir activity_start inmediato (anti-rebote de "uhm").
        self._last_activity_end_ms: int = 0

        # --- Barge-in en LIBRE por wake-word ("Hey JARVIS") ---
        self._barge_in_enabled = (
            os.environ.get("JARVIS_LIBRE_BARGE_IN", str(LIBRE_BARGE_IN_DEFAULT)).lower()
            in ("true", "1", "yes")
        )
        self._wakeword_model = os.environ.get(
            "JARVIS_WAKEWORD_MODEL", WAKEWORD_MODEL_DEFAULT
        )
        self._wakeword_threshold = float(
            os.environ.get("JARVIS_WAKEWORD_THRESHOLD", str(WAKEWORD_THRESHOLD_DEFAULT))
        )
        # Detector wake-word (lazy: se carga al entrar en LIBRE). None = aun no
        # cargado o dependencia ausente (barge-in degradado).
        self._wakeword: WakeWordGate | None = None
        # Serializa la carga de modelos de LIBRE (Silero VAD + wakeword) para que
        # el pre-warm de arranque y un toggle a LIBRE no la hagan dos veces a la vez.
        self._libre_models_lock = threading.Lock()
        # True mientras Jarvis reproduce su voz en LIBRE (ventana de barge-in).
        self._libre_speaking = False
        # Pico de score wake-word del turno hablado actual (diagnostico): nos
        # dice si tu "Hey JARVIS" se acerco al umbral o si el eco lo enmascara.
        self._wakeword_peak = 0.0

        # --- AEC: cancelacion de eco para que el wake-word funcione en parlantes ---
        # Limpia la voz de Jarvis del mic ANTES de detectar el wake-word. El
        # player empuja su salida como referencia (far-end); el mic se procesa
        # contra ella. Sin AEC el eco enmascara/iguala tu voz (probado 3 veces).
        self._aec_enabled = (
            self._barge_in_enabled
            and os.environ.get("JARVIS_AEC", "true").lower() in ("true", "1", "yes")
        )
        self._aec = (
            AECStream(
                block=512,
                partitions=int(os.environ.get("JARVIS_AEC_PARTITIONS", "8")),
                mu=float(os.environ.get("JARVIS_AEC_MU", "0.3")),
            )
            if self._aec_enabled
            else None
        )
        # Pico de ERLE (dB de eco cancelado) del turno hablado, para diagnostico.
        self._aec_erle_peak = 0.0

        # Telemetria (compartida)
        self.tracker = TokenTracker()
        self.latency = LatencyTracker(window=50)
        self.persistence = UsagePersistence(ROOT / "data" / "usage.db", self.session_id)
        self.gate = BudgetGate(history_provider_costs=self._historical_budget_costs)
        self.modes = ModeManager()
        self.approvals = ApprovalBroker(timeout_s=30.0)
        self.actions = SafeActionExecutor(root=ROOT, approval_broker=self.approvals)
        self.screen = ScreenCapture(ROOT / "data" / "screenshots")
        from vision.camera import CameraCapture
        self.camera = CameraCapture(ROOT / "data" / "camera")
        self.preferences = ensure_runtime_preferences(ROOT / "data" / "preferences.json")
        self.reasoner = self._build_reasoner()
        self.code_reasoner = self._build_code_reasoner()
        self.obsidian_mcp = ObsidianMCPClient(python_exe=sys.executable, cwd=ROOT)

        # Memoria Obsidian
        log.info("Iniciando memoria Obsidian...")
        self.vault = ObsidianVault()
        self.rag = VaultRAG(vault=self.vault, index_dir=ROOT / "data" / "rag")
        if not self.rag.load():
            log.info("Index no encontrado. Indexando vault completo (1 vez, ~30s)...")
            stats = self.rag.reindex_all()
            log.info(f"Indexado: {stats}")
            self.rag.save()
        else:
            log.info(
                f"Index cargado: {len(self.rag.chunks)} chunks de "
                f"{len(self.rag.manifest)} archivos"
            )

        # Pre-warm del modelo de embeddings: evita que el primer jarvis_recall
        # bloquee el asyncio loop por 1-3s mientras se carga el modelo.
        log.info("Pre-warm sentence-transformers (evita bloqueo en primer recall)...")
        self.rag._embed(["pre-warm"])
        log.info("Modelo pre-cargado.")

        # Multi-source semantic memory. Fail-safe: Jarvis keeps using the legacy
        # Obsidian RAG if this index cannot be loaded or built.
        self.semantic_memory = None
        if SemanticMemoryIndex.enabled():
            try:
                self.semantic_memory = SemanticMemoryIndex.from_env(ROOT)
                self.semantic_memory.model = self.rag.model
                if not self.semantic_memory.load():
                    log.info("Semantic memory no encontrada. Indexando fuentes locales seguras...")
                    registry = SourceRegistry(vault=self.vault, workspace_root=ROOT.parent)
                    stats = self.semantic_memory.rebuild(registry)
                    log.info(f"Semantic memory indexada: {stats}")
                else:
                    log.info(
                        f"Semantic memory cargada: {len(self.semantic_memory.chunks)} chunks de "
                        f"{len(self.semantic_memory.manifest)} documentos"
                    )
            except Exception as exc:
                log.warning(f"[WARN] semantic memory deshabilitada por fallo: {exc}")
                self.semantic_memory = None

        # Fase 1 — Continuidad: reconciliar journal huérfano (síntesis diferida)
        # y recuperar el resumen de la última sesión para inyectarlo al prompt.
        recall_block = ""
        if self.session_continuity_enabled:
            try:
                if self.session_journal.has_pending():
                    log.info("Journal huérfano detectado; sintetizando sesión previa...")
                    p = synthesize_and_save(
                        self.session_journal,
                        self.reasoner,
                        self.vault,
                        min_turns=self.session_min_turns,
                        session_id=self.session_id,
                    )
                    if p is not None:
                        self._startup_notices.append(("Memoria pendiente consolidada", "ok"))
                        try:
                            self.rag.index_file(p)
                            self.rag.save()
                            if self.semantic_memory is not None:
                                registry = SourceRegistry(
                                    vault=self.vault,
                                    workspace_root=ROOT.parent,
                                    sources=("obsidian",),
                                )
                                self.semantic_memory.index_documents(registry.iter_documents())
                        except Exception as exc:
                            log.warning(f"[WARN] no se indexó nota huérfana: {exc}")
                    else:
                        self._startup_notices.append(("Memoria pendiente sin consolidar", "warn"))
            except Exception as exc:
                log.warning(f"[WARN] síntesis diferida falló: {exc}")
            try:
                recent = load_recent_summaries(
                    self.vault,
                    limit=self.session_recent_limit,
                    max_chars_each=self.session_recall_max_chars,
                )
                recall_block = build_recent_recall_block(recent)
                if not recall_block:
                    prev = load_last_summary(self.vault, self.session_recall_max_chars)
                    recall_block = build_recall_block(prev)
                if recall_block:
                    log.info("Contexto de sesión anterior inyectado al system_prompt.")
            except Exception as exc:
                log.warning(f"[WARN] recall de sesión previa falló: {exc}")

        # Fase 3 — Proactividad: motor + briefing de arranque.
        self.proactivity = None
        briefing_block = ""
        try:
            pcfg = ProactivityConfig.from_env()
            if pcfg.enabled:
                self.proactivity = ProactivityEngine(
                    config=pcfg,
                    state_path=Path("data") / "proactivity_state.json",
                )
                briefing_block = self.proactivity.build_briefing(self.vault)
                if briefing_block:
                    log.info("Briefing proactivo inyectado al system_prompt.")
        except Exception as exc:
            log.warning(f"[WARN] proactividad (arranque) falló: {exc}")

        # Briefing matutino hablado: se dispara una vez por proceso en el primer
        # connect (no en reconexiones). Flag de instancia = idempotencia.
        self._briefing_sent = False
        self._morning_cfg = MorningBriefConfig.from_env()
        self._briefing_block_cache = briefing_block  # vault block ya calculado

        skill_block = ""
        try:
            skill_block = active_skill_prompt_block(
                skill_dir=ROOT / "skills" / "local",
                state_path=ROOT / "data" / "skills" / "state.json",
            )
            if skill_block:
                log.info("Skill activa inyectada al system_prompt.")
        except Exception as exc:
            log.warning(f"[WARN] skill activa no pudo inyectarse: {exc}")

        self.obs_memory = OBSMemoryController(
            vault=self.vault,
            reasoner=self.reasoner,
            on_job_done=self._on_obs_memory_job_done,
        )
        self.tool_ctx = ToolContext(
            vault=self.vault,
            rag=self.rag,
            semantic_memory=self.semantic_memory,
            reasoner=self.reasoner,
            code_reasoner=self.code_reasoner,
            tracker=self.tracker,
            gate=self.gate,
            screen=self.screen,
            camera=self.camera,
            actions=self.actions,
            modes=self.modes,
            obsidian_mcp=self.obsidian_mcp,
            obs_memory=self.obs_memory,
            approvals=self.approvals,
            set_listen_mode=self._apply_listen_mode,
            proactivity=self.proactivity,
        )
        self.dispatcher = ToolDispatcher(self.tool_ctx)
        self.indexer = IncrementalIndexer(
            self.rag,
            on_change=self._on_vault_change,
        )

        # Audio
        self.player = AudioPlayer(
            on_underflow=self._on_playback_complete,
            on_playback=self._on_player_output if self._barge_in_enabled else None,
        )
        self.capture = AudioCapture(on_chunk=self._on_audio_chunk)
        self.vad: VADGate | None = None  # lazy load en modo libre

        # Overlay
        self.overlay = create_overlay(
            self.tracker,
            self.gate,
            on_close=lambda: self.stop(close_overlay=False),
            on_command=self._on_ui_command,
        )
        # Arranca el pump de UI en el main thread: drena _ui_thread y se re-arma.
        # El primer after() se registra aquí (build corre en el main thread), así
        # que toda llamada Tcl posterior queda en el thread del mainloop.
        self._install_ui_pump()
        for message, level in self._startup_notices:
            self.overlay.log_event(message, level)
        self.approvals.set_handler(
            lambda action: self._tk(
                lambda: self.overlay.show_approval(action, self.approvals.resolve)
            )
        )

        # Modo: 'PTT' (default) o 'LIBRE'
        self.mode = "PTT"

        # Hotkeys
        self.hotkey_cb = HotkeyCallbacks(
            on_ptt_press=self._on_ptt_press,
            on_ptt_release=self._on_ptt_release,
            on_toggle_listen_mode=self._on_toggle_mode,
            on_capture_screen=self._on_capture_screen,
            on_capture_region=self._on_capture_region,
            on_capture_camera=self._on_capture_camera,
            on_pause=lambda: self._log("pause pendiente Fase 4"),
            on_kill=self._on_kill,
        )
        self.hotkey_listener = HotkeyListener(self.hotkey_cb)

        # Sesion Gemini Live
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY no configurado en .env")

        session_callbacks = SessionCallbacks(
            on_audio=self._on_gemini_audio,
            on_text=self._on_gemini_text,
            on_input_transcript=self._on_input_transcript,
            on_interrupted=self._on_interrupted,
            on_turn_complete=self._on_turn_complete,
            on_connected=self._on_connected,
            on_error=self._on_error,
            on_log=self._log,
            on_connection_status=self._on_connection_status,
            on_tool_start=self._on_tool_start,
            on_tool_end=self._on_tool_end,
        )
        self.session = JarvisSession(
            config=SessionConfig(
                api_key=api_key,
                voice=os.environ.get("GEMINI_VOICE", "Aoede"),
                system_prompt=(
                    SYSTEM_PROMPT
                    + "\n\n"
                    + preferences_prompt_block(self.preferences)
                    + (("\n\n" + recall_block) if recall_block else "")
                    + (("\n\n" + briefing_block) if briefing_block else "")
                    + (("\n\n" + skill_block) if skill_block else "")
                ),
                manual_activity_mode=True,
                enable_input_transcription=True,
                tools=[make_tool_object()],
                tracker=self.tracker,
                tool_dispatcher=self.dispatcher,
                tool_timeouts_s=TOOL_TIMEOUTS_S,
                context_compression=(
                    os.environ.get("JARVIS_CONTEXT_COMPRESSION", "true").lower()
                    in ("true", "1", "yes")
                ),
                context_trigger_tokens=int(
                    os.environ.get("JARVIS_CONTEXT_TRIGGER_TOKENS", "25600")
                ),
                context_target_tokens=int(
                    os.environ.get("JARVIS_CONTEXT_TARGET_TOKENS", "12800")
                ),
            ),
            callbacks=session_callbacks,
        )

        from vision.camera import CameraWatchController
        self.camera_watch = CameraWatchController(
            camera=self.camera,
            session=self.session,
            on_state=lambda active: self._tk(
                lambda: self.overlay.set_camera_active(active)
            ),
            on_frame=lambda frame: self._tk(
                lambda: self.overlay.update_camera_preview(frame)
            ),
            gate_check=lambda: self.gate.can_invoke(self.tracker, "gemini"),
            on_log=self._log,
        )
        self.tool_ctx.camera_watch = self.camera_watch

        from google import genai
        self.tool_ctx.genai_client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options={"api_version": "v1beta"},
        )
        self.tool_ctx.on_focus_box = lambda box_2d, lbl: self._tk(
            lambda: self._apply_focus_box(box_2d, lbl)
        )

    def _apply_focus_box(self, box_2d, label: str) -> None:
        # box_2d viene normalizado 0..1000; el preview lo convierte a px usando la
        # geometria real de la imagen (letterbox), asi queda alineado en cualquier
        # aspect ratio. No convertir aqui.
        self.overlay.set_camera_focus(box_2d, label)

    def _on_obs_memory_job_done(self, job: dict) -> None:
        title = job.get("title") or "OBS"
        note_path = job.get("note_path") or ""
        status = job.get("status") or ""
        if status == "done":
            suffix = f" -> {note_path}" if note_path else ""
            self._log(f"[OBS] analisis listo: {title}{suffix}")
            self._tk(lambda: self.overlay.log_event(f"OBS listo: {title}", "ok"))
            self._tk(lambda: self.overlay.append_output(f"\n[OBS Memory listo: {title}{suffix}]\n"))
            return

        error = job.get("error") or "sin detalle"
        self._log(f"[WARN] OBS Memory fallo: {title}: {error}")
        self._tk(lambda: self.overlay.log_event(f"OBS fallo: {title}", "error"))
        self._tk(lambda: self.overlay.append_output(f"\n[OBS Memory fallo: {title} - {error}]\n"))

    # ---- Lifecycle ----

    def start(self) -> None:
        self._log(f"Sesion {self.session_id} iniciando")
        self.player.start()
        self.capture.start()
        self.indexer.start()
        self.session.start()
        self.hotkey_listener.start()
        self.overlay.set_state("idle")
        self.overlay.set_mode("PTT")
        self._schedule_usage_flush()
        self._schedule_thinking_watchdog()
        # Pre-warm de los modelos de LIBRE (Silero VAD + wakeword) en background,
        # igual que el pre-warm de embeddings. Asi el primer toggle a modo libre es
        # instantaneo en vez de congelar el thread del hotkey 1-3s mientras cargan.
        threading.Thread(
            target=self._ensure_libre_models, name="JarvisLibrePrewarm", daemon=True
        ).start()

    def stop(self, *, close_overlay: bool = True) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._log("Cerrando Jarvis...")
        if close_overlay:
            self._tk(lambda: self.overlay.close(), force=True)
        try:
            if getattr(self, "camera_watch", None):
                self.camera_watch.stop()
        except Exception:
            pass
        try: self.hotkey_listener.stop()
        except Exception: pass
        try: self.session.stop()
        except Exception: pass
        self._tk(lambda: self.overlay.set_connection_status("stopped", ""), force=True)
        try: self.indexer.stop()
        except Exception: pass
        try: self.capture.stop()
        except Exception: pass
        try: self.player.stop()
        except Exception: pass
        try: self._save_session_memory()
        except Exception as exc: self._log(f"[WARN] memoria episodica fallo: {exc}")
        try: self.persistence.flush_snapshot(self.tracker)
        except Exception: pass
        try: self._log(self.latency.summary_line())
        except Exception as exc: self._log(f"[WARN] latency summary fallo: {exc}")

    def run(self) -> None:
        self.start()
        try:
            self.overlay.run()
        finally:
            self.stop(close_overlay=False)

    # ---- Hotkey handlers (llamados desde thread de keyboard) ----

    def _on_ptt_press(self) -> None:
        if not self._gemini_budget_available("[PTT] press"):
            return
        if self.mode != "PTT":
            self._log("[PTT] press IGNORADO: estamos en modo LIBRE")
            return
        self._audio_chunks_sent = 0
        self._log("[PTT] press -> activity_start + start_recording")
        self._set_overlay_state("listening")
        self.session.start_user_activity()
        self.capture.start_recording()

    def _on_ptt_release(self) -> None:
        if self.mode != "PTT":
            return
        self.capture.stop_recording()
        chunks = getattr(self, "_audio_chunks_sent", 0)
        self._log(f"[PTT] release -> {chunks} chunks enviados, activity_end")
        self.session.end_user_activity()
        self.latency.mark_user_end()
        self._set_overlay_state("thinking")

    def _on_toggle_mode(self) -> None:
        """Hotkey Ctrl+Shift+M: alterna entre el modo actual y el contrario."""
        self._apply_listen_mode("PTT" if self.mode == "LIBRE" else "LIBRE")

    def _apply_listen_mode(self, target: str) -> dict:
        """Aplica un modo de escucha concreto (PTT/LIBRE) de forma idempotente.

        Punto unico de verdad: lo usan la hotkey (_on_toggle_mode) y la tool de
        voz `jarvis_listen_mode`. Devuelve un dict serializable para que la tool
        pueda informar el resultado a Gemini.
        """
        target = (target or "").strip().upper()
        if target not in ("PTT", "LIBRE"):
            return {"ok": False, "error": f"modo de escucha invalido: {target}"}
        if target == self.mode:
            return {"ok": True, "mode": self.mode, "changed": False}

        if target == "LIBRE":
            self.mode = "LIBRE"
            # Modelos ya pre-cargados en background al arranque -> normalmente
            # instantaneo. Si el usuario fue mas rapido que el pre-warm, esto los
            # carga ahora (el lock evita doble carga).
            self._ensure_libre_models()
            if self.vad is not None:
                self.vad.reset()
            if self._aec is not None:
                self._aec.reset()
            self._libre_in_activity = False
            self._libre_speaking = False
            self.capture.start_recording()
            self._log("[MODE] LIBRE activado. VAD controla activity_start/end.")
            self._tk(lambda: self.overlay.set_mode("LIBRE"))
            self._set_overlay_state("listening")
        else:
            self.mode = "PTT"
            self.capture.stop_recording()
            self._libre_speaking = False
            if self._aec is not None:
                self._aec.reset()
            # Si VAD dejo activity abierta, cerrarla
            if getattr(self, "_libre_in_activity", False):
                self.session.end_user_activity()
                self._libre_in_activity = False
            self._log("[MODE] PTT restaurado.")
            self._tk(lambda: self.overlay.set_mode("PTT"))
            self._set_overlay_state("idle")
        return {"ok": True, "mode": self.mode, "changed": True}

    def _on_kill(self) -> None:
        self._log("KILL recibido -> salida dura")
        hard_exit(130)

    def _on_capture_screen(self) -> None:
        if not self.gate.can_invoke(self.tracker, "gemini"):
            self._log("[SCREEN] captura ignorada: gemini bloqueado por budget")
            self._set_overlay_state("blocked")
            return
        try:
            shot = self.screen.capture()
            self._log(f"[SCREEN] capturada {shot.width}x{shot.height}: {shot.path.name}")
            self._tk(lambda: self.overlay.log_event("Pantalla capturada", "ok"))
            self._set_overlay_state("thinking")
            self.session.send_image(
                shot.png_bytes,
                mime_type=shot.mime_type,
                prompt=visual_capture_prompt("screen"),
            )
        except Exception as exc:
            self._log(f"[SCREEN] error: {type(exc).__name__}: {exc}")

    def _on_capture_camera(self) -> None:
        if not self.gate.can_invoke(self.tracker, "gemini"):
            self._log("[CAMERA] captura ignorada: gemini bloqueado por budget")
            self._set_overlay_state("blocked")
            return
        try:
            frame = self.camera.capture()
            self._log(f"[CAMERA] capturada {frame.width}x{frame.height}: {frame.path.name}")
            self._tk(lambda: self.overlay.log_event("Camara capturada", "ok"))
            self._tk(lambda data=frame.jpeg_bytes: self._show_camera_frame(data))
            self._set_overlay_state("thinking")
            self.session.send_image(
                frame.jpeg_bytes,
                mime_type=frame.mime_type,
                prompt=visual_capture_prompt("camera"),
            )
        except Exception as exc:
            self._log(f"[CAMERA] error: {type(exc).__name__}: {exc}")
            self._set_overlay_state("idle" if self.mode == "PTT" else "listening")

    def _show_camera_frame(self, jpeg_bytes: bytes) -> None:
        frame = SimpleNamespace(jpeg_bytes=jpeg_bytes)
        self.overlay.set_camera_active(True)
        self.overlay.update_camera_preview(frame)

    # ---- Comandos de la UI web (botones del overlay premium) ----

    def _on_ui_command(self, command: str, args: dict) -> bool:
        """Enruta comandos de los botones de la UI web a la logica de Jarvis.

        Lo invoca WebJarvisOverlay desde el thread del servidor HTTP. Los metodos
        destino ya marshallan a la sesion/UI (igual que las hotkeys, que corren
        en el thread del teclado), asi que es seguro llamarlos directo. Devuelve
        True si el comando se reconoce.
        """
        if command == "toggleMode":
            self._on_toggle_mode()
            return True
        if command == "setMode":
            self._apply_listen_mode(str(args.get("mode", "")))
            return True
        if command == "toggleCamera":
            self._on_capture_camera()
            return True
        if command == "sendText":
            text = str(args.get("text", "")).strip()
            if text:
                self._send_user_text(text)
            return True
        return False

    def _send_user_text(self, text: str) -> None:
        """Inyecta un turno de usuario escrito en la sesion Gemini (chat box)."""
        if not self._gemini_budget_available("[TEXT] send"):
            return
        self._log(f"[TEXT] usuario -> {text[:80]}")
        self._tk(lambda: self.overlay.append_input(text + "\n"))
        self._set_overlay_state("thinking")
        self.latency.mark_user_end()
        self.session.send_text(text)

    def _on_capture_region(self) -> None:
        """Hotkey Ctrl+Alt+S. Llamado desde keyboard thread.

        Muestra overlay de snipping para que Isaac arrastre un rectangulo;
        solo esa region se envia a Gemini (mucho mas barato en tokens).
        """
        if not self.gate.can_invoke(self.tracker, "gemini"):
            self._log("[REGION] ignorado: gemini bloqueado por budget")
            self._set_overlay_state("blocked")
            return
        # Mostrar selector desde main thread tkinter (Toplevel + grab_set)
        self._tk(self._show_region_selector)

    def _show_region_selector(self) -> None:
        """Crea y muestra el RegionSelector. DEBE correr en main thread.

        En modo web (overlay sin root tk), degrada a captura completa.
        """
        root = getattr(self.overlay, "root", None)
        if root is None:
            self._log("[REGION] modo web: RegionSelector no disponible, usando captura completa")
            self.overlay.log_event("Ctrl+Alt+S: seleccion de region no disponible en modo web", "warn")
            self._on_capture_screen()
            return
        try:
            RegionSelector(
                root,
                on_select=self._on_region_selected,
            ).show()
        except Exception as exc:
            self._log(f"[REGION] no pude mostrar selector: {type(exc).__name__}: {exc}")

    def _on_region_selected(self, bbox: tuple[int, int, int, int] | None) -> None:
        """Callback del selector. bbox=None si Isaac canceló o rect muy pequeño."""
        if bbox is None:
            self._log("[REGION] cancelado por Isaac")
            return
        try:
            shot = self.screen.capture_region(bbox)
            self._log(f"[REGION] {shot.width}x{shot.height}: {shot.path.name}")
            self._tk(lambda: self.overlay.log_event("Region capturada", "ok"))
            self._set_overlay_state("thinking")
            self.session.send_image(
                shot.png_bytes,
                mime_type=shot.mime_type,
                prompt=visual_capture_prompt("region"),
            )
        except Exception as exc:
            self._log(f"[REGION] error: {type(exc).__name__}: {exc}")
            self._set_overlay_state("idle" if self.mode == "PTT" else "listening")

    # ---- Audio chunk -> Gemini (thread de sounddevice) ----

    def _on_audio_chunk(self, pcm_bytes: bytes) -> None:
        if not self._gemini_budget_available("[AUDIO] chunk"):
            if getattr(self, "_libre_in_activity", False):
                self.session.end_user_activity()
                self._libre_in_activity = False
            return
        # En modo LIBRE, VAD local controla activity boundaries.
        # En modo PTT, los maneja la hotkey (press = start, release = end).
        if self.mode == "LIBRE":
            if self.vad is None:
                # VAD aun cargando (pre-warm en background) o no disponible: en
                # LIBRE NO streameamos el mic sin gating de VAD — eso dispararia
                # coste y, en modo manual, audio sin activity boundaries. Mejor
                # descartar el chunk hasta que el VAD este listo.
                return
            # Mientras Jarvis habla:
            #  - barge-in ON: el mic sigue vivo; buscamos voz tuya sostenida y
            #    de alta confianza para cortarlo. Nada de esto se manda a Gemini
            #    hasta que el barge-in se confirma (ver _trigger_barge_in).
            #  - barge-in OFF: echo guard clasico, ignoramos el mic.
            if self._libre_speaking:
                self._detect_barge_in(pcm_bytes)
                return
            if self.player.is_playing():
                return
            import time as _time
            now_ms = int(_time.time() * 1000)
            events = self.vad.feed(pcm_bytes)
            for ev in events:
                if ev.kind == "start":
                    if not self._gemini_budget_available("[VAD] activity_start"):
                        return
                    cooldown_remaining = LIBRE_ACTIVITY_COOLDOWN_MS - (now_ms - self._last_activity_end_ms)
                    if cooldown_remaining > 0:
                        self._log(f"[VAD] voz detectada IGNORADA: cooldown post-activity_end ({cooldown_remaining}ms restantes)")
                        continue
                    if not getattr(self, "_libre_in_activity", False):
                        self._libre_in_activity = True
                        self._log("[VAD] voz detectada -> activity_start")
                        self.session.start_user_activity()
                    self._set_overlay_state("listening")
                elif ev.kind == "end":
                    if getattr(self, "_libre_in_activity", False):
                        self._libre_in_activity = False
                        self._last_activity_end_ms = now_ms
                        self._log("[VAD] silencio -> activity_end")
                        self.session.end_user_activity()
                        self.latency.mark_user_end()
                    self._set_overlay_state("thinking")
            # En LIBRE solo enviamos audio cuando VAD esta en activity (ahorra API)
            if not getattr(self, "_libre_in_activity", False):
                return
        self.session.send_audio_chunk(pcm_bytes)
        self._audio_chunks_sent = getattr(self, "_audio_chunks_sent", 0) + 1

    def _gemini_budget_available(self, source: str) -> bool:
        if self.gate.can_invoke(self.tracker, "gemini"):
            return True
        self._log(f"{source} IGNORADO: gemini bloqueado por budget")
        self._set_overlay_state("blocked")
        return False

    def _on_player_output(self, pcm24k_bytes: bytes) -> None:
        """Referencia (far-end) para el AEC: lo que sale al parlante.

        Llamado desde el thread de salida de sounddevice. Resamplea 24k->16k y
        lo encola en el AEC. Debe ser rapido (resampleo + append con lock).
        """
        if self._aec is None or not pcm24k_bytes:
            return
        try:
            far24 = np.frombuffer(pcm24k_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            self._aec.push_far(resample_24k_to_16k(far24))
        except Exception as exc:
            # Corre en el thread de salida de sounddevice (~cada 42ms). Tragar el
            # fallo en silencio es lo que ocultó el NameError de numpy durante toda
            # la vida del feature: el AEC quedaba sin far-end y el barge-in degradado.
            # Avisamos UNA vez (sin spamear el hot path) para que nunca vuelva a pasar.
            if not getattr(self, "_aec_push_warned", False):
                self._aec_push_warned = True
                self._log(f"[WARN] AEC push_far falló (eco no se cancelará): {type(exc).__name__}: {exc}")

    def _detect_barge_in(self, pcm_bytes: bytes) -> None:
        """Detecta la wake-word mientras Jarvis habla, para cortarlo (LIBRE).

        Llamado desde el thread worker de captura con el mic VIVO durante el
        playback. Pipeline: AEC (quita el eco de la voz de Jarvis del mic) ->
        wake-word ("Hey JARVIS") sobre la senal LIMPIA. El AEC es lo que hace
        viable la deteccion en parlantes; sin el, el eco enmascara tu voz.
        """
        if self._wakeword is None or not self._libre_speaking:
            return
        # Cancelar el eco antes de detectar. Si AEC desactivado, usa el mic crudo.
        if self._aec is not None:
            pcm_bytes = self._aec.process_near(pcm_bytes)
            if self._aec.last_erle_db > self._aec_erle_peak:
                self._aec_erle_peak = self._aec.last_erle_db
        score = self._wakeword.predict(pcm_bytes)
        if score > self._wakeword_peak:
            self._wakeword_peak = score
        if score >= self._wakeword.threshold:
            self._log(
                f"[BARGE-IN] wake-word '{self._wakeword.model_name}' "
                f"detectada (score={score:.2f}) -> corto a Jarvis"
            )
            self._trigger_barge_in()

    def _trigger_barge_in(self) -> None:
        """Confirma el barge-in: corta a Jarvis y abre tu turno de inmediato."""
        self._libre_speaking = False
        discarded = self.player.interrupt()  # silencia a Jarvis YA
        self.latency.mark_interrupted()
        self._log(f"[BARGE-IN] interrumpo a Jarvis ({discarded} chunks descartados)")
        if self._wakeword is not None:
            self._wakeword.reset()  # limpiar buffer para el proximo turno
        if self._aec is not None:
            self._aec.reset()
        if self.vad is not None:
            self.vad.reset()  # estado limpio para tu nuevo turno
        # Sin cooldown: querés hablar ahora mismo. Abrimos activity de usuario.
        self._last_activity_end_ms = 0
        if not getattr(self, "_libre_in_activity", False):
            self._libre_in_activity = True
            self.session.start_user_activity()
        self._set_overlay_state("listening")

    # ---- Eventos de la sesion Gemini (thread asyncio) ----

    def _on_connected(self) -> None:
        self._log("Conectado a Gemini Live")
        # Reconexion en LIBRE: la sesion NUEVA no heredo el activity_start que
        # mandamos a la anterior (que ya murio). Si dejaramos _libre_in_activity=True,
        # el VAD nunca reabriria una actividad y el turno quedaria colgado (sintoma
        # tipico en sesiones largas). Reseteamos a pizarra limpia para que el proximo
        # chunk de voz abra una actividad fresca contra la sesion actual.
        if self.mode == "LIBRE":
            self._libre_in_activity = False
            self._libre_speaking = False
            self._last_activity_end_ms = 0
            if self.vad is not None:
                self.vad.reset()
            if self._aec is not None:
                self._aec.reset()
            if self._wakeword is not None:
                self._wakeword.reset()
            self._set_overlay_state("listening")

        # Briefing matutino hablado: solo el primer connect del proceso.
        if not self._briefing_sent and self._morning_cfg.enabled:
            self._briefing_sent = True  # marcar antes para no reintentar en fallo
            try:
                if self._gemini_budget_available("[BRIEF] morning"):
                    events_provider = None
                    if self._morning_cfg.calendar_enabled:
                        from integrations.google_calendar import today_events
                        cred = Path(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS", ""))
                        events_provider = lambda: today_events(
                            credentials_path=cred,
                            token_path=Path("data") / "google_token.json",
                        )
                    data = collect_morning_brief(
                        vault_block=self._briefing_block_cache,
                        cfg=self._morning_cfg,
                        events_provider=events_provider,
                    )
                    prompt = render_brief_prompt(
                        data, max_age_days=self._morning_cfg.news_max_age_days)
                    self._log("[BRIEF] enviando briefing matutino hablado")
                    self.session.send_text(prompt)
            except Exception as exc:
                self._log(f"[WARN] briefing matutino falló: {exc}")

    def _on_connection_status(self, status: str, detail: str = "") -> None:
        self._tk(lambda: self.overlay.set_connection_status(status, detail))

    def _on_gemini_audio(self, pcm_bytes: bytes) -> None:
        # Marca TTFB en el primer chunk del turno. LatencyTracker.mark_first_audio
        # es idempotente por turno: solo cuenta la primera llamada.
        self.latency.mark_first_audio()
        # En LIBRE manejamos el eco segun haya wake-word disponible:
        #  - con wake-word: dejamos el mic VIVO para escuchar "Hey JARVIS" y
        #    poder cortar a Jarvis. Marcamos el inicio del turno hablado (1 vez)
        #    y limpiamos el buffer del detector.
        #  - sin wake-word (dep ausente o barge-in off): comportamiento clasico,
        #    silenciamos el mic (anti-echo); se reanuda en _on_playback_complete.
        if self.mode == "LIBRE":
            if self._barge_in_enabled and self._wakeword is not None:
                if not self._libre_speaking:
                    self._libre_speaking = True
                    self._wakeword_peak = 0.0
                    self._aec_erle_peak = 0.0
                    self._wakeword.reset()
                    if self._aec is not None:
                        self._aec.reset()
            else:
                self.capture.stop_recording()
        self._set_overlay_state("speaking")
        self._tk(
            lambda b=pcm_bytes: self.overlay.feed_voice_audio(b),
            coalesce_key="voice_audio",
        )
        self.player.push(pcm_bytes)

    def _on_gemini_text(self, text: str) -> None:
        self._output_transcript.append(text)
        self._tk(lambda: self.overlay.append_output(text))

    def _on_input_transcript(self, text: str) -> None:
        self._input_transcript.append(text)
        self._tk(lambda: self.overlay.append_input(text))

    def _on_interrupted(self) -> None:
        # Barge-in: limpia la cola de audio del player
        n = self.player.interrupt()
        self.latency.mark_interrupted()
        self._log(f"Interrumpido (descarte {n} chunks de audio)")
        self._set_overlay_state("listening")

    def _on_tool_start(self, name: str, args: dict | None = None) -> None:
        """Llamado desde JarvisSession antes de despachar un tool.

        Muestra hint humano en el overlay para que Isaac sepa que Jarvis
        esta trabajando (especialmente importante con ask_claude_deep
        donde Gemini queda mudo varios segundos esperando el response).
        """
        hint = TOOL_HINTS.get(name)
        self._tk(lambda: self.overlay.record_tool_start(name, args or {}))
        if not hint:
            return
        self._tk(lambda: self.overlay.log_event(hint.rstrip(".")))
        self._tk(lambda: self.overlay.append_output(f"\n[{hint}]\n"))

    def _on_tool_end(self, name: str, elapsed_ms: float, ok: bool, response=None) -> None:
        """Llamado tras el dispatch. Solo logueo aqui; el overlay no necesita
        confirmacion explicita porque la respuesta de Jarvis llegara enseguida."""
        self.latency.record_tool(name, elapsed_ms)
        self._tk(lambda: self.overlay.record_tool_end(name, elapsed_ms, ok, response))
        self._preview_camera_tool_result(name, response)
        if not ok:
            self._log(f"[TOOL] {name} fallo o timeout tras {elapsed_ms:.0f}ms")
            self._tk(lambda: self.overlay.log_event(f"Tool fallo: {name}", "error"))

    def _preview_camera_tool_result(self, name: str, response=None) -> None:
        if name != "camera_look" or not isinstance(response, dict):
            return
        attach = response.get("__attach_image") or {}
        jpeg_bytes = attach.get("png_bytes")
        if not isinstance(jpeg_bytes, (bytes, bytearray)) or not jpeg_bytes:
            return
        self._tk(lambda data=bytes(jpeg_bytes): self._show_camera_frame(data))

    def _on_turn_complete(self) -> None:
        # Cierra metricas del turno y loguea una linea condensada con TTFB.
        # mark_turn_complete devuelve el turno cerrado para logueo inmediato.
        turn = self.latency.mark_turn_complete()
        if turn is not None:
            line = self.latency.format_turn(turn)
            self._log(line)
            self._tk(lambda l=line: self.overlay.record_turn_latency(l))
        if self.mode == "PTT":
            self._set_overlay_state("idle")
        # En LIBRE: NO cambiar estado a "listening" aqui; el player puede seguir
        # reproduciendo. _on_playback_complete lo hara cuando la cola se vacie.
        # Append nueva linea para separar turnos en transcript
        self._tk(lambda: self.overlay.append_output("\n"))

        # Fase 1 — Continuidad: persistir el DELTA de este turno al journal.
        if self.session_continuity_enabled:
            try:
                user_delta = " ".join(
                    self._input_transcript[self._journal_input_idx:]
                ).strip()
                jarvis_delta = "".join(
                    self._output_transcript[self._journal_output_idx:]
                ).strip()
                self._journal_input_idx = len(self._input_transcript)
                self._journal_output_idx = len(self._output_transcript)
                if user_delta or jarvis_delta:
                    self.session_journal.append_turn(user_delta, jarvis_delta)
                # Fase 3 — Proactividad: observar el turno (encola, NO emite).
                if self.proactivity is not None and user_delta:
                    try:
                        self.proactivity.observe(
                            self.vault,
                            self.rag,
                            user_delta,
                            semantic_memory=self.semantic_memory,
                        )
                    except Exception as exc:
                        self._log(f"[WARN] proactividad (observe) falló: {exc}")
            except Exception as exc:
                self._log(f"[WARN] journal append falló: {exc}")

    def _on_playback_complete(self) -> None:
        """Llamado desde AudioPlayer._callback cuando la cola de audio se vacia.

        Reanuda el mic en modo LIBRE despues de que Jarvis termina de hablar.
        Se invoca desde el thread de sounddevice (audio output callback).
        """
        if self.mode != "LIBRE" or self._stopping:
            return
        # Jarvis termino de hablar sin barge-in: cerrar la ventana de barge-in.
        # Diagnostico: si el pico de wake-word fue notable pero no llego al
        # umbral, lo logueamos para poder calibrar (eco vs umbral vs acento).
        if self._libre_speaking and self._wakeword is not None and self._wakeword_peak > 0.1:
            erle = f", AEC ERLE peak={self._aec_erle_peak:.1f}dB" if self._aec is not None else ""
            self._log(
                f"[BARGE-IN] wake-word peak={self._wakeword_peak:.2f} este turno "
                f"(umbral {self._wakeword.threshold:.2f}){erle} — no disparo"
            )
        if self._libre_speaking or self.mode == "LIBRE":
            payload: dict = {}
            if self._aec is not None:
                payload["erlePeakDb"] = round(float(self._aec_erle_peak), 1)
            if self._wakeword is not None:
                payload["wakewordPeak"] = round(float(self._wakeword_peak), 2)
            if payload:
                self._tk(lambda p=payload: self.overlay.record_audio_telemetry(p))
        self._libre_speaking = False
        if self._wakeword is not None:
            self._wakeword.reset()
        if self._aec is not None:
            self._aec.reset()
        if self.vad is not None:
            self.vad.reset()  # limpiar estado VAD por si capto eco residual
        # Con barge-in ON el mic nunca se detuvo (start_recording es no-op);
        # con barge-in OFF aqui se reanuda tras el anti-echo.
        self.capture.start_recording()
        self._log("[LIBRE] playback completo -> mic reanudado")
        self._set_overlay_state("listening")

    def _on_error(self, exc: BaseException) -> None:
        record_error("jarvis.session", exc=exc, context={"handler": "_on_error"})
        self._log(f"[ERROR] {type(exc).__name__}: {exc}")
        self._set_overlay_state("idle" if self.mode == "PTT" else "listening")

    # ---- Vault watcher ----

    def _on_vault_change(self, kind: str, path: Path, n_chunks: int) -> None:
        rel = path.relative_to(self.vault.vault_path)
        self._log(f"vault {kind}: {rel} (+{n_chunks} chunks)")

    # ---- Helpers ----

    def _log(self, msg: str) -> None:
        # Heuristica para distribuir niveles sin reescribir 100 call-sites:
        # mensajes con [ERROR]/[WARN] van a sus niveles, el resto a INFO.
        lower = msg.lower()
        is_error = (
            "[ERROR]" in msg
            or " error:" in lower
            or " fallo" in lower
            or " falló" in lower
        )
        if is_error:
            record_error("jarvis.log", message=msg)
            log.error(msg)
        elif "[WARN]" in msg or "[WATCHDOG]" in msg:
            log.warning(msg)
        else:
            log.info(msg)

    _UI_ACTIVE_POLL_MS = 16  # ~60fps mientras hay trabajo pendiente.
    _UI_IDLE_POLL_MS = 80    # Reduce churn de comandos Tcl cuando la UI esta quieta.
    _UI_MAX_CALLBACKS_PER_PUMP = 80

    def _tk(self, fn, *, coalesce_key: str | None = None, force: bool = False) -> None:
        """Marshalla a main thread tkinter de forma thread-safe.

        Encola en _ui_thread (operación Python pura, sin Tcl) en vez de llamar
        root.after() desde el thread llamante. El pump del main thread drena la
        cola. Seguro desde cualquier thread, incl. workers de to_thread (que es
        donde corre la aprobación HITL y antes abortaba el proceso)."""
        if self._stopping and not force:
            return
        try:
            if force:
                self._ui_thread.submit_force(fn)
            elif coalesce_key:
                self._ui_thread.submit_latest(coalesce_key, fn)
            else:
                self._ui_thread.submit(fn)
        except Exception:
            pass

    def _install_ui_pump(self) -> None:
        """Instala (desde el main thread) el pump que drena _ui_thread vía after."""
        def _pump() -> None:
            max_callbacks = None if self._stopping else self._UI_MAX_CALLBACKS_PER_PUMP
            drained = self._ui_thread.drain(max_callbacks=max_callbacks)
            if self._stopping:
                return
            try:
                more_pending = self._ui_thread.pending() > 0
                delay_ms = (
                    self._UI_ACTIVE_POLL_MS
                    if more_pending or drained >= self._UI_MAX_CALLBACKS_PER_PUMP
                    else self._UI_IDLE_POLL_MS
                )
                self.overlay.after(delay_ms, _pump)
            except Exception:
                pass
        try:
            self.overlay.after(self._UI_ACTIVE_POLL_MS, _pump)
        except Exception:
            pass

    def _set_overlay_state(self, state: str) -> None:
        import time
        if state == "thinking":
            self._thinking_since_ms = int(time.time() * 1000)
        elif state in ("idle", "listening", "speaking", "blocked"):
            self._thinking_since_ms = None
        self._tk(
            lambda s=state: self.overlay.set_state(s),
            coalesce_key="overlay_state",
        )

    def _schedule_usage_flush(self) -> None:
        if self._stopping:
            return
        try:
            self.persistence.flush_snapshot(self.tracker)
        except Exception as exc:
            self._log(f"[WARN] flush telemetry fallo: {exc}")
        try:
            self.overlay.after(USAGE_FLUSH_MS, self._schedule_usage_flush)
        except Exception:
            pass

    def _schedule_thinking_watchdog(self) -> None:
        if self._stopping:
            return
        try:
            import time
            if self._thinking_since_ms is not None:
                elapsed = int(time.time() * 1000) - self._thinking_since_ms
                if elapsed >= THINKING_WATCHDOG_MS:
                    self._log(f"[WATCHDOG] thinking > {elapsed}ms; restaurando estado")
                    self._thinking_since_ms = None
                    self.overlay.set_state("idle" if self.mode == "PTT" else "listening")
                    self.overlay.append_output("\n[Jarvis: sigo escuchando; el turno anterior tardo mucho.]\n")
            self.overlay.after(THINKING_WATCHDOG_MS, self._schedule_thinking_watchdog)
        except Exception:
            pass

    def _historical_budget_costs(self) -> dict[str, float]:
        period = self.gate.period if hasattr(self, "gate") else "session"
        if period == "daily":
            hours = 24
        elif period == "weekly":
            hours = 24 * 7
        else:
            return {"gemini": 0.0, "claude": 0.0, "other": 0.0}
        return self.persistence.cost_by_provider_window(
            hours_back=hours,
            exclude_session_id=self.session_id,
        )

    def _build_reasoner(self) -> ClaudeReasoner | None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            self._log("ANTHROPIC_API_KEY no configurada; ask_claude_deep queda desactivada")
            return None
        try:
            return ClaudeReasoner(tracker=self.tracker)
        except Exception as exc:
            self._log(f"[WARN] ClaudeReasoner no disponible: {type(exc).__name__}: {exc}")
            return None

    def _build_code_reasoner(self) -> GPT55CodeReasoner | None:
        if not os.environ.get("OPENAI_API_KEY"):
            self._log("OPENAI_API_KEY no configurada; ask_gpt55_code queda desactivada")
            return None
        try:
            return GPT55CodeReasoner(tracker=self.tracker)
        except Exception as exc:
            self._log(f"[WARN] GPT55CodeReasoner no disponible: {type(exc).__name__}: {exc}")
            return None

    def _ensure_libre_models(self) -> None:
        """Carga (idempotente, thread-safe) los modelos de LIBRE: Silero VAD y,
        si el barge-in esta activo, el wakeword.

        La llaman el pre-warm de arranque (background) y _apply_listen_mode. El
        lock evita que ambos carguen a la vez. Pre-cargar al arranque hace que el
        toggle a LIBRE sea instantaneo en vez de congelar el thread del hotkey o
        del worker de la tool durante la carga (1-3s de Silero/ONNX).
        """
        with self._libre_models_lock:
            if self.vad is None:
                self._log("Cargando Silero VAD para modo libre...")
                try:
                    self.vad = VADGate()
                except Exception as exc:
                    self._log(f"[WARN] no pude cargar Silero VAD: {type(exc).__name__}: {exc}")
            if self._barge_in_enabled:
                self._ensure_wakeword()

    def _ensure_wakeword(self) -> None:
        """Carga perezosa del detector wake-word para el barge-in (LIBRE).

        Degrada con gracia: si openWakeWord no esta instalado o falla, desactiva
        el barge-in para la sesion (en vez de crashear). Idempotente.
        """
        if self._wakeword is not None or not self._barge_in_enabled:
            return
        try:
            self._log(f"Cargando wake-word '{self._wakeword_model}' para barge-in...")
            self._wakeword = WakeWordGate(
                model_name=self._wakeword_model,
                threshold=self._wakeword_threshold,
            )
            self._log("[MODE] Barge-in por wake-word activo. Deci 'Hey JARVIS' para cortar.")
        except Exception as exc:
            self._barge_in_enabled = False
            self._log(
                f"[WARN] wake-word no disponible ({type(exc).__name__}: {exc}); "
                "barge-in desactivado. Instala: pip install openwakeword"
            )

    def _save_session_memory(self) -> None:
        """Cierre limpio: sintetiza el journal en una nota-diario fechada.

        Idempotente (corre una sola vez por proceso). Si la continuidad está
        desactivada o no hay reasoner, no hace nada. Si la síntesis falla, el
        journal queda intacto y se reintenta como huérfano al próximo arranque.
        """
        if self._session_saved or not self.session_continuity_enabled:
            return
        self._session_saved = True
        p = synthesize_and_save(
            self.session_journal,
            self.reasoner,
            self.vault,
            min_turns=self.session_min_turns,
            session_id=self.session_id,
        )
        if p is None:
            return
        try:
            self.rag.index_file(p)
            self.rag.save()
        except Exception as exc:
            self._log(f"[WARN] no se indexó nota de sesión: {exc}")
        self._log(f"nota de sesión guardada: {p.relative_to(self.vault.vault_path)}")


def main() -> int:
    _install_crash_capture()
    print("=" * 60)
    print("  JARVIS — Asistente Conversacional Tiempo Real")
    print("  Sesion local · Sonnet 4.6 + Gemini 3.1 Flash Live")
    print(f"  Version: {JARVIS_VERSION_LABEL}")
    print("=" * 60)

    # --- Healthcheck de arranque (fail-fast) ---
    # Verifica env, vault, audio, disco antes de levantar Gemini Live.
    # Configurable: JARVIS_HEALTHCHECK_STRICT=false desactiva el abort.
    from jarvis_health import run_healthcheck

    strict = os.environ.get("JARVIS_HEALTHCHECK_STRICT", "true").lower() == "true"
    timeout = float(os.environ.get("JARVIS_HEALTHCHECK_TIMEOUT", "5.0"))
    health = run_healthcheck(strict=strict, ping_gemini=False, timeout_s=timeout)
    for c in health.checks:
        if c.ok:
            status = "OK  "
        elif c.optional:
            status = "WARN"
        else:
            status = "FAIL"
        print(f"  [{status}] {c.name:<14s} ({c.elapsed_ms:>6.1f}ms)  {c.detail}")
    if not health.ok:
        print(f"\n[jarvis] healthcheck FAILED. Revisa los items con [FAIL] arriba.")
        print(f"[jarvis] Para arrancar igual en modo degraded: JARVIS_HEALTHCHECK_STRICT=false")
        return 2
    print(f"[jarvis] healthcheck OK ({health.total_ms:.0f}ms)")
    print("=" * 60)

    jarvis = Jarvis()
    jarvis.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
