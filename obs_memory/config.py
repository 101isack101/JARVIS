"""Configuration for OBS episodic memory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_VIDEO_EXTENSIONS = (".mkv", ".mp4", ".mov", ".flv")


@dataclass
class OBSMemoryConfig:
    enabled: bool = False
    recording_dir: Path | None = None
    data_dir: Path = Path("data") / "obs_memory"
    output_folder: str = "obs_sessions"
    retention: str = "keep_video"
    allow_external_video_paths: bool = False
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 4455
    websocket_password: str = ""
    obs_exe: Path | None = None
    auto_start: bool = True
    startup_timeout_s: float = 20.0
    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    keyframes_per_video: int = 8
    max_transcript_chars: int = 30000
    video_extensions: tuple[str, ...] = DEFAULT_VIDEO_EXTENSIONS
    process_background: bool = True
    analysis_mode: str = "course"
    course_chunk_sec: int = 300
    course_keyframes_per_chunk: int = 6

    @classmethod
    def from_env(cls, root: Path | None = None) -> "OBSMemoryConfig":
        root = root or Path.cwd()
        recording_dir = os.environ.get("JARVIS_OBS_RECORDING_DIR", "").strip()
        data_dir = Path(os.environ.get("JARVIS_OBS_DATA_DIR", str(root / "data" / "obs_memory")))
        extensions = tuple(
            ext.strip().lower() if ext.strip().startswith(".") else f".{ext.strip().lower()}"
            for ext in os.environ.get(
                "JARVIS_OBS_VIDEO_EXTENSIONS",
                ",".join(DEFAULT_VIDEO_EXTENSIONS),
            ).split(",")
            if ext.strip()
        )
        return cls(
            enabled=os.environ.get("JARVIS_OBS_MEMORY_ENABLED", "false").lower()
            in {"true", "1", "yes", "on"},
            recording_dir=Path(recording_dir).expanduser().resolve() if recording_dir else None,
            data_dir=data_dir.expanduser().resolve(),
            output_folder=os.environ.get("JARVIS_OBS_OUTPUT_FOLDER", "obs_sessions").strip()
            or "obs_sessions",
            retention=os.environ.get(
                "JARVIS_OBS_RETENTION",
                "keep_video",
            ).strip().lower(),
            allow_external_video_paths=os.environ.get(
                "JARVIS_OBS_ALLOW_EXTERNAL_VIDEO_PATHS",
                "false",
            ).lower()
            in {"true", "1", "yes", "on"},
            websocket_host=os.environ.get("JARVIS_OBS_WEBSOCKET_HOST", "127.0.0.1").strip(),
            websocket_port=int(os.environ.get("JARVIS_OBS_WEBSOCKET_PORT", "4455")),
            websocket_password=os.environ.get("JARVIS_OBS_WEBSOCKET_PASSWORD", ""),
            obs_exe=_obs_exe_from_env(),
            auto_start=os.environ.get("JARVIS_OBS_AUTO_START", "true").lower()
            in {"true", "1", "yes", "on"},
            startup_timeout_s=float(os.environ.get("JARVIS_OBS_STARTUP_TIMEOUT_S", "20")),
            whisper_model=os.environ.get("JARVIS_OBS_WHISPER_MODEL", "small").strip() or "small",
            whisper_device=os.environ.get("JARVIS_OBS_WHISPER_DEVICE", "cpu").strip() or "cpu",
            whisper_compute_type=os.environ.get(
                "JARVIS_OBS_WHISPER_COMPUTE_TYPE",
                "int8",
            ).strip()
            or "int8",
            keyframes_per_video=max(
                0,
                min(int(os.environ.get("JARVIS_OBS_KEYFRAMES", "8")), 24),
            ),
            max_transcript_chars=max(
                2000,
                min(int(os.environ.get("JARVIS_OBS_MAX_TRANSCRIPT_CHARS", "30000")), 120000),
            ),
            video_extensions=extensions or DEFAULT_VIDEO_EXTENSIONS,
            process_background=os.environ.get("JARVIS_OBS_PROCESS_BACKGROUND", "true").lower()
            in {"true", "1", "yes", "on"},
            analysis_mode=os.environ.get("JARVIS_OBS_ANALYSIS_MODE", "course").strip().lower()
            or "course",
            course_chunk_sec=max(
                60,
                min(int(os.environ.get("JARVIS_OBS_COURSE_CHUNK_SEC", "300")), 900),
            ),
            course_keyframes_per_chunk=max(
                1,
                min(int(os.environ.get("JARVIS_OBS_COURSE_KEYFRAMES_PER_CHUNK", "6")), 12),
            ),
        )

    def as_status(self) -> dict:
        return {
            "enabled": self.enabled,
            "recording_dir": str(self.recording_dir) if self.recording_dir else "",
            "data_dir": str(self.data_dir),
            "output_folder": self.output_folder,
            "retention": self.retention,
            "allow_external_video_paths": self.allow_external_video_paths,
            "websocket": {
                "host": self.websocket_host,
                "port": self.websocket_port,
                "password_configured": bool(self.websocket_password),
            },
            "obs_exe": str(self.obs_exe) if self.obs_exe else "",
            "auto_start": self.auto_start,
            "startup_timeout_s": self.startup_timeout_s,
            "whisper": {
                "model": self.whisper_model,
                "device": self.whisper_device,
                "compute_type": self.whisper_compute_type,
            },
            "keyframes_per_video": self.keyframes_per_video,
            "video_extensions": list(self.video_extensions),
            "process_background": self.process_background,
            "analysis_mode": self.analysis_mode,
            "course_chunk_sec": self.course_chunk_sec,
            "course_keyframes_per_chunk": self.course_keyframes_per_chunk,
        }


def _obs_exe_from_env() -> Path | None:
    explicit = os.environ.get("JARVIS_OBS_EXE", "").strip()
    candidates = [
        Path(explicit) if explicit else None,
        Path(r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"),
        Path(r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate.resolve()
    return None
