"""Thin OBS WebSocket client wrapper.

Jarvis depends on this module lazily so the assistant can still boot when OBS
or obsws-python are not installed.
"""

from __future__ import annotations

import time
import subprocess
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import OBSMemoryConfig


@dataclass
class OBSRecordStatus:
    active: bool
    paused: bool = False
    timecode: str = ""
    output_path: str = ""


def _field(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


class OBSWebSocketClient:
    def __init__(self, config: OBSMemoryConfig) -> None:
        self.config = config
        self._client = None

    def _connect(self):
        if self._client is not None:
            return self._client
        try:
            from obsws_python import ReqClient
        except ImportError as exc:
            raise RuntimeError(
                "Falta dependencia obsws-python. Instala requirements.txt para controlar OBS."
            ) from exc
        self._client = ReqClient(
            host=self.config.websocket_host,
            port=self.config.websocket_port,
            password=self.config.websocket_password or None,
            timeout=5,
        )
        return self._client

    def connect_or_start(self):
        if self._port_open():
            return self._connect()
        if not self.config.auto_start:
            return self._connect()
        self.start_obs_process()
        deadline = time.time() + self.config.startup_timeout_s
        while time.time() < deadline:
            if self._port_open():
                self._client = None
                return self._connect()
            time.sleep(0.75)
        try:
            self._client = None
            return self._connect()
        except Exception as exc:
            raise RuntimeError(f"OBS no abrio WebSocket a tiempo: {exc}") from exc

    def start_obs_process(self) -> None:
        exe = self.config.obs_exe
        if exe is None or not Path(exe).exists():
            raise RuntimeError(
                "No encontre obs64.exe. Configura JARVIS_OBS_EXE o instala OBS Studio."
            )
        args = [
            str(exe),
            "--minimize-to-tray",
            "--disable-shutdown-check",
            "--websocket_port",
            str(self.config.websocket_port),
            "--websocket_password",
            self.config.websocket_password,
            "--websocket_ipv4_only",
        ]
        subprocess.Popen(
            args,
            cwd=str(Path(exe).parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

    def _port_open(self) -> bool:
        try:
            with socket.create_connection(
                (self.config.websocket_host, self.config.websocket_port),
                timeout=0.4,
            ):
                return True
        except OSError:
            return False

    def status(self) -> OBSRecordStatus:
        resp = self.connect_or_start().get_record_status()
        return OBSRecordStatus(
            active=bool(_field(resp, "outputActive", "output_active", default=False)),
            paused=bool(_field(resp, "outputPaused", "output_paused", default=False)),
            timecode=str(_field(resp, "outputTimecode", "output_timecode", default="") or ""),
            output_path=str(_field(resp, "outputPath", "output_path", default="") or ""),
        )

    def start_recording(self) -> dict:
        status = self.status()
        if status.active:
            return {"ok": True, "already_recording": True, "status": status.__dict__}
        self.connect_or_start().start_record()
        status = self._wait_for_recording(active=True)
        return {"ok": True, "started": True, "status": status.__dict__}

    def stop_recording(self) -> dict:
        status = self.status()
        if not status.active:
            return {"ok": False, "error": "OBS no esta grabando", "status": status.__dict__}
        resp = self.connect_or_start().stop_record()
        self._wait_for_recording(active=False)
        output_path = str(_field(resp, "outputPath", "output_path", default="") or "")
        return {
            "ok": True,
            "stopped": True,
            "output_path": output_path,
            "status_before_stop": status.__dict__,
        }

    def _wait_for_recording(self, *, active: bool, timeout_s: float = 5.0) -> OBSRecordStatus:
        deadline = time.time() + timeout_s
        last = self.status()
        while time.time() < deadline:
            last = self.status()
            if last.active is active:
                return last
            time.sleep(0.2)
        return last
