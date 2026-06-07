"""
gemini/session.py - Sesion Gemini Live envuelta en una clase asyncio.

JarvisSession maneja el ciclo de vida del WebSocket bidireccional con
Gemini Live, expone eventos via callbacks thread-safe, y permite enviar
audio/texto desde cualquier thread mediante asyncio.run_coroutine_threadsafe.

Modelo de threading:
  - JarvisSession.run() corre en un worker thread con su propio asyncio loop
  - Desde el thread principal (tkinter), llamar a metodos publicos (send_audio,
    start_user_activity, etc.) los marshalla al loop via run_coroutine_threadsafe
  - Los callbacks (on_audio, on_text, ...) se llaman desde el thread asyncio;
    el consumidor debe marshallar a tkinter con root.after(0, ...)
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from dataclasses import dataclass, field
from typing import Callable

from google import genai
from google.genai import types

from vision.prompts import visual_capture_prompt

DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
INPUT_MIME_PCM_16K = "audio/pcm;rate=16000"
DEFAULT_TOOL_TIMEOUT_S = 12.0

# Modelo virtual para reportar tokens al tracker (granularidad por kind)
def _gemini_model_key(model: str, kind: str) -> str:
    return f"{model}:{kind}"


def _safe_tool_args_for_log(args: dict, max_chars: int = 240) -> dict:
    """Redacta y acorta argumentos antes de escribirlos al log."""
    try:
        from security.secret_filter import redact_log_text
    except Exception:
        redact_log_text = lambda text, max_chars=max_chars: str(text)[:max_chars]  # noqa: E731

    sensitive_keys = {
        "content",
        "context_extra",
        "prompt",
        "text",
        "details",
        "stdout",
        "stderr",
    }
    safe: dict = {}
    for key, value in (args or {}).items():
        if isinstance(value, str):
            if key in sensitive_keys:
                safe[key] = redact_log_text(value, max_chars=max_chars)
            else:
                safe[key] = redact_log_text(value, max_chars=max_chars)
        else:
            safe[key] = value
    return safe


def _call_compatible(callback: Callable, *args) -> None:
    """Call callback with as many positional args as it can accept.

    Keeps SessionCallbacks source-compatible with older 1-arg/3-arg tool
    callbacks while allowing richer args/response callbacks for the overlay.
    """
    try:
        sig = inspect.signature(callback)
    except (TypeError, ValueError):
        callback(*args)
        return

    params = list(sig.parameters.values())
    if any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params):
        callback(*args)
        return

    positional = [
        p
        for p in params
        if p.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    callback(*args[: len(positional)])


@dataclass
class SessionCallbacks:
    """Callbacks invocados desde el thread asyncio de la sesion.

    El consumidor (overlay, jarvis.py) debe marshallar a su thread con
    `root.after(0, callback)` o similar si va a tocar UI.
    """

    on_audio: Callable[[bytes], None] = lambda _: None
    on_text: Callable[[str], None] = lambda _: None
    on_input_transcript: Callable[[str], None] = lambda _: None
    on_interrupted: Callable[[], None] = lambda: None
    on_turn_complete: Callable[[], None] = lambda: None
    on_connected: Callable[[], None] = lambda: None
    on_error: Callable[[BaseException], None] = lambda _: None
    on_log: Callable[[str], None] = lambda _: None
    on_connection_status: Callable[[str, str], None] = lambda *_: None
    # Disparado al iniciar/terminar un tool dispatch. Util para mostrar
    # feedback en el overlay ("Consultando con Claude...") durante tools
    # largos donde Gemini queda mudo esperando el FunctionResponse.
    # on_tool_start recibe (name, args).
    # on_tool_end recibe (name, elapsed_ms, ok, response).
    on_tool_start: Callable[[str, dict], None] = lambda *_: None
    on_tool_end: Callable[[str, float, bool, object], None] = lambda *_: None


@dataclass
class SessionConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    voice: str = "Aoede"
    system_prompt: str = "Eres un asistente conversacional en espanol."
    manual_activity_mode: bool = True  # True=PTT, False=auto VAD del servidor
    enable_input_transcription: bool = True
    tools: list = field(default_factory=list)  # types.Tool list para function calling
    tracker: object | None = None         # TokenTracker (opcional)
    tool_dispatcher: object | None = None  # ToolDispatcher (opcional)
    tool_timeout_s: float = DEFAULT_TOOL_TIMEOUT_S
    # Override de timeout por tool. Ej: {"ask_claude_deep": 30.0} para dar
    # mas margen a Claude sin alargar el resto. Si la tool no esta listada
    # cae en `tool_timeout_s`.
    tool_timeouts_s: dict[str, float] = field(default_factory=dict)
    # Context window compression: ventana deslizante que comprime turnos viejos
    # cuando el contexto se acerca al limite, para que sesiones LARGAS no se
    # saturen (sintoma: respuestas lentisimas y "empieza de cero" al truncar).
    # trigger_tokens=cuando dispara la compresion; target_tokens=a cuanto baja.
    context_compression: bool = True
    context_trigger_tokens: int = 25600
    context_target_tokens: int = 12800


class JarvisSession:
    """Wrapper asyncio sobre google-genai live.connect()."""

    def __init__(self, config: SessionConfig, callbacks: SessionCallbacks) -> None:
        self.config = config
        self.cb = callbacks
        self._client: genai.Client | None = None
        self._session = None  # type: ignore[var-annotated]
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected = asyncio.Event()
        self._stop_event: asyncio.Event | None = None
        self._receive_task: asyncio.Task | None = None
        # Session resumption: token que Gemini nos manda periodicamente.
        # Lo usamos al reconectar para retomar la sesion en el mismo estado.
        self._resumption_handle: str | None = None
        self._reconnect_attempts = 0
        self._max_reconnects = 50  # plenty para una sesion larga
        self._submit_drop_warned = False
        self._go_away_requested = False

    # ---- Lifecycle ----

    def start(self) -> None:
        """Arranca el loop asyncio en un worker thread y conecta."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._thread_target, name="JarvisSession", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Cierra la sesion y termina el thread."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None

    def _thread_target(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except Exception as exc:
            self.cb.on_error(exc)
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            except Exception:
                pass
            self._loop.close()

    async def _async_stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()

    async def _async_main(self) -> None:
        """Loop de reconexion con backoff exponencial.

        Gemini Live cierra la WS al finalizar cada turn_complete (sin
        session_resumption). Reconectamos usando el handle guardado para
        mantener continuidad. Diferenciamos dos escenarios:

        - Cierre limpio post-turn: el receive_loop salio sin excepcion y
          tenemos handle valido. Reconexion rapida (0.3s).
        - Cierre por error (network flap, rate limit, 5xx): backoff
          exponencial empezando en JARVIS_RECONNECT_BACKOFF_BASE (default 1s)
          y duplicando hasta JARVIS_RECONNECT_BACKOFF_MAX (default 16s).
        """
        import os as _os
        self._stop_event = asyncio.Event()
        self._client = genai.Client(
            api_key=self.config.api_key,
            http_options={"api_version": "v1beta"},
        )

        backoff_base = float(_os.environ.get("JARVIS_RECONNECT_BACKOFF_BASE", "1.0"))
        backoff_max = float(_os.environ.get("JARVIS_RECONNECT_BACKOFF_MAX", "16.0"))
        consecutive_errors = 0

        while not self._stop_event.is_set():
            had_error = False
            try:
                await self._connect_once()
            except Exception as exc:
                had_error = True
                self.cb.on_log(f"[ERROR] connect_once excepcion: {type(exc).__name__}: {exc}")
                self.cb.on_error(exc)

            if self._stop_event.is_set():
                break

            self._reconnect_attempts += 1
            if self._reconnect_attempts > self._max_reconnects:
                self.cb.on_log(f"[ERROR] Excedido limite de reconexiones ({self._max_reconnects})")
                break

            if had_error:
                consecutive_errors += 1
                delay = min(backoff_base * (2 ** (consecutive_errors - 1)), backoff_max)
                self.cb.on_connection_status("reconnecting", f"reintentando en {delay:.1f}s")
                self.cb.on_log(
                    f"Reconectando con backoff #{consecutive_errors}: "
                    f"esperando {delay:.1f}s (intento total #{self._reconnect_attempts})..."
                )
                # Esperar respetando stop_event para no bloquear shutdown
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    break  # stop_event se activo durante la espera
                except asyncio.TimeoutError:
                    pass
            else:
                consecutive_errors = 0  # turno limpio: reset del backoff
                self.cb.on_connection_status("reconnecting", "reconectando sesion Live")
                self.cb.on_log(
                    f"Reconectando (intento #{self._reconnect_attempts}, "
                    f"handle={'si' if self._resumption_handle else 'no'})..."
                )
                await asyncio.sleep(0.3)

    async def _connect_once(self) -> None:
        """Una sesion completa: conecta, recibe hasta cierre, retorna."""
        self._go_away_requested = False
        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=self.config.voice
                )
            )
        )

        realtime_cfg = types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                disabled=self.config.manual_activity_mode
            )
        )

        # SessionResumptionConfig: signal al server que vamos a manejar
        # reconexion. Pasa el handle si lo tenemos para retomar contexto.
        resumption = types.SessionResumptionConfig(handle=self._resumption_handle)

        live_cfg_kwargs = dict(
            response_modalities=["AUDIO"],
            speech_config=speech_config,
            system_instruction=types.Content(
                parts=[types.Part(text=self.config.system_prompt)]
            ),
            realtime_input_config=realtime_cfg,
            session_resumption=resumption,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
        )
        # Context window compression: clave para sesiones LARGAS. Sin esto, el
        # contexto de Live se llena -> respuestas lentisimas y reset ("empieza
        # de cero"). La ventana deslizante comprime turnos viejos al superar
        # trigger_tokens, bajando a target_tokens, manteniendo la sesion viva.
        if self.config.context_compression:
            live_cfg_kwargs["context_window_compression"] = (
                types.ContextWindowCompressionConfig(
                    trigger_tokens=self.config.context_trigger_tokens,
                    sliding_window=types.SlidingWindow(
                        target_tokens=self.config.context_target_tokens
                    ),
                )
            )
        if self.config.enable_input_transcription:
            live_cfg_kwargs["input_audio_transcription"] = (
                types.AudioTranscriptionConfig()
            )
            live_cfg_kwargs["output_audio_transcription"] = (
                types.AudioTranscriptionConfig()
            )
        if self.config.tools:
            live_cfg_kwargs["tools"] = self.config.tools

        live_cfg = types.LiveConnectConfig(**live_cfg_kwargs)

        self.cb.on_connection_status("connecting", f"conectando {self.config.model}")
        self.cb.on_log(f"Conectando a {self.config.model} (voz {self.config.voice})")
        try:
            async with self._client.aio.live.connect(
                model=self.config.model, config=live_cfg
            ) as session:
                self._session = session
                self.cb.on_connected()
                self.cb.on_connection_status("connected", "Gemini Live conectado")
                self.cb.on_log("Conectado")
                self._receive_task = asyncio.create_task(self._receive_loop())
                # Esperar que receive_loop termine (server cerro WS) O stop_event
                done, pending = await asyncio.wait(
                    [self._receive_task, asyncio.create_task(self._stop_event.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                self._session = None
        except Exception as exc:
            self.cb.on_connection_status("error", f"{type(exc).__name__}: {exc}")
            self.cb.on_error(exc)
            self._session = None

    async def _receive_loop(self) -> None:
        """Lee del WS continuamente y dispara callbacks con logging completo.

        Maneja eventos:
          - server_content (audio, texto, transcripts, interrupciones, turn_complete)
          - tool_call -> dispatcher local + send_tool_response
          - usage_metadata -> reporta tokens al TokenTracker
          - go_away -> servidor avisa que cierra la conexion
          - session_resumption_update -> servidor manda token para resumir
        """
        msg_count = 0
        turn_count = 0
        try:
            while self._stop_event is not None and not self._stop_event.is_set():
                saw_message = False
                turn_count += 1
                async for response in self._session.receive():
                    saw_message = True
                    msg_count += 1
                    action = await self._handle_response(response, msg_count)
                    if action == "reconnect":
                        self.cb.on_log("go_away procesado: cerrando sesion para reconectar limpio")
                        return
                    if action == "turn_complete":
                        break

                if not saw_message:
                    # receive() agotado sin mensajes: eso si parece cierre real.
                    self.cb.on_log(
                        f"[WARN] receive_loop sin mensajes en turno #{turn_count}; "
                        "conexion posiblemente cerrada"
                    )
                    break
        except asyncio.CancelledError:
            self.cb.on_log(f"receive_loop cancelado tras {msg_count} mensajes")
            raise
        except Exception as exc:
            self.cb.on_log(f"[ERROR] receive_loop excepcion tras {msg_count} mensajes: {type(exc).__name__}: {exc}")
            self.cb.on_error(exc)

    async def _handle_response(self, response, msg_count: int) -> str | None:
        """Procesa un mensaje Live.

        Devuelve:
          - "turn_complete" para cerrar el turno actual y seguir escuchando.
          - "reconnect" para salir del async context y reconectar limpio.
          - None para continuar leyendo mensajes.
        """
        # --- Telemetria ---
        self._record_usage(response)

        # --- session_resumption_update ---
        # Procesar antes de go_away para conservar el handle mas fresco si el
        # servidor avisa que la sesion expira.
        sru = getattr(response, "session_resumption_update", None)
        if sru:
            new_handle = getattr(sru, "new_handle", None)
            resumable = getattr(sru, "resumable", False)
            if resumable and new_handle:
                self._resumption_handle = new_handle
                self.cb.on_log(f"session_resumption_update: handle guardado ({len(new_handle)} chars)")
            else:
                self.cb.on_log(f"session_resumption_update: resumable={resumable}, no handle")

        # --- go_away (servidor desconecta pronto) ---
        if getattr(response, "go_away", None):
            ttl = getattr(response.go_away, "time_left", None)
            self._go_away_requested = True
            self.cb.on_connection_status("reconnecting", "Gemini pidio renovar sesion")
            self.cb.on_log(f"[WARN] go_away recibido (ttl={ttl}). Cerrando para reconectar limpio.")
            return "reconnect"

        # --- Tool calls ---
        if getattr(response, "tool_call", None):
            n_calls = len(getattr(response.tool_call, "function_calls", []) or [])
            self.cb.on_log(f"tool_call: {n_calls} function_calls")
            await self._handle_tool_call(response.tool_call)

        # --- Server content ---
        sc = getattr(response, "server_content", None)
        if sc is None:
            if not getattr(response, "tool_call", None) and not getattr(response, "usage_metadata", None):
                self.cb.on_log(f"msg #{msg_count}: sin server_content (raro)")
            return None

        if getattr(sc, "interrupted", False):
            self.cb.on_log("server_content.interrupted")
            self.cb.on_interrupted()

        input_tx = getattr(sc, "input_transcription", None)
        if input_tx and getattr(input_tx, "text", None):
            self.cb.on_input_transcript(input_tx.text)

        # Output transcription: el texto de lo que Jarvis esta diciendo en voz.
        # Gemini Live lo emite cuando `output_audio_transcription` esta activado
        # en la LiveConnectConfig (lo esta — ver _connect_once arriba).
        # Lo ruteamos a `on_text` para que aparezca en el panel "JARVIS" del
        # overlay junto al audio. Asi Isaac puede leer terminos tecnicos y
        # copiarlos en otras plataformas.
        output_tx = getattr(sc, "output_transcription", None)
        if output_tx and getattr(output_tx, "text", None):
            self.cb.on_text(output_tx.text)

        if getattr(sc, "model_turn", None):
            audio_chunks = 0
            text_parts = 0
            for part in sc.model_turn.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    audio_chunks += 1
                    self.cb.on_audio(part.inline_data.data)
                if getattr(part, "text", None):
                    text_parts += 1
                    self.cb.on_text(part.text)
            if audio_chunks or text_parts:
                self.cb.on_log(f"model_turn: {audio_chunks} audio, {text_parts} text")

        if getattr(sc, "turn_complete", False):
            self.cb.on_log(f"turn_complete (msg #{msg_count})")
            self.cb.on_turn_complete()
            return "turn_complete"
        return None

    def _record_usage(self, response) -> None:
        """Parsea usage_metadata de Gemini Live y publica al TokenTracker."""
        if not self.config.tracker:
            return
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return
        # Gemini Live entrega tokens por modalidad. Sumamos por kind.
        # Campos posibles: prompt_token_count, response_token_count,
        # prompt_tokens_details (lista de {modality, token_count}), etc.
        try:
            prompt_details = getattr(usage, "prompt_tokens_details", None) or []
            response_details = getattr(usage, "response_tokens_details", None) or []
            for d in prompt_details:
                modality = (getattr(d, "modality", "") or "").lower()
                tokens = int(getattr(d, "token_count", 0) or 0)
                kind = self._modality_to_kind(modality, "in")
                self.config.tracker.record(
                    _gemini_model_key(self.config.model, kind),
                    input_tokens=tokens,
                )
            for d in response_details:
                modality = (getattr(d, "modality", "") or "").lower()
                tokens = int(getattr(d, "token_count", 0) or 0)
                kind = self._modality_to_kind(modality, "out")
                self.config.tracker.record(
                    _gemini_model_key(self.config.model, kind),
                    output_tokens=tokens,
                )
            # Fallback: si no hay details granulares, usar totales
            if not prompt_details and not response_details:
                p = int(getattr(usage, "prompt_token_count", 0) or 0)
                r = int(getattr(usage, "response_token_count", 0) or 0)
                if p:
                    self.config.tracker.record(
                        _gemini_model_key(self.config.model, "text-in"),
                        input_tokens=p,
                    )
                if r:
                    self.config.tracker.record(
                        _gemini_model_key(self.config.model, "text-out"),
                        output_tokens=r,
                    )
        except Exception as exc:
            self.cb.on_log(f"usage_metadata parse warning: {exc}")

    @staticmethod
    def _modality_to_kind(modality: str, direction: str) -> str:
        """Mapa modalidad Gemini -> kind del TokenTracker (audio-in, text-out, etc.)."""
        m = modality.lower()
        if "audio" in m:
            base = "audio"
        elif "image" in m or "vision" in m or "video" in m:
            base = "vision" if direction == "in" else "text"  # vision solo input
        else:
            base = "text"
        return f"{base}-{direction}"

    async def _handle_tool_call(self, tool_call) -> None:
        """Cuando Gemini emite function_call, dispatch local + send response.

        IMPORTANTE: el dispatcher.call() es sincrono y puede bloquear varios
        segundos (ej. primer jarvis_recall carga sentence-transformers).
        Lo corremos en un thread executor para NO bloquear el receive_loop,
        que tiene que seguir entregando audio de Gemini en tiempo real.

        Tools que necesitan adjuntar binarios (ej. screen_look con PNG) lo
        marcan con la clave privada `__attach_image` en el dict de respuesta.
        Aqui la extraemos y la enviamos como user-content separado tras el
        tool_response, porque el SDK no acepta bytes en FunctionResponse.parts.
        """
        import time as _time
        if not self.config.tool_dispatcher:
            self.cb.on_log("tool_call recibido pero sin dispatcher configurado")
            return
        function_responses = []
        pending_attachments: list[tuple[bytes, str, str]] = []  # (bytes, mime, source)
        dispatcher = self.config.tool_dispatcher
        for fc in getattr(tool_call, "function_calls", []) or []:
            name = fc.name
            args = dict(fc.args) if fc.args else {}
            timeout = self.config.tool_timeouts_s.get(name, self.config.tool_timeout_s)
            self.cb.on_log(f"tool_call: {name}({_safe_tool_args_for_log(args)}) [timeout={timeout:.0f}s]")
            try:
                _call_compatible(self.cb.on_tool_start, name, args)
            except Exception as exc:
                self.cb.on_log(f"on_tool_start callback fallo: {exc}")
            t0 = _time.perf_counter()
            ok = True
            try:
                # Tools async (ej. ask_claude_deep) corren directo en el loop
                # para que asyncio.wait_for cancele la request HTTP de verdad.
                # Tools sync van por asyncio.to_thread como fallback.
                if hasattr(dispatcher, "is_async") and dispatcher.is_async(name):
                    result = await asyncio.wait_for(
                        dispatcher.call_async(name, args),
                        timeout=timeout,
                    )
                else:
                    result = await asyncio.wait_for(
                        asyncio.to_thread(dispatcher.call, name, args),
                        timeout=timeout,
                    )
            except asyncio.TimeoutError:
                ok = False
                result = {
                    "ok": False,
                    "error": (
                        f"{name} tardo mas de {timeout:.0f}s y fue cancelado para "
                        "mantener la conversacion fluida. Decile a Isaac que la tarea "
                        "es muy grande para un turno de voz; sugiere dividirla en pasos "
                        "o guardarla como trabajo asincrono."
                    ),
                    "timeout_s": timeout,
                }
            except Exception as exc:
                ok = False
                result = {"error": f"{type(exc).__name__}: {exc}"}
            elapsed_ms = (_time.perf_counter() - t0) * 1000
            self.cb.on_log(f"tool_response: {name} -> {elapsed_ms:.0f}ms (ok={ok})")
            try:
                response_for_callback = getattr(result, "response", result)
                _call_compatible(self.cb.on_tool_end, name, elapsed_ms, ok, response_for_callback)
            except Exception as exc:
                self.cb.on_log(f"on_tool_end callback fallo: {exc}")
            response = getattr(result, "response", result)
            # Extraer adjunto binario (side-channel) antes de serializar a JSON
            if isinstance(response, dict) and "__attach_image" in response:
                attach = response.pop("__attach_image") or {}
                png_bytes = attach.get("png_bytes")
                mime_type = attach.get("mime_type", "image/png")
                source = attach.get("source", "tool")
                if isinstance(png_bytes, (bytes, bytearray)) and png_bytes:
                    pending_attachments.append((bytes(png_bytes), mime_type, source))
            function_responses.append(types.FunctionResponse(
                id=getattr(fc, "id", None),
                name=name,
                response=response,
            ))
        if function_responses and self._session is not None:
            try:
                await self._session.send_tool_response(
                    function_responses=function_responses
                )
            except Exception as exc:
                self.cb.on_error(exc)
                return
            # Tras el tool_response, enviar adjuntos como user-content separado
            # (ya valida via send_client_content que send_image usa).
            for png_bytes, mime_type, source in pending_attachments:
                try:
                    await self._async_send_image(
                        png_bytes,
                        mime_type,
                        prompt=visual_capture_prompt(source),
                    )
                except Exception as exc:
                    self.cb.on_error(exc)

    # ---- Public API (thread-safe wrappers) ----

    def _submit(self, coro) -> None:
        if not self._loop or not self._loop.is_running():
            try:
                coro.close()
            except Exception:
                pass
            if not self._submit_drop_warned:
                self._submit_drop_warned = True
                self.cb.on_log("[WARN] comando descartado: sesion asyncio aun no lista")
            return
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        fut.add_done_callback(self._consume_submit_result)

    def _consume_submit_result(self, fut) -> None:
        try:
            fut.result()
        except Exception as exc:
            self.cb.on_error(exc)

    def send_audio_chunk(self, pcm_bytes: bytes) -> None:
        """Envia un chunk de audio PCM 16kHz int16 al modelo."""
        if not pcm_bytes:
            return
        self._submit(self._async_send_audio(pcm_bytes))

    async def _async_send_audio(self, pcm_bytes: bytes) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm_bytes, mime_type=INPUT_MIME_PCM_16K)
            )
        except Exception as exc:
            self.cb.on_error(exc)

    def send_video_frame(self, jpeg_bytes: bytes) -> None:
        """Envia un frame de video (JPEG) por el canal realtime (modo vision)."""
        if not jpeg_bytes:
            return
        self._submit(self._async_send_video(jpeg_bytes))

    async def _async_send_video(self, jpeg_bytes: bytes) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_realtime_input(
                video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
            )
        except Exception as exc:
            self.cb.on_error(exc)

    def start_user_activity(self) -> None:
        """En modo manual (PTT), marca inicio del turno del usuario."""
        if not self.config.manual_activity_mode:
            return
        self._submit(self._async_activity_start())

    async def _async_activity_start(self) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_realtime_input(activity_start=types.ActivityStart())
        except Exception as exc:
            self.cb.on_error(exc)

    def end_user_activity(self) -> None:
        """En modo manual (PTT), marca fin del turno del usuario."""
        if not self.config.manual_activity_mode:
            return
        self._submit(self._async_activity_end())

    async def _async_activity_end(self) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_realtime_input(activity_end=types.ActivityEnd())
        except Exception as exc:
            self.cb.on_error(exc)

    def send_text(self, text: str) -> None:
        """Envia un mensaje de texto (util para debug)."""
        self._submit(self._async_send_text(text))

    async def _async_send_text(self, text: str) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_client_content(
                turns={"role": "user", "parts": [{"text": text}]},
                turn_complete=True,
            )
        except Exception as exc:
            self.cb.on_error(exc)

    def send_image(self, image_bytes: bytes, mime_type: str = "image/png", prompt: str = "") -> None:
        """Envia una imagen/screenshot y un prompt corto al modelo."""
        if not image_bytes:
            return
        self._submit(self._async_send_image(image_bytes, mime_type, prompt))

    async def _async_send_image(self, image_bytes: bytes, mime_type: str, prompt: str) -> None:
        if self._session is None:
            return
        try:
            parts = [types.Part.from_bytes(data=image_bytes, mime_type=mime_type)]
            if prompt:
                parts.append(types.Part(text=prompt))
            # send_client_content es la API actual del SDK; send(input=...) ya no
            # acepta listas de Parts (queda como version deprecada).
            await self._session.send_client_content(
                turns=types.Content(role="user", parts=parts),
                turn_complete=True,
            )
        except Exception as exc:
            self.cb.on_error(exc)
