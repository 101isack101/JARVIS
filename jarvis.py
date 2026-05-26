"""
jarvis.py - Entry point orquestador de Jarvis.

Une todos los modulos:
  - JarvisOverlay (tkinter, main thread)
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
import uuid
from datetime import datetime
from pathlib import Path

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
from audio.vad import VADGate
from claude.reasoner import ClaudeReasoner
from gemini.session import JarvisSession, SessionCallbacks, SessionConfig
from gemini.system_prompt import SYSTEM_PROMPT
from memory.indexer import IncrementalIndexer
from memory import notes as notes_mod
from memory.obsidian_vault import ObsidianVault
from memory.rag import VaultRAG
from memory.tools import ToolContext, ToolDispatcher, make_tool_object
from mcp_obsidian.client import ObsidianMCPClient
from overlay.hotkeys import HotkeyCallbacks, HotkeyListener
from overlay.window import JarvisOverlay
from runtime_modes import ModeManager
from runtime_preferences import ensure_runtime_preferences, preferences_prompt_block
from security.approvals import ApprovalBroker
from security.kill_switch import hard_exit
from telemetry.budgets import BudgetGate
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

USAGE_FLUSH_MS = 10_000
THINKING_WATCHDOG_MS = 15_000
# Cooldown despues de activity_end: ignora nuevos activity_start del VAD
# por este tiempo. Evita que un "uhm" inmediato dispare otra activity y
# auto-interrumpa la respuesta de Gemini antes de que pueda articularla.
LIBRE_ACTIVITY_COOLDOWN_MS = 600

# Hints humanos para mostrar en el overlay cuando arranca un tool.
# Solo se muestran tools "lentos" (>1s tipicos) — los rapidos no necesitan
# feedback porque Jarvis responde casi inmediatamente despues.
TOOL_HINTS: dict[str, str] = {
    "ask_claude_deep": "Consultando con Claude…",
    "screen_look": "Mirando tu pantalla…",
    "chrome_read_page": "Leyendo Chrome…",
    "study_mode": "Study Mode trabajando…",
    "jarvis_recall": "Buscando en tus notas…",
    "jarvis_browse": "Listando notas…",
    "jarvis_remember": "Guardando en tu vault…",
    "obsidian_mcp": "Operando en Obsidian…",
    "jarvis_run_safe_command": "Ejecutando comando…",
    "spotify_control": "Controlando Spotify…",
}

# Per-tool timeouts. ask_claude_deep tipicamente tarda 8-15s en prompts
# complejos; el default global (12s) caia justo en el limite. 30s da margen
# real, y como ahora cancela la HTTP de verdad (ask_async), no hay budget
# desperdiciado si en algun caso se pasa.
TOOL_TIMEOUTS_S: dict[str, float] = {
    "ask_claude_deep": 30.0,
    "study_mode": 60.0,
    "chrome_read_page": 20.0,
}


class Jarvis:
    """Orquestador. Lifecycle: build -> run -> stop."""

    def __init__(self) -> None:
        self.session_id = uuid.uuid4().hex[:8]
        self._stopping = False
        self._input_transcript: list[str] = []
        self._output_transcript: list[str] = []
        self._thinking_since_ms: int | None = None
        # Timestamp del ultimo activity_end disparado por VAD en LIBRE.
        # Usado para suprimir activity_start inmediato (anti-rebote de "uhm").
        self._last_activity_end_ms: int = 0

        # Telemetria (compartida)
        self.tracker = TokenTracker()
        self.latency = LatencyTracker(window=50)
        self.persistence = UsagePersistence(ROOT / "data" / "usage.db", self.session_id)
        self.gate = BudgetGate(history_provider_costs=self._historical_budget_costs)
        self.modes = ModeManager()
        self.approvals = ApprovalBroker(timeout_s=30.0)
        self.actions = SafeActionExecutor(root=ROOT, approval_broker=self.approvals)
        self.screen = ScreenCapture(ROOT / "data" / "screenshots")
        self.preferences = ensure_runtime_preferences(ROOT / "data" / "preferences.json")
        self.reasoner = self._build_reasoner()
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

        self.tool_ctx = ToolContext(
            vault=self.vault,
            rag=self.rag,
            reasoner=self.reasoner,
            tracker=self.tracker,
            gate=self.gate,
            screen=self.screen,
            actions=self.actions,
            modes=self.modes,
            obsidian_mcp=self.obsidian_mcp,
            approvals=self.approvals,
        )
        self.dispatcher = ToolDispatcher(self.tool_ctx)
        self.indexer = IncrementalIndexer(
            self.rag,
            on_change=self._on_vault_change,
        )

        # Audio
        self.player = AudioPlayer(on_underflow=self._on_playback_complete)
        self.capture = AudioCapture(on_chunk=self._on_audio_chunk)
        self.vad: VADGate | None = None  # lazy load en modo libre

        # Overlay
        self.overlay = JarvisOverlay(self.tracker, self.gate, on_close=self.stop)
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
                system_prompt=SYSTEM_PROMPT + "\n\n" + preferences_prompt_block(self.preferences),
                manual_activity_mode=True,
                enable_input_transcription=True,
                tools=[make_tool_object()],
                tracker=self.tracker,
                tool_dispatcher=self.dispatcher,
                tool_timeouts_s=TOOL_TIMEOUTS_S,
            ),
            callbacks=session_callbacks,
        )

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

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._log("Cerrando Jarvis...")
        try: self.hotkey_listener.stop()
        except Exception: pass
        try: self.session.stop()
        except Exception: pass
        self._tk(lambda: self.overlay.set_connection_status("stopped", ""))
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
            self.stop()

    # ---- Hotkey handlers (llamados desde thread de keyboard) ----

    def _on_ptt_press(self) -> None:
        if not self.gate.can_invoke(self.tracker, "gemini"):
            self._log("[PTT] press IGNORADO: gemini bloqueado por budget")
            self._set_overlay_state("blocked")
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
        if self.mode == "PTT":
            self.mode = "LIBRE"
            if self.vad is None:
                self._log("Cargando Silero VAD para modo libre...")
                self.vad = VADGate()
            self.vad.reset()
            self._libre_in_activity = False
            self.capture.start_recording()
            self._log("[MODE] LIBRE activado. VAD controla activity_start/end.")
            self._tk(lambda: self.overlay.set_mode("LIBRE"))
            self._set_overlay_state("listening")
        else:
            self.mode = "PTT"
            self.capture.stop_recording()
            # Si VAD dejo activity abierta, cerrarla
            if getattr(self, "_libre_in_activity", False):
                self.session.end_user_activity()
                self._libre_in_activity = False
            self._log("[MODE] PTT restaurado.")
            self._tk(lambda: self.overlay.set_mode("PTT"))
            self._set_overlay_state("idle")

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
            self._set_overlay_state("thinking")
            self.session.send_image(
                shot.png_bytes,
                mime_type=shot.mime_type,
                prompt=visual_capture_prompt("screen"),
            )
        except Exception as exc:
            self._log(f"[SCREEN] error: {type(exc).__name__}: {exc}")

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
        """Crea y muestra el RegionSelector. DEBE correr en main thread."""
        try:
            RegionSelector(
                self.overlay.root,
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
        # En modo LIBRE, VAD local controla activity boundaries.
        # En modo PTT, los maneja la hotkey (press = start, release = end).
        if self.mode == "LIBRE" and self.vad is not None:
            # Echo guard: ignorar mic mientras Jarvis habla por altavoces.
            # Sin esto, el VAD detecta la voz de Jarvis como input y la interrumpe.
            if self.player.is_playing():
                return
            import time as _time
            now_ms = int(_time.time() * 1000)
            events = self.vad.feed(pcm_bytes)
            for ev in events:
                if ev.kind == "start":
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

    # ---- Eventos de la sesion Gemini (thread asyncio) ----

    def _on_connected(self) -> None:
        self._log("Conectado a Gemini Live")

    def _on_connection_status(self, status: str, detail: str = "") -> None:
        self._tk(lambda: self.overlay.set_connection_status(status, detail))

    def _on_gemini_audio(self, pcm_bytes: bytes) -> None:
        # Marca TTFB en el primer chunk del turno. LatencyTracker.mark_first_audio
        # es idempotente por turno: solo cuenta la primera llamada.
        self.latency.mark_first_audio()
        # En LIBRE: silenciar mic mientras Jarvis habla (anti-echo).
        # Se reanuda en _on_playback_complete cuando la cola del player se vacia.
        if self.mode == "LIBRE":
            self.capture.stop_recording()
        self._set_overlay_state("speaking")
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

    def _on_tool_start(self, name: str) -> None:
        """Llamado desde JarvisSession antes de despachar un tool.

        Muestra hint humano en el overlay para que Isaac sepa que Jarvis
        esta trabajando (especialmente importante con ask_claude_deep
        donde Gemini queda mudo varios segundos esperando el response).
        """
        hint = TOOL_HINTS.get(name)
        if not hint:
            return
        self._tk(lambda: self.overlay.append_output(f"\n[{hint}]\n"))

    def _on_tool_end(self, name: str, elapsed_ms: float, ok: bool) -> None:
        """Llamado tras el dispatch. Solo logueo aqui; el overlay no necesita
        confirmacion explicita porque la respuesta de Jarvis llegara enseguida."""
        self.latency.record_tool(name, elapsed_ms)
        if not ok:
            self._log(f"[TOOL] {name} fallo o timeout tras {elapsed_ms:.0f}ms")

    def _on_turn_complete(self) -> None:
        # Cierra metricas del turno y loguea una linea condensada con TTFB.
        # mark_turn_complete devuelve el turno cerrado para logueo inmediato.
        turn = self.latency.mark_turn_complete()
        if turn is not None:
            self._log(self.latency.format_turn(turn))
        if self.mode == "PTT":
            self._set_overlay_state("idle")
        # En LIBRE: NO cambiar estado a "listening" aqui; el player puede seguir
        # reproduciendo. _on_playback_complete lo hara cuando la cola se vacie.
        # Append nueva linea para separar turnos en transcript
        self._tk(lambda: self.overlay.append_output("\n"))

    def _on_playback_complete(self) -> None:
        """Llamado desde AudioPlayer._callback cuando la cola de audio se vacia.

        Reanuda el mic en modo LIBRE despues de que Jarvis termina de hablar.
        Se invoca desde el thread de sounddevice (audio output callback).
        """
        if self.mode != "LIBRE" or self._stopping:
            return
        if self.vad is not None:
            self.vad.reset()  # limpiar estado VAD por si capto eco residual
        self.capture.start_recording()
        self._log("[LIBRE] playback completo -> mic reanudado")
        self._set_overlay_state("listening")

    def _on_error(self, exc: BaseException) -> None:
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
        if "[ERROR]" in msg:
            log.error(msg)
        elif "[WARN]" in msg or "[WATCHDOG]" in msg:
            log.warning(msg)
        else:
            log.info(msg)

    def _tk(self, fn) -> None:
        """Marshalla a main thread tkinter."""
        try:
            self.overlay.root.after(0, fn)
        except Exception:
            pass

    def _set_overlay_state(self, state: str) -> None:
        import time
        if state == "thinking":
            self._thinking_since_ms = int(time.time() * 1000)
        elif state in ("idle", "listening", "speaking", "blocked"):
            self._thinking_since_ms = None
        self._tk(lambda: self.overlay.set_state(state))

    def _schedule_usage_flush(self) -> None:
        if self._stopping:
            return
        try:
            self.persistence.flush_snapshot(self.tracker)
        except Exception as exc:
            self._log(f"[WARN] flush telemetry fallo: {exc}")
        try:
            self.overlay.root.after(USAGE_FLUSH_MS, self._schedule_usage_flush)
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
            self.overlay.root.after(THINKING_WATCHDOG_MS, self._schedule_thinking_watchdog)
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

    def _save_session_memory(self) -> None:
        input_text = " ".join(self._input_transcript).strip()
        output_text = "".join(self._output_transcript).strip()
        snap = self.tracker.snapshot()
        if not input_text and not output_text and snap.total_cost_usd == 0:
            return
        title = f"Jarvis session {datetime.now().strftime('%Y-%m-%d %H%M')} {self.session_id}"
        body = (
            f"# {title}\n\n"
            f"- Session ID: `{self.session_id}`\n"
            f"- Modo final: `{self.modes.mode}`\n"
            f"- Costo estimado: `${snap.total_cost_usd:.6f}`\n"
            f"- Duracion: `{snap.session_duration_s():.0f}s`\n\n"
            "## Isaac dijo\n\n"
            f"{input_text[:4000] or '(sin transcript de entrada)'}\n\n"
            "## Jarvis respondio\n\n"
            f"{output_text[:4000] or '(sin transcript de salida)'}\n"
        )
        path = self.vault.memory_file(title)
        notes_mod.write_note(
            self.vault,
            path,
            body=body,
            tags=["jarvis-session", "episodic-memory"],
        )
        self.rag.index_file(path)
        self.rag.save()
        self._log(f"memoria episodica guardada: {path.relative_to(self.vault.vault_path)}")


def main() -> int:
    print("=" * 60)
    print("  JARVIS — Asistente Conversacional Tiempo Real")
    print("  Sesion local · Sonnet 4.6 + Gemini 3.1 Flash Live")
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
