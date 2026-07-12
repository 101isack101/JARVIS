"""
vision/native_capture.py - Wrapper opcional para el sidecar C++ de camara.

El sidecar corre fuera del proceso Python, captura via Windows Media Foundation
y entrega frames JPEG por stdout. Python conserva la API de CameraCapture y solo
lee/escribe el frame final. Si no esta disponible, vision.camera usa OpenCV.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "vision" / "native" / "bin" / "jarvis_vision_sidecar.exe"
_HEADER = struct.Struct("<4sIIIQ")
_MAGIC = b"JVF1"


@dataclass(frozen=True)
class NativeVisionStatus:
    available: bool
    path: str
    detail: str
    version: str = ""


@dataclass(frozen=True)
class NativeCameraFrame:
    width: int
    height: int
    jpeg_bytes: bytes
    timestamp_ns: int = 0


def native_vision_mode() -> str:
    raw = os.environ.get("JARVIS_VISION_NATIVE", "auto").strip().lower()
    if raw in {"0", "false", "no", "off", "python", "opencv", "disabled"}:
        return "off"
    if raw in {"required", "require", "force", "forced", "native"}:
        return "required"
    return "auto"


def native_vision_exe() -> Path:
    raw = os.environ.get("JARVIS_VISION_NATIVE_EXE", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_EXE


def native_vision_status() -> NativeVisionStatus:
    exe = native_vision_exe()
    if sys.platform != "win32":
        return NativeVisionStatus(False, str(exe), "sidecar nativo disponible solo en Windows")
    if not exe.exists():
        return NativeVisionStatus(False, str(exe), "no existe; ejecuta vision/native/build.ps1")
    try:
        proc = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception as exc:
        return NativeVisionStatus(False, str(exe), f"{type(exc).__name__}: {exc}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit={proc.returncode}"
        return NativeVisionStatus(False, str(exe), detail[:500])
    version = (proc.stdout or "").strip()
    return NativeVisionStatus(True, str(exe), "ok", version=version)


def _read_exact(pipe, size: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        if time.time() > deadline:
            raise TimeoutError(f"timeout leyendo {size} bytes del sidecar de vision")
        chunk = pipe.read(remaining)
        if not chunk:
            raise EOFError("sidecar de vision cerro stdout")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_frame(pipe, timeout_s: float) -> NativeCameraFrame:
    deadline = time.time() + timeout_s
    header = _read_exact(pipe, _HEADER.size, deadline)
    magic, width, height, payload_len, timestamp_ns = _HEADER.unpack(header)
    if magic != _MAGIC:
        raise RuntimeError(f"protocolo de vision invalido: magic={magic!r}")
    if payload_len <= 0 or payload_len > 32 * 1024 * 1024:
        raise RuntimeError(f"payload JPEG invalido: {payload_len} bytes")
    jpeg = _read_exact(pipe, payload_len, deadline)
    return NativeCameraFrame(width=width, height=height, jpeg_bytes=jpeg, timestamp_ns=timestamp_ns)


def _drain_stderr(proc: subprocess.Popen, sink: list[str]) -> None:
    stream = proc.stderr
    if stream is None:
        return
    try:
        for raw in iter(stream.readline, b""):
            if not raw:
                break
            try:
                sink.append(raw.decode("utf-8", errors="replace").strip())
            except Exception:
                pass
            del sink[:-20]
    except Exception:
        return


class NativeCameraStream:
    def __init__(
        self,
        *,
        index: int,
        max_side: int,
        fps: float,
        exe: Path | None = None,
    ) -> None:
        self.index = int(index)
        self.max_side = int(max_side)
        self.fps = float(fps)
        self.exe = Path(exe) if exe is not None else native_vision_exe()
        self._stderr_lines: list[str] = []
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            [
                str(self.exe),
                "--device",
                str(self.index),
                "--max-side",
                str(self.max_side),
                "--fps",
                f"{self.fps:.3f}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        self._stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(self._proc, self._stderr_lines),
            name="JarvisVisionSidecarStderr",
            daemon=True,
        )
        self._stderr_thread.start()

    @property
    def stderr_tail(self) -> str:
        return " | ".join(line for line in self._stderr_lines if line)[-1000:]

    def read_frame(self, timeout_s: float = 5.0) -> NativeCameraFrame:
        if self._proc.stdout is None:
            raise RuntimeError("sidecar sin stdout")
        if self._proc.poll() is not None:
            detail = self.stderr_tail or f"exit={self._proc.returncode}"
            raise RuntimeError(f"sidecar de vision finalizado: {detail}")
        return _read_frame(self._proc.stdout, timeout_s=timeout_s)

    def close(self) -> None:
        proc = self._proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)


def capture_once(index: int, max_side: int, timeout_s: float = 6.0) -> NativeCameraFrame:
    stream = NativeCameraStream(index=index, max_side=max_side, fps=1.0)
    try:
        return stream.read_frame(timeout_s=timeout_s)
    finally:
        stream.close()
