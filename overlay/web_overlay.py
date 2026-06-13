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
from urllib.parse import unquote, urlparse

from jarvis_version import JARVIS_VERSION_LABEL
from overlay.scheduler import UiScheduler
from telemetry.budgets import BudgetGate, ProviderBudget
from telemetry.tracker import TokenTracker


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
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
REFRESH_MS = 500
DEFAULT_AUDIO_VISUAL_FPS = 30


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

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path == "/state":
            self._send_json(self.server.overlay.snapshot())
            return
        if parsed.path == "/events":
            self._stream_events()
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
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
            ok = self.server.overlay.handle_web_command(command)
            self._send_json({"ok": ok})
            return
        self._send_json({"ok": False, "error": "unknown endpoint"}, HTTPStatus.NOT_FOUND)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _authorized(self, body: dict[str, Any]) -> bool:
        token = self.headers.get("X-Jarvis-Ui-Token", "") or str(body.get("uiToken", ""))
        return secrets.compare_digest(token, self.server.overlay.ui_token)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path: str) -> None:
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
    ) -> None:
        self.tracker = tracker
        self.gate = gate
        self._on_close = on_close or (lambda: None)
        self._state = "idle"
        self._mode = "PTT"
        self._connection_status = "connecting"
        self._connection_detail = ""
        self._input_text = ""
        self._output_text = ""
        self._events: list[str] = []
        self._event_history: list[tuple[str, str, str]] = []
        self._memory_events: list[dict[str, Any]] = []
        self._agent_events: list[dict[str, Any]] = []
        self._audio_telemetry: dict[str, Any] = {}
        self._latency_lines: list[str] = []
        self._camera_active = False
        self._camera_frame_b64: str | None = None
        self._camera_focus: dict[str, Any] | None = None
        self._memory_active: dict[str, dict[str, Any]] = {}
        self._pending_approvals: dict[str, Callable[[str, bool], None]] = {}
        self.ui_token = secrets.token_urlsafe(32)
        self._already_blocked: set[str] = set()
        self._clients: set[queue.Queue[str]] = set()
        self._clients_lock = threading.Lock()
        self._last_audio_emit_ts = 0.0
        self._last_camera_emit_ts = 0.0
        self._tool_event_seq = 0
        self._audio_emit_interval_s = _audio_emit_interval_from_env()
        self._closed = False
        self._close_event = threading.Event()
        self._log_path = Path(__file__).resolve().parent.parent / "data" / "jarvis.log"
        self._scheduler = UiScheduler(name="JarvisWebScheduler")

        # Watchdog supervisado (shell Tauri): si la ventana desaparece sin avisar
        # >grace, apagarse para no dejar un backend zombi con el microfono abierto.
        self._supervised = _env_truthy("JARVIS_SUPERVISED", False)
        self._had_client = False
        self._last_client_seen = time.monotonic()
        self._watchdog_grace_s = 60.0

        self._server = self._start_server()
        self.url = f"http://{self._server.server_address[0]}:{self._server.server_address[1]}/"
        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="jarvis-web-ui",
            daemon=True,
        )
        self._server_thread.start()

        self.log_event("Web UI lista", "ok")
        self.after(REFRESH_MS, self._refresh_runtime_panels)
        if self._supervised:
            self.after(10_000, self._watchdog_check)
        if _env_truthy("JARVIS_WEB_UI_OPEN_BROWSER", True) and not self._supervised:
            self.after(250, lambda: webbrowser.open(self.url))

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
            "uiToken": self.ui_token,
            "state": self._state,
            "mode": self._mode,
            "connection": {
                "status": self._connection_status,
                "detail": self._connection_detail,
            },
            "privacy": self.privacy_label_text(),
            "inputTranscript": self._input_text[-12000:],
            "outputTranscript": self._output_text[-16000:],
            "events": [
                {"stamp": stamp, "level": level, "message": message}
                for stamp, level, message in self._event_history[-8:]
            ],
            "memory": self._memory_stats(),
            "agentEvents": self._agent_events[-30:],
            "audioTelemetry": self._audio_telemetry,
            "latency": self._latency_lines,
            "cameraActive": self._camera_active,
            "cameraFrame": self._camera_frame_b64,
            "cameraFocus": self._camera_focus,
            "budget": self._budget_payload(),
        }

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
        self.emit("appendInput", text)

    def append_output(self, text: str) -> None:
        if not text:
            return
        self._output_text = f"{self._output_text}{text}"[-16000:]
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
        self.emit("clearTranscripts")
        self.log_event("Transcript reiniciado")

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

    def handle_web_command(self, command: str) -> bool:
        if command == "close":
            self.after(0, self.close)
            return True
        if command == "clearTranscripts":
            self.after(0, self.reset_transcripts)
            return True
        if command == "openDashboard":
            self.after(0, self.open_dashboard)
            return True
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
        """Envia frame JPEG como base64 por SSE (throttle 4fps)."""
        now = time.monotonic()
        if now - self._last_camera_emit_ts < 0.25:  # 4fps max
            return
        self._last_camera_emit_ts = now
        self._camera_frame_b64 = base64.b64encode(frame.jpeg_bytes).decode("ascii")
        if not self._camera_active:
            self._camera_active = True
            self.emit("setCameraActive", True)
        self.emit("cameraFrame", self._camera_frame_b64)

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
            self.emit("updateMemoryStats", self._memory_stats())
            self._notify_blocked_budgets()
        finally:
            try:
                self.after(REFRESH_MS, self._refresh_runtime_panels)
            except Exception:
                pass

    def _budget_payload(self) -> dict[str, Any]:
        report = self.gate.evaluate(self.tracker)
        tokens = self.tracker.tokens_by_provider()
        return {
            "period": report.period,
            "hardStop": report.hard_stop,
            "gemini": _provider_payload(report.gemini, tokens["gemini"]),
            "claude": _provider_payload(report.claude, tokens["claude"]),
            "totalUsd": report.gemini.spent_usd + report.claude.spent_usd,
        }

    def _notify_blocked_budgets(self) -> None:
        report = self.gate.evaluate(self.tracker)
        for provider, budget in (("gemini", report.gemini), ("claude", report.claude)):
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
