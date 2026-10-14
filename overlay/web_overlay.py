"""Browser-backed premium overlay for Jarvis.

This module keeps the same public surface used by jarvis.py while rendering the
premium UI in overlay/web_ui. Runtime events are pushed to the browser through a
tiny local SSE bridge built only with the Python standard library.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import secrets
import struct
import threading
import time
import webbrowser
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from jarvis_version import JARVIS_VERSION_LABEL
from overlay.scheduler import UiScheduler
from telemetry.budgets import BudgetGate, ProviderBudget
from telemetry.tracker import TokenTracker

try:  # psutil es opcional; sin el, System Stats queda vacio (UI usa fallback)
    import psutil
except Exception:  # pragma: no cover - entorno sin psutil
    psutil = None  # type: ignore[assignment]

# Mapa de codigos WMO (Open-Meteo) a descripcion corta para el panel Weather.
_WMO_DESC = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast clouds",
    45: "fog", 48: "rime fog", 51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "violent showers",
    85: "snow showers", 86: "snow showers", 95: "thunderstorm",
    96: "thunderstorm w/ hail", 99: "thunderstorm w/ hail",
}


@dataclass
class ApprovalAction:
    """Accion pendiente de aprobacion HITL en modo web."""
    id: str
    tool: str
    args: dict[str, Any]
    risk: str = "medium"
    timeout_s: float = 30.0
    title: str = ""
    details: str = ""

    def __post_init__(self) -> None:
        if not self.title:
            self.title = self.tool
        if not self.details:
            self.details = json.dumps(self.args, ensure_ascii=False)[:200]

_WEB_DIST = Path(__file__).resolve().parent / "web_dist"
_WEB_FALLBACK = Path(__file__).resolve().parent / "web_ui"
_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COOKIE_NAME = "jarvis_ui"
REFRESH_MS = 500
DEFAULT_AUDIO_VISUAL_FPS = 30
USAGE_RATE_WINDOW_S = 30.0
CAMERA_RATE_WINDOW_S = 5.0
MAX_JSON_REQUEST_BYTES = 1_000_000
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _env_truthy(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _format_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _audio_emit_interval_from_env() -> float:
    try:
        fps = int(os.environ.get("JARVIS_WEB_UI_AUDIO_FPS", str(DEFAULT_AUDIO_VISUAL_FPS)))
    except ValueError:
        fps = DEFAULT_AUDIO_VISUAL_FPS
    return 1.0 / max(1, fps)


def _camera_emit_interval_from_env() -> float:
    raw = os.environ.get("JARVIS_CAMERA_PREVIEW_FPS", "").strip()
    if not raw:
        raw = os.environ.get("JARVIS_CAMERA_FPS", "4.0").strip()
    try:
        fps = float(raw)
    except ValueError:
        fps = 4.0
    fps = max(0.1, min(fps, 15.0))
    return 1.0 / fps


def resolve_web_dir() -> Path:
    override = os.environ.get("JARVIS_WEB_UI_DIR", "").strip()
    if override:
        return Path(override)
    if (_WEB_DIST / "index.html").is_file():
        return _WEB_DIST
    return _WEB_FALLBACK


def _provider_payload(pb: ProviderBudget, tokens: int) -> dict[str, Any]:
    return {
        "provider": pb.provider,
        "spentUsd": pb.spent_usd,
        "limitUsd": pb.limit_usd,
        "pct": max(0.0, min(pb.pct, 1.0)),
        "status": pb.status.value,
        "blocked": pb.blocked,
        "tokens": tokens,
        "tokensLabel": _format_tokens(tokens),
        "label": f"{_format_tokens(tokens)} ${pb.spent_usd:.3f}/${pb.limit_usd:.2f}",
    }


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], overlay: "WebJarvisOverlay") -> None:
        self.overlay = overlay
        super().__init__(address, _BridgeHandler)


class _BridgeHandler(BaseHTTPRequestHandler):
    server: _BridgeServer

    def log_message(self, fmt: str, *args: Any) -> None:
        if _env_truthy("JARVIS_WEB_UI_HTTP_LOG", False):
            super().log_message(fmt, *args)

    def _host_allowed(self) -> bool:
        """Rechaza DNS rebinding contra el bridge local.

        Sin esto, una web maliciosa puede apuntar su dominio a 127.0.0.1, quedar
        same-origin con el bridge y leer /state (que expone uiToken, transcripts
        y frames de camara) para despues postear /command con ese token. El
        chequeo de token no alcanza porque el propio /state lo entrega.
        """
        try:
            hostname = urlsplit(f"//{self.headers.get('Host') or ''}").hostname
        except ValueError:
            return False
        if not hostname:
            return False
        bound = str(self.server.server_address[0]).lower()
        return hostname.lower() in (LOOPBACK_HOSTNAMES | {bound})

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._host_allowed():
            self.send_error(HTTPStatus.FORBIDDEN, "bad host")
            return
        parsed = urlparse(self.path)
        query_token = ""
        if parsed.query:
            query_token = (parse_qs(parsed.query).get("access_token") or [""])[0]
        if parsed.path in {"", "/"} and query_token:
            if not self._token_matches(query_token):
                self.send_error(HTTPStatus.FORBIDDEN, "bad token")
                return
            # Servir el HTML en la misma request (Set-Cookie). Un 302 deja a
            # WebView2 de Tauri sin cookie y /events responde 403: UI muda.
            self._serve_static("/", set_cookie=True)
            return
        if parsed.path == "/health":
            self._send_json({"ok": True, "version": JARVIS_VERSION_LABEL})
            return
        if parsed.path in {"/state", "/events"}:
            if not self._authorized({"uiToken": query_token} if query_token else {}):
                self._send_json({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
        if parsed.path == "/state":
            self._send_json(self.server.overlay.snapshot())
            return
        if parsed.path == "/events":
            self._stream_events()
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._host_allowed():
            self.send_error(HTTPStatus.FORBIDDEN, "bad host")
            return
        if not self._origin_allowed():
            self.send_error(HTTPStatus.FORBIDDEN, "bad origin")
            return
        parsed = urlparse(self.path)
        body = self._read_json()
        if parsed.path == "/approval":
            if not self._authorized(body):
                self._send_json({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            action_id = str(body.get("id", ""))
            approved = bool(body.get("approved", False))
            ok = self.server.overlay.resolve_web_approval(action_id, approved)
            self._send_json({"ok": ok})
            return
        if parsed.path == "/command":
            if not self._authorized(body):
                self._send_json({"ok": False, "error": "forbidden"}, HTTPStatus.FORBIDDEN)
                return
            command = str(body.get("command", ""))
            raw_args = body.get("args", {})
            args = raw_args if isinstance(raw_args, dict) else {}
            ok = self.server.overlay.handle_web_command(command, args)
            self._send_json({"ok": ok})
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > MAX_JSON_REQUEST_BYTES:
            return {}
        raw = self.rfile.read(length)
        if len(raw) != length:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _token_from_cookie(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return ""

    def _token_matches(self, token: str) -> bool:
        expected = self.server.overlay.ui_token
        if not token or not expected:
            return False
        try:
            return secrets.compare_digest(token, expected)
        except (TypeError, ValueError):
            return False

    def _authorized(self, body: dict[str, Any]) -> bool:
        token = (
            self.headers.get("X-Jarvis-Ui-Token", "")
            or str(body.get("uiToken", "") or "")
            or self._token_from_cookie()
        )
        return self._token_matches(token)

    def _origin_allowed(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            hostname = urlsplit(origin).hostname
        except ValueError:
            return False
        if not hostname:
            return False
        bound = str(self.server.server_address[0]).lower()
        return hostname.lower() in (LOOPBACK_HOSTNAMES | {bound})

    def _redirect_with_cookie(self) -> None:
        token = self.server.overlay.ui_token
        self.send_response(int(HTTPStatus.FOUND))
        self.send_header("Location", "/")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str, set_cookie: bool = False) -> None:
        rel = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        web_dir = resolve_web_dir()
        target = (web_dir / rel).resolve()
        try:
            target.relative_to(web_dir.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        if set_cookie:
            token = self.server.overlay.ui_token
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400",
            )
        self.end_headers()
        self.wfile.write(data)

    def _stream_events(self) -> None:
        client = self.server.overlay.register_client()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self._write_sse({"command": "snapshot", "args": [self.server.overlay.snapshot()]})
            while not self.server.overlay.closed:
                try:
                    payload = client.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.overlay.unregister_client(client)

    def _write_sse(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


class WebJarvisOverlay:
    """Premium browser UI that mimics JarvisOverlay for the orchestrator."""

    def __init__(
        self,
        tracker: TokenTracker,
        gate: BudgetGate,
        on_close: Callable[[], None] | None = None,
        on_command: Callable[[str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.tracker = tracker
        self.gate = gate
        self._on_close = on_close or (lambda: None)
        # Dispatcher de comandos de UI que no resuelve el propio overlay
        # (toggleMode, toggleCamera, sendText...). Lo provee jarvis.py y enruta
        # a sus callbacks/sesion. Devuelve True si acepto el comando.
        self._on_command = on_command or (lambda _cmd, _args: False)
        self._state = "idle"
        self._mode = "PTT"
        self._connection_status = "connecting"
        self._connection_detail = ""
        self._input_text = ""
        self._output_text = ""
        self._conversation_messages: list[dict[str, Any]] = []
        self._conversation_seq = 0
        self._events: list[str] = []
        self._event_history: list[tuple[str, str, str]] = []
        self._memory_events: list[dict[str, Any]] = []
        self._agent_events: list[dict[str, Any]] = []
        self._missions: dict[str, dict[str, Any]] = {}
        self._mission_events: list[dict[str, Any]] = []
        self._mission_detail: dict[str, Any] | None = None
        self._audio_telemetry: dict[str, Any] = {}
        self._latency_lines: list[str] = []
        self._latency_summary: dict[str, Any] = {}
        self._camera_active = False
        self._camera_frame_b64: str | None = None
        self._camera_focus: dict[str, Any] | None = None
        self._memory_active: dict[str, dict[str, Any]] = {}
        self._pending_approvals: dict[str, Callable[[str, bool], None]] = {}
        self.ui_token = (os.environ.get("JARVIS_UI_TOKEN") or "").strip() or secrets.token_urlsafe(32)
        self._already_blocked: set[str] = set()
        self._clients: set[queue.Queue[str]] = set()
        self._clients_lock = threading.Lock()
        self._last_audio_emit_ts = 0.0
        self._last_camera_emit_ts = 0.0
        self._tool_event_seq = 0
        self._audio_emit_interval_s = _audio_emit_interval_from_env()
        self._camera_emit_interval_s = _camera_emit_interval_from_env()
        self._usage_rate_samples: list[tuple[float, dict[str, int], dict[str, float]]] = []
        self._camera_stream_samples: list[tuple[float, int]] = []
        self._camera_preview_samples: list[tuple[float, int]] = []
        self._camera_stream_total_bytes = 0
        self._camera_preview_total_bytes = 0
        self._camera_stream_total_frames = 0
        self._camera_preview_total_frames = 0
        self._closed = False
        self._close_event = threading.Event()
        self._log_path = Path(__file__).resolve().parent.parent / "data" / "jarvis.log"
        self._scheduler = UiScheduler(name="JarvisWebScheduler")

        # Weather: ultimo payload cacheado + evento para forzar re-fetch (boton
        # Refresh). Un hilo daemon lo refresca cada ~15 min sin bloquear el
        # scheduler de UI (la llamada de red tiene timeout corto y fallback).
        self._weather: dict[str, Any] = {}
        self._weather_refresh = threading.Event()
        self._weather_thread: threading.Thread | None = None

        # Watchdog supervisado (shell Tauri): si la ventana desaparece sin avisar
        # >grace, apagarse para no dejar un backend zombi con el microfono abierto.
        self._supervised = _env_truthy("JARVIS_SUPERVISED", False)
        self._had_client = False
        self._last_client_seen = time.monotonic()
        self._watchdog_grace_s = 60.0

        self._server = self._start_server()
        host, port = self._server.server_address[0], self._server.server_address[1]
        self.url = f"http://{host}:{port}/"
        self.bootstrap_url = f"{self.url}?access_token={self.ui_token}"
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="jarvis-web-ui",
            daemon=True,
        )
        self._server_thread.start()

        self.log_event("Web UI lista", "ok")
        self.after(REFRESH_MS, self._refresh_runtime_panels)
        if _env_truthy("JARVIS_WEATHER", True):
            self._weather_thread = threading.Thread(
                target=self._weather_loop, name="jarvis-weather", daemon=True
            )
            self._weather_thread.start()
        if self._supervised:
            self.after(10_000, self._watchdog_check)
        if _env_truthy("JARVIS_WEB_UI_OPEN_BROWSER", True) and not self._supervised:
            self.after(250, lambda: webbrowser.open(self.bootstrap_url))

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def state(self) -> str:
        return self._state

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def event_history(self) -> list[tuple[str, str, str]]:
        return self._event_history

    @property
    def memory_events(self) -> list[dict[str, Any]]:
        return self._memory_events

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        return self._agent_events

    @property
    def log_path(self) -> Path:
        return self._log_path

    def privacy_label_text(self) -> str:
        if _env_truthy("JARVIS_HIDE_FROM_CAPTURE", False):
            return "Browser visible"
        return "Captura visible"

    def after(self, delay_ms: int | float, fn: Callable[[], None]) -> int:
        """Interfaz comun con JarvisOverlay.after(). Usa UiScheduler (headless)."""
        return self._scheduler.after(delay_ms, fn)

    def _watchdog_check(self) -> None:
        """En modo supervisado (Tauri), si la ventana desaparece > grace, apagarse
        para no dejar un backend zombi con el microfono abierto. Re-agenda cada 10s."""
        if self._closed or not self._supervised:
            return
        with self._clients_lock:
            n = len(self._clients)
        if n > 0:
            self._last_client_seen = time.monotonic()
        elif self._had_client and (time.monotonic() - self._last_client_seen) > self._watchdog_grace_s:
            print(f"[web-ui] supervisado y sin clientes {int(self._watchdog_grace_s)}s; cerrando JARVIS")
            self.close()
            return
        self.after(10_000, self._watchdog_check)

    def register_client(self) -> queue.Queue[str]:
        client: queue.Queue[str] = queue.Queue(maxsize=256)
        with self._clients_lock:
            self._clients.add(client)
        self._had_client = True
        self._last_client_seen = time.monotonic()
        return client

    def unregister_client(self, client: queue.Queue[str]) -> None:
        with self._clients_lock:
            self._clients.discard(client)

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": JARVIS_VERSION_LABEL,
            "state": self._state,
            "mode": self._mode,
            "connection": {
                "status": self._connection_status,
                "detail": self._connection_detail,
            },
            "privacy": self.privacy_label_text(),
            "inputTranscript": self._input_text[-12000:],
            "outputTranscript": self._output_text[-16000:],
            "conversationMessages": self._conversation_messages[-80:],
            "events": [
                {"stamp": stamp, "level": level, "message": message}
                for stamp, level, message in self._event_history[-8:]
            ],
            "memory": self._memory_stats(),
            "agentEvents": self._agent_events[-30:],
            "missions": list(self._missions.values())[-20:],
            "missionEvents": self._mission_events[-100:],
            "missionDetail": self._mission_detail,
            "audioTelemetry": self._audio_telemetry,
            "latency": self._latency_lines,
            "latencySummary": self._latency_summary,
            "cameraActive": self._camera_active,
            "cameraFrame": self._camera_frame_b64,
            "cameraFocus": self._camera_focus,
            "budget": self._budget_payload(),
            "runtimeTelemetry": self._runtime_telemetry_payload(),
            "systemStats": self._system_stats(),
            "weather": self._weather,
            "errorJournal": self._error_journal_tail(),
        }

    def _error_journal_tail(self) -> list[dict[str, str]]:
        try:
            from telemetry.error_journal import tail_errors
            return tail_errors(limit=20)
        except Exception:
            return []

    def emit(self, command: str, *args: Any) -> None:
        payload = json.dumps(
            {"command": command, "args": list(args)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.put_nowait(payload)
            except queue.Full:
                try:
                    client.get_nowait()
                except queue.Empty:
                    pass
                try:
                    client.put_nowait(payload)
                except queue.Full:
                    pass

    def set_state(self, state: str) -> None:
        if state not in {"idle", "listening", "thinking", "speaking", "blocked"}:
            return
        self._state = state
        self.emit("setState", state)

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self.emit("setMode", mode)
        self.log_event(f"Modo {'libre' if mode == 'LIBRE' else 'PTT'}")

    def set_connection_status(self, status: str, detail: str = "") -> None:
        self._connection_status = status
        self._connection_detail = detail
        self.emit("setConnectionStatus", status, detail)
        if status in {"connected", "reconnecting", "error", "stopped"}:
            label = {
                "connected": "Gemini listo",
                "reconnecting": "Gemini reconectando",
                "error": "Gemini error",
                "stopped": "Gemini detenido",
            }.get(status, f"Gemini {status}")
            self.log_event(f"{label}{': ' + detail if detail else ''}", "error" if status == "error" else "info")

    def log_event(self, message: str, level: str = "info") -> None:
        message = " ".join((message or "").split())
        if not message:
            return
        stamp = time.strftime("%H:%M")
        self._event_history.append((stamp, level, message))
        self._event_history = self._event_history[-200:]
        self._events.append(f"{stamp} {message}")
        self._events = self._events[-3:]
        self.emit("logEvent", message, level)

    def append_input(self, text: str) -> None:
        if not text:
            return
        self._input_text = f"{self._input_text} {text}".strip()[-12000:]
        self._append_conversation_message("user", text, separator=" ")
        self.emit("appendInput", text)

    def append_output(self, text: str) -> None:
        if not text:
            return
        self._output_text = f"{self._output_text}{text}"[-16000:]
        self._append_conversation_message("jarvis", text, separator="")
        self.emit("appendOutput", text)

    def feed_voice_audio(self, pcm_bytes: bytes) -> None:
        now = time.monotonic()
        if now - self._last_audio_emit_ts < self._audio_emit_interval_s:
            return
        self._last_audio_emit_ts = now
        self.emit("feedAudioLevel", self.audio_level_from_pcm(pcm_bytes))

    def reset_transcripts(self) -> None:
        self._input_text = ""
        self._output_text = ""
        self._conversation_messages = []
        self.emit("clearTranscripts")
        self.log_event("Transcript reiniciado")

    def export_conversation(self) -> Path | None:
        text = self._conversation_markdown()
        if not text.strip():
            self.log_event("No hay conversacion para extraer", "warn")
            self.emit("conversationExtracted", {"ok": False, "error": "empty"})
            return None
        raw_dir = os.environ.get("JARVIS_CONVERSATION_EXPORT_DIR", "").strip()
        out_dir = Path(raw_dir).expanduser() if raw_dir else _ROOT / "data" / "conversations"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"jarvis-conversation-{time.strftime('%Y%m%d-%H%M%S')}.md"
        path.write_text(text, encoding="utf-8")
        self.log_event(f"Conversacion extraida: {path.name}", "ok")
        self.emit("conversationExtracted", {"ok": True, "path": str(path)})
        return path

    def _conversation_markdown(self) -> str:
        messages = list(self._conversation_messages)
        fallback = []
        if self._input_text.strip():
            fallback.append({"role": "user", "text": self._input_text.strip(), "stamp": ""})
        if self._output_text.strip():
            fallback.append({"role": "jarvis", "text": self._output_text.strip(), "stamp": ""})
        messages = messages or fallback
        if not messages:
            return ""

        lines = [
            "# Conversacion JARVIS",
            "",
            f"Extraida: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Mensajes: {len(messages)}",
            "",
        ]
        for message in messages:
            role = "Isaac" if message.get("role") == "user" else "JARVIS"
            stamp = str(message.get("stamp") or "").strip()
            heading = f"## {role}" + (f" - {stamp}" if stamp else "")
            body = str(message.get("text") or "").strip()
            lines.extend([heading, "", body, ""])
        return "\n".join(lines).strip() + "\n"

    def _append_conversation_message(self, role: str, text: str, *, separator: str) -> None:
        text = text or ""
        if not text:
            return
        if self._conversation_messages and self._conversation_messages[-1].get("role") == role:
            current = str(self._conversation_messages[-1].get("text", ""))
            if separator and current and text:
                updated = f"{current}{separator}{text}".strip()
            else:
                updated = f"{current}{text}"
            self._conversation_messages[-1]["text"] = updated[-16000:]
            return
        self._conversation_seq += 1
        self._conversation_messages.append({
            "id": f"msg-{self._conversation_seq}",
            "role": role,
            "text": text[-16000:],
            "stamp": time.strftime("%H:%M"),
        })
        self._conversation_messages = self._conversation_messages[-80:]

    def record_tool_start(self, name: str, args: dict[str, Any] | None = None) -> None:
        """Registra cualquier tool para el thought log web.

        Las tools de memoria tambien alimentan el panel historico existente.
        """
        args = args or {}
        self._tool_event_seq += 1
        now_ms = int(time.time() * 1000)
        entry = {
            "id": f"tool-{self._tool_event_seq}",
            "stamp": time.strftime("%H:%M:%S"),
            "name": name,
            "summary": self._tool_args_summary(name, args),
            "status": "running",
            "detail": "En progreso",
            "elapsedMs": None,
            "startedAt": now_ms,
            "endedAt": None,
        }
        self._agent_events.append(entry)
        self._agent_events = self._agent_events[-150:]
        self.emit("agentToolStart", entry)
        self.record_memory_tool_start(name, args)

    def record_tool_end(
        self,
        name: str,
        elapsed_ms: float,
        ok: bool,
        response: Any = None,
    ) -> None:
        status = "ok" if ok and not self._response_failed(response) else "error"
        now_ms = int(time.time() * 1000)
        entry = {
            "id": "",
            "stamp": time.strftime("%H:%M:%S"),
            "name": name,
            "summary": "",
            "status": status,
            "detail": self._memory_response_summary(name, response),
            "elapsedMs": elapsed_ms,
            "startedAt": now_ms,
            "endedAt": now_ms,
        }
        for idx in range(len(self._agent_events) - 1, -1, -1):
            existing = self._agent_events[idx]
            if existing.get("name") == name and existing.get("status") == "running":
                entry["id"] = str(existing.get("id", ""))
                entry["summary"] = str(existing.get("summary", ""))
                entry["startedAt"] = existing.get("startedAt", now_ms)
                self._agent_events[idx] = entry
                break
        else:
            self._tool_event_seq += 1
            entry["id"] = f"tool-{self._tool_event_seq}"
            self._agent_events.append(entry)
        self._agent_events = self._agent_events[-150:]
        self.emit("agentToolEnd", entry)
        self.record_memory_tool_end(name, elapsed_ms, ok, response)

    def set_missions(self, missions: list[dict[str, Any]]) -> None:
        """Carga summaries persistidos al conectar el runtime, sin artefactos crudos."""
        ordered = sorted(
            [item for item in (missions or []) if isinstance(item, dict) and item.get("id")],
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        )[-20:]
        self._missions = {
            str(item.get("id")): dict(item)
            for item in ordered
        }

    def record_mission_event(self, event: dict[str, Any]) -> None:
        """Actualiza timeline SSE después de que el runtime persistió el evento."""
        if not isinstance(event, dict):
            return
        mission = event.get("mission")
        if isinstance(mission, dict) and mission.get("id"):
            mission = dict(mission)
            self._missions[str(mission["id"])] = mission
            # Mantener únicamente las 20 más recientes por updated_at.
            ordered = sorted(
                self._missions.values(),
                key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            )[-20:]
            self._missions = {str(item["id"]): item for item in ordered}
            self.emit("missionUpdated", mission)
        clean_event = {
            "id": event.get("id"),
            "mission_id": event.get("mission_id"),
            "step_id": event.get("step_id"),
            "event_type": event.get("event_type"),
            "level": event.get("level", "info"),
            "payload": event.get("payload") if isinstance(event.get("payload"), dict) else {},
            "created_at": event.get("created_at"),
        }
        self._mission_events.append(clean_event)
        self._mission_events = self._mission_events[-100:]
        self.emit("missionEvent", clean_event)

    def set_mission_detail(self, detail: dict[str, Any]) -> None:
        """Entrega al Command Center un detalle acotado, sin contenido de artefactos."""
        if not isinstance(detail, dict):
            return
        mission = detail.get("mission")
        steps = detail.get("steps")
        events = detail.get("events")
        safe = {
            "ok": bool(detail.get("ok")),
            "error_code": detail.get("error_code"),
            "error": str(detail.get("error") or "")[:500],
            "mission": dict(mission) if isinstance(mission, dict) else None,
            "steps": [dict(item) for item in (steps or [])[:8] if isinstance(item, dict)],
            "events": [dict(item) for item in (events or [])[-50:] if isinstance(item, dict)],
        }
        # El resumen final es evidencia ya redactada; aun asi lo acotamos para SSE/UI.
        if safe["mission"] and isinstance(safe["mission"].get("final_summary"), str):
            summary = safe["mission"]["final_summary"]
            safe["mission"]["final_summary"] = (
                summary[:3999] + "…" if len(summary) > 4000 else summary
            )
        self._mission_detail = safe
        self.emit("missionDetail", safe)

    def record_audio_telemetry(self, payload: dict[str, Any]) -> None:
        self._audio_telemetry = {**payload, "stamp": time.strftime("%H:%M:%S")}
        self.emit("audioTelemetry", self._audio_telemetry)

    def record_turn_latency(self, line: str) -> None:
        line = " ".join((line or "").split())
        if not line:
            return
        self._latency_lines.append(line)
        self._latency_lines = self._latency_lines[-20:]
        self.emit("turnLatency", line)

    def record_latency_summary(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        self._latency_summary = dict(payload)
        self.emit("latencySummary", self._latency_summary)

    def record_memory_tool_start(self, name: str, args: dict[str, Any] | None = None) -> None:
        if not self._is_memory_tool(name):
            return
        args = args or {}
        stamp = time.strftime("%H:%M:%S")
        summary = self._memory_args_summary(name, args)
        self._memory_active[name] = {
            "stamp": stamp,
            "name": name,
            "summary": summary,
        }
        self._memory_events.append({
            "stamp": stamp,
            "name": name,
            "summary": summary,
            "status": "running",
            "detail": "En progreso",
            "elapsed_ms": None,
        })
        self._memory_events = self._memory_events[-120:]
        self.log_event(f"Memoria: {summary}", "info")
        self.emit("updateMemoryStats", self._memory_stats())

    def record_memory_tool_end(
        self,
        name: str,
        elapsed_ms: float,
        ok: bool,
        response: Any = None,
    ) -> None:
        if not self._is_memory_tool(name):
            return
        active = self._memory_active.pop(name, {})
        summary = active.get("summary") or self._memory_args_summary(name, {})
        detail = self._memory_response_summary(name, response)
        status = "ok" if ok and not self._response_failed(response) else "error"
        event = {
            "stamp": time.strftime("%H:%M:%S"),
            "name": name,
            "summary": summary,
            "status": status,
            "detail": detail,
            "elapsed_ms": elapsed_ms,
        }
        for idx in range(len(self._memory_events) - 1, -1, -1):
            existing = self._memory_events[idx]
            if existing.get("name") == name and existing.get("status") == "running":
                self._memory_events[idx] = event
                break
        else:
            self._memory_events.append(event)
        self._memory_events = self._memory_events[-120:]
        self.log_event(f"Memoria: {detail}", "ok" if status == "ok" else "error")
        self.emit("updateMemoryStats", self._memory_stats())

    def show_approval(self, action: Any, on_decision: Callable[[str, bool], None]) -> None:
        self._pending_approvals[action.id] = on_decision
        self.log_event(f"Aprobacion pendiente: {action.title}", "warn")
        self.emit(
            "showApproval",
            {
                "id": action.id,
                "risk": action.risk,
                "title": action.title,
                "details": action.details,
                "timeout_s": action.timeout_s,
            },
        )
        self.after(int(action.timeout_s * 1000), lambda: self.resolve_web_approval(action.id, False))

    def resolve_web_approval(self, action_id: str, approved: bool) -> bool:
        callback = self._pending_approvals.pop(action_id, None)
        if callback is None:
            return False
        self.log_event(
            "Accion aprobada" if approved else "Accion rechazada",
            "ok" if approved else "warn",
        )
        try:
            callback(action_id, approved)
        finally:
            self.emit("hideApproval", bool(approved))
        return True

    def handle_web_command(self, command: str, args: dict[str, Any] | None = None) -> bool:
        args = args or {}
        if command == "close":
            self.after(0, self.close)
            return True
        if command == "clearTranscripts":
            self.after(0, self.reset_transcripts)
            return True
        if command == "extractConversation":
            self.after(0, self.export_conversation)
            return True
        if command == "openDashboard":
            self.after(0, self.open_dashboard)
            return True
        if command == "refreshStats":
            stats = self._system_stats()
            if stats:
                self.emit("systemStats", stats)
            return True
        if command == "refreshWeather":
            # Despierta al hilo de weather para re-fetch inmediato.
            self._weather_refresh.set()
            return True
        # Comandos enrutados a jarvis.py (toggleMode, toggleCamera, sendText...).
        # El dispatcher de jarvis se encarga de marshallar a su propio hilo de
        # UI/sesion; aqui solo lo invocamos y devolvemos si lo reconocio.
        try:
            return bool(self._on_command(command, args))
        except Exception as exc:  # pragma: no cover - defensivo
            self.log_event(f"comando UI '{command}' fallo: {exc}", "error")
            return False

    # ---- Camera preview (SSE headless) ----

    def set_camera_active(self, active: bool) -> None:
        """Indica al frontend si la camara esta ON/OFF via SSE."""
        self._camera_active = bool(active)
        if not self._camera_active:
            self._camera_frame_b64 = None
            self._camera_focus = None
        level = "warn" if active else "ok"
        msg = "CAMARA ACTIVA (modo vision)" if active else "Camara apagada"
        self.log_event(msg, level)
        self.emit("setCameraActive", self._camera_active)

    def update_camera_preview(self, frame) -> None:
        """Envia frame JPEG como base64 por SSE con throttle configurable."""
        jpeg_bytes = frame.jpeg_bytes
        self._record_camera_stream_frame(len(jpeg_bytes))
        now = time.monotonic()
        if now - self._last_camera_emit_ts < self._camera_emit_interval_s:
            return
        self._last_camera_emit_ts = now
        self._record_camera_preview_frame(len(jpeg_bytes))
        self._camera_frame_b64 = base64.b64encode(jpeg_bytes).decode("ascii")
        if not self._camera_active:
            self._camera_active = True
            self.emit("setCameraActive", True)
        self.emit("cameraFrame", self._camera_frame_b64)

    def _record_camera_stream_frame(self, size_bytes: int) -> None:
        now = time.monotonic()
        self._camera_stream_samples.append((now, max(0, int(size_bytes))))
        self._camera_stream_samples = [
            sample for sample in self._camera_stream_samples
            if now - sample[0] <= CAMERA_RATE_WINDOW_S
        ]
        self._camera_stream_total_bytes += max(0, int(size_bytes))
        self._camera_stream_total_frames += 1

    def _record_camera_preview_frame(self, size_bytes: int) -> None:
        now = time.monotonic()
        # SSE envia base64: ~33% mas bytes que el JPEG original.
        wire_bytes = int(max(0, size_bytes) * 4 / 3)
        self._camera_preview_samples.append((now, wire_bytes))
        self._camera_preview_samples = [
            sample for sample in self._camera_preview_samples
            if now - sample[0] <= CAMERA_RATE_WINDOW_S
        ]
        self._camera_preview_total_bytes += wire_bytes
        self._camera_preview_total_frames += 1

    def _bandwidth_payload(self, samples: list[tuple[float, int]], now: float) -> dict[str, float]:
        samples[:] = [sample for sample in samples if now - sample[0] <= CAMERA_RATE_WINDOW_S]
        if not samples:
            return {"fps": 0.0, "kbps": 0.0}
        oldest = samples[0][0]
        elapsed = max(0.001, min(CAMERA_RATE_WINDOW_S, now - oldest))
        return {
            "fps": round(len(samples) / elapsed, 1),
            "kbps": round(sum(size for _ts, size in samples) / elapsed / 1024, 1),
        }

    def set_camera_focus(self, box_px: Any, label: str = "") -> None:
        """Envia bounding box de foco al frontend por SSE."""
        self._camera_focus = {"box": box_px, "label": label}
        self.emit("cameraFocus", self._camera_focus)

    def camera_look(self) -> None:
        """Indica al frontend que se esta realizando una captura de camara."""
        self.emit("setCameraActive", True)

    def camera_watch_start(self) -> None:
        """Indica al frontend que el modo watch esta activo."""
        self.emit("setCameraActive", True)

    def camera_watch_stop(self) -> None:
        """Indica al frontend que el modo watch se detuvo."""
        self.emit("setCameraActive", False)

    def toggle_compact(self) -> None:
        self.emit("toggleCompact")
        self.log_event("Modo compacto alternado")

    def open_dashboard(self) -> None:
        self.log_event("Command Center web enfocado")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_event.set()
        self._weather_refresh.set()  # desbloquea el hilo de weather para que salga
        try:
            self._on_close()
        finally:
            try:
                self._scheduler.shutdown(timeout_s=1.0)
            except Exception:
                pass
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass

    def run(self) -> None:
        print(f"[web-ui] JARVIS disponible en {self.url}")
        self._close_event.wait()

    def _start_server(self) -> _BridgeServer:
        host = os.environ.get("JARVIS_WEB_UI_HOST", DEFAULT_HOST)
        preferred = int(os.environ.get("JARVIS_WEB_UI_PORT", str(DEFAULT_PORT)))
        candidates = [0] if preferred == 0 else list(range(preferred, preferred + 20))
        last_error: OSError | None = None
        for port in candidates:
            try:
                return _BridgeServer((host, port), self)
            except OSError as exc:
                last_error = exc
        raise RuntimeError(f"No pude iniciar Web UI local desde puerto {preferred}") from last_error

    def _refresh_runtime_panels(self) -> None:
        if self._closed:
            return
        try:
            self.emit("updateBudget", self._budget_payload())
            self.emit("runtimeTelemetry", self._runtime_telemetry_payload())
            self.emit("updateMemoryStats", self._memory_stats())
            self.emit("errorJournal", self._error_journal_tail())
            stats = self._system_stats()
            if stats:
                self.emit("systemStats", stats)
            self._notify_blocked_budgets()
        finally:
            try:
                self.after(REFRESH_MS, self._refresh_runtime_panels)
            except Exception:
                pass

    def _system_stats(self) -> dict[str, Any]:
        """CPU/RAM/Disk reales via psutil. Vacio si psutil no esta disponible."""
        if psutil is None:
            return {}
        try:
            cpu = psutil.cpu_percent(interval=None)
            vm = psutil.virtual_memory()
            disk = psutil.disk_usage(Path.home().anchor or os.sep)
            return {
                "cpu": round(cpu),
                "ram": round(vm.percent),
                "ramUsedGb": round(vm.used / 1e9, 1),
                "ramTotalGb": round(vm.total / 1e9, 1),
                "diskUsedGb": round(disk.used / 1e9),
                "diskTotalGb": round(disk.total / 1e9),
                "diskPct": round(disk.percent),
            }
        except Exception:
            return {}

    # ---- Weather (Open-Meteo + geolocalizacion por IP, sin API key) ----

    def _weather_loop(self) -> None:
        while not self._close_event.is_set():
            payload = self._fetch_weather()
            if payload:
                self._weather = payload
                self.emit("weather", payload)
            # Espera ~15 min, o hasta que el boton Refresh dispare el evento.
            self._weather_refresh.wait(timeout=900)
            self._weather_refresh.clear()

    def _fetch_weather(self) -> dict[str, Any] | None:
        import urllib.request

        def _get_json(url: str, timeout: float):
            req = urllib.request.Request(url, headers={"User-Agent": "JARVIS/1.1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        try:
            lat = os.environ.get("JARVIS_WEATHER_LAT")
            lon = os.environ.get("JARVIS_WEATHER_LON")
            place = os.environ.get("JARVIS_WEATHER_PLACE", "")
            if not lat or not lon:
                loc = _get_json("http://ip-api.com/json/?fields=lat,lon,city,country", 4.0)
                lat, lon = loc.get("lat"), loc.get("lon")
                if lat is None or lon is None:
                    return None
                place = ", ".join(p for p in (loc.get("city"), loc.get("country")) if p)
            data = _get_json(
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
                "wind_speed_10m,weather_code",
                5.0,
            )
            cur = data.get("current", {})
            code = int(cur.get("weather_code", 0) or 0)
            return {
                "tempC": round(float(cur.get("temperature_2m", 0)), 1),
                "feelsC": round(float(cur.get("apparent_temperature", 0)), 1),
                "humidity": round(float(cur.get("relative_humidity_2m", 0))),
                "windMs": round(float(cur.get("wind_speed_10m", 0)) / 3.6, 1),
                "place": place or "Unknown",
                "desc": _WMO_DESC.get(code, "—"),
                "code": code,
            }
        except Exception:
            return None

    def _runtime_telemetry_payload(self) -> dict[str, Any]:
        now = time.monotonic()
        tokens = self.tracker.tokens_by_provider()
        costs = self.tracker.cost_by_provider()
        self._usage_rate_samples.append((now, dict(tokens), dict(costs)))
        self._usage_rate_samples = [
            sample for sample in self._usage_rate_samples
            if now - sample[0] <= USAGE_RATE_WINDOW_S
        ]
        oldest_ts, oldest_tokens, oldest_costs = self._usage_rate_samples[0]
        elapsed = max(0.001, now - oldest_ts)

        def provider(name: str) -> dict[str, Any]:
            token_count = int(tokens.get(name, 0))
            cost = float(costs.get(name, 0.0))
            token_rate = max(0.0, (token_count - int(oldest_tokens.get(name, 0))) * 60.0 / elapsed)
            cost_rate = max(0.0, (cost - float(oldest_costs.get(name, 0.0))) * 60.0 / elapsed)
            return {
                "tokens": token_count,
                "tokensLabel": _format_tokens(token_count),
                "costUsd": round(cost, 6),
                "tokensPerMin": round(token_rate, 1),
                "usdPerMin": round(cost_rate, 6),
            }

        stream = self._bandwidth_payload(self._camera_stream_samples, now)
        preview = self._bandwidth_payload(self._camera_preview_samples, now)
        names = ("gemini", "grok", "other")
        total_tokens = sum(int(tokens.get(name, 0)) for name in names)
        total_cost = sum(float(costs.get(name, 0.0)) for name in names)
        old_total_tokens = sum(int(oldest_tokens.get(name, 0)) for name in names)
        old_total_cost = sum(float(oldest_costs.get(name, 0.0)) for name in names)
        return {
            "stamp": time.strftime("%H:%M:%S"),
            "windowS": int(USAGE_RATE_WINDOW_S),
            "cameraWindowS": int(CAMERA_RATE_WINDOW_S),
            "totalTokens": total_tokens,
            "totalTokensLabel": _format_tokens(total_tokens),
            "totalUsd": round(total_cost, 6),
            "totalTokensPerMin": round(max(0.0, (total_tokens - old_total_tokens) * 60.0 / elapsed), 1),
            "totalUsdPerMin": round(max(0.0, (total_cost - old_total_cost) * 60.0 / elapsed), 6),
            "gemini": provider("gemini"),
            "grok": provider("grok"),
            "claude": provider("claude"),
            "other": provider("other"),
            "camera": {
                "active": self._camera_active,
                "streamFps": stream["fps"],
                "streamKbps": stream["kbps"],
                "previewFps": preview["fps"],
                "previewKbps": preview["kbps"],
                "streamFrames": self._camera_stream_total_frames,
                "previewFrames": self._camera_preview_total_frames,
                "streamMb": round(self._camera_stream_total_bytes / (1024 * 1024), 3),
                "previewMb": round(self._camera_preview_total_bytes / (1024 * 1024), 3),
                "avgFrameKb": round(
                    (self._camera_stream_total_bytes / max(1, self._camera_stream_total_frames)) / 1024,
                    1,
                ),
            },
        }

    def _budget_payload(self) -> dict[str, Any]:
        report = self.gate.evaluate(self.tracker)
        tokens = self.tracker.tokens_by_provider()
        return {
            "period": report.period,
            "hardStop": report.hard_stop,
            "gemini": _provider_payload(report.gemini, tokens.get("gemini", 0)),
            "grok": _provider_payload(report.claude, tokens.get("grok", tokens.get("claude", 0))),
            "claude": _provider_payload(report.claude, tokens.get("claude", tokens.get("grok", 0))),
            "totalUsd": report.gemini.spent_usd + report.claude.spent_usd,
        }

    def _notify_blocked_budgets(self) -> None:
        report = self.gate.evaluate(self.tracker)
        for provider, budget in (("gemini", report.gemini), ("grok", report.claude)):
            if budget.blocked and provider not in self._already_blocked:
                self._already_blocked.add(provider)
                self._on_blocked(provider)

    def _on_blocked(self, provider: str) -> None:
        self.set_state("blocked")
        self.log_event(f"Budget {provider} agotado", "error")
        self.append_output(f"\n[Budget {provider} agotado - invocaciones nuevas bloqueadas]\n")

    def _memory_stats(self) -> dict[str, int]:
        ok = sum(1 for event in self._memory_events if event.get("status") == "ok")
        active = sum(1 for event in self._memory_events if event.get("status") == "running")
        error = sum(1 for event in self._memory_events if event.get("status") == "error")
        return {"ok": ok, "active": active, "error": error}

    def _privacy_enabled(self) -> bool:
        # Browser windows cannot be excluded from capture through the hidden Tk
        # root. Keep the label honest and use JARVIS_UI=tk when capture hiding is
        # required.
        return False

    @staticmethod
    def audio_level_from_pcm(pcm_bytes: bytes) -> float:
        if not pcm_bytes:
            return 0.05
        sample_count = len(pcm_bytes) // 2
        if sample_count <= 0:
            return 0.05
        step = max(1, sample_count // 700)
        total = 0.0
        count = 0
        for sample_idx in range(0, sample_count, step):
            sample = struct.unpack_from("<h", pcm_bytes, sample_idx * 2)[0]
            total += float(sample * sample)
            count += 1
        if count == 0:
            return 0.05
        rms = (total / count) ** 0.5 / 32768.0
        return max(0.05, min(1.0, rms * 7.5))

    @staticmethod
    def _is_memory_tool(name: str) -> bool:
        return name in {
            "jarvis_recall",
            "jarvis_session_recall",
            "jarvis_remember",
            "jarvis_browse",
            "jarvis_link",
            "obsidian_mcp",
            "study_mode",
        }

    def _memory_args_summary(self, name: str, args: dict[str, Any]) -> str:
        if name == "jarvis_recall":
            query = self._clip(str(args.get("query", "")), 72)
            top_k = args.get("top_k", 3)
            return f"recall '{query}' top {top_k}"
        if name == "jarvis_session_recall":
            query = self._clip(str(args.get("query", "")), 52)
            when = self._clip(str(args.get("when", "")), 24)
            return f"session recall '{query}' {when}".strip()
        if name == "jarvis_remember":
            title = self._clip(str(args.get("title", "(sin titulo)")), 72)
            content_len = len(str(args.get("content", "")))
            tags = args.get("tags") or []
            tags_text = ", ".join(map(str, tags[:4])) if isinstance(tags, list) else str(tags)
            suffix = f" tags={tags_text}" if tags_text else ""
            return f"remember '{title}' ({content_len} chars){suffix}"
        if name == "jarvis_browse":
            folder = self._clip(str(args.get("folder") or "<vault>"), 72)
            limit = args.get("limit", 20)
            return f"browse {folder} limit {limit}"
        if name == "jarvis_link":
            src = self._clip(str(args.get("note_from", "")), 44)
            dst = self._clip(str(args.get("note_to", "")), 44)
            return f"link {src} -> {dst}"
        if name == "obsidian_mcp":
            op = str(args.get("operation", "operation"))
            target = args.get("path") or args.get("note_from") or args.get("destination") or ""
            return f"obsidian {op} {self._clip(str(target), 72)}".strip()
        if name == "study_mode":
            action = str(args.get("action", "status"))
            title = str(args.get("title") or args.get("note_path") or "")
            return f"study {action} {self._clip(title, 72)}".strip()
        return name

    def _tool_args_summary(self, name: str, args: dict[str, Any]) -> str:
        if self._is_memory_tool(name):
            return self._memory_args_summary(name, args)
        try:
            rendered = json.dumps(args, ensure_ascii=False)
        except Exception:
            rendered = str(args)
        return self._clip(rendered, 90)

    def _memory_response_summary(self, name: str, response: Any) -> str:
        if not isinstance(response, dict):
            return f"{name} completo"
        if name == "jarvis_recall":
            found = response.get("found", 0)
            titles = []
            for item in response.get("results", [])[:3]:
                if isinstance(item, dict):
                    titles.append(item.get("title") or item.get("path") or "nota")
            title_text = f": {', '.join(map(str, titles))}" if titles else ""
            return f"recall encontro {found}{title_text}"
        if name == "jarvis_session_recall":
            found = response.get("found", 0)
            dates = [
                str(item.get("date") or item.get("title") or "sesion")
                for item in response.get("sessions", [])[:3]
                if isinstance(item, dict)
            ]
            suffix = f": {', '.join(dates)}" if dates else ""
            return f"session recall encontro {found}{suffix}"
        if name == "jarvis_remember":
            if not response.get("saved"):
                reason = response.get("reason") or response.get("error") or "sin detalle"
                return f"remember no guardo: {self._clip(str(reason), 80)}"
            op = response.get("operation", "saved")
            path = self._clip(str(response.get("path", response.get("title", ""))), 80)
            chunks = response.get("chunks_indexed")
            suffix = f", {chunks} chunks" if chunks is not None else ""
            return f"memory {op}: {path}{suffix}"
        if name == "jarvis_browse":
            return f"browse listo: {response.get('count', 0)} notas en {response.get('folder', '<vault>')}"
        if name == "jarvis_link":
            if response.get("linked"):
                return f"link creado: {response.get('from')} -> {response.get('to')}"
            return f"link fallo: {self._clip(str(response.get('error', 'sin detalle')), 80)}"
        if name == "obsidian_mcp":
            if response.get("ok") is False or response.get("error"):
                return f"obsidian fallo: {self._clip(str(response.get('error', 'sin detalle')), 80)}"
            detail = response.get("operation") or response.get("path") or response.get("message") or "operacion completa"
            return f"obsidian listo: {self._clip(str(detail), 80)}"
        if name == "study_mode":
            if response.get("ok") is False or response.get("error"):
                return f"study fallo: {self._clip(str(response.get('error', 'sin detalle')), 80)}"
            action = response.get("action") or response.get("status") or response.get("state") or "actualizado"
            note = response.get("note_path") or response.get("path") or response.get("title") or ""
            return f"study {action} {self._clip(str(note), 72)}".strip()
        if response.get("error"):
            return f"{name} fallo: {self._clip(str(response.get('error')), 80)}"
        return f"{name} completo"

    @staticmethod
    def _response_failed(response: Any) -> bool:
        return isinstance(response, dict) and (
            response.get("ok") is False
            or response.get("saved") is False and response.get("blocked") is True
            or bool(response.get("error"))
        )

    @staticmethod
    def _clip(text: str, max_chars: int) -> str:
        text = " ".join((text or "").split())
        if len(text) <= max_chars:
            return text
        return text[: max(0, max_chars - 3)].rstrip() + "..."
