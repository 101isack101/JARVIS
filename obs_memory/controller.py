"""Controller for OBS episodic memory."""

from __future__ import annotations

import time
import uuid
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from security.policy import is_inside_root
from security.secret_filter import redact_secrets

from .config import OBSMemoryConfig
from .course_analyzer import OBSCourseAnalyzer
from .media import extract_artifacts
from .obs_client import OBSWebSocketClient
from .synthesizer import OBSMemorySynthesizer
from .transcriber import WhisperTranscriber
from .writer import OBSMemoryWriter, safe_filename


@dataclass
class ProcessingResult:
    ok: bool
    note_path: str = ""
    video_deleted: bool = False
    error: str = ""


class OBSMemoryController:
    def __init__(
        self,
        *,
        vault,
        reasoner=None,
        config: OBSMemoryConfig | None = None,
        obs_client=None,
        transcriber=None,
        synthesizer=None,
        on_job_done: Callable[[dict], None] | None = None,
    ) -> None:
        self.vault = vault
        self.reasoner = reasoner
        self.config = config or OBSMemoryConfig.from_env(Path(__file__).resolve().parent.parent)
        self.obs = obs_client or OBSWebSocketClient(self.config)
        self.transcriber = transcriber or WhisperTranscriber(
            self.config.whisper_model,
            self.config.whisper_device,
            self.config.whisper_compute_type,
        )
        self.synthesizer = synthesizer or OBSMemorySynthesizer(reasoner=reasoner)
        self.writer = OBSMemoryWriter(vault, self.config.output_folder)
        self.current_title: str | None = None
        self.current_started_at: float | None = None
        self._jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.RLock()
        self.on_job_done = on_job_done

    def status(self) -> dict:
        payload = {"ok": True, "config": self.config.as_status()}
        try:
            payload["obs"] = self.obs.status().__dict__
        except Exception as exc:
            payload["obs"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        latest = self.latest_recording()
        payload["latest_recording"] = str(latest) if latest else ""
        payload["active_session"] = {
            "title": self.current_title or "",
            "started_at": self.current_started_at,
        }
        payload["jobs"] = self.jobs(limit=5)
        return payload

    def start(self, title: str | None = None) -> dict:
        if not self.config.enabled:
            return {
                "ok": False,
                "error": "OBS Memory esta desactivado. Configura JARVIS_OBS_MEMORY_ENABLED=true.",
            }
        clean_title = safe_filename(redact_secrets((title or "OBS Session").strip()))
        try:
            result = self.obs.start_recording()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if result.get("ok"):
            self.current_title = clean_title
            self.current_started_at = time.time()
        return {"title": clean_title, **result}

    def stop(self, *, title: str | None = None, process: bool = True) -> dict:
        if not self.config.enabled:
            return {
                "ok": False,
                "error": "OBS Memory esta desactivado. Configura JARVIS_OBS_MEMORY_ENABLED=true.",
            }
        try:
            stop_result = self.obs.stop_recording()
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not stop_result.get("ok"):
            return stop_result
        final_title = safe_filename(title or self.current_title or "OBS Session")
        self.current_title = None
        self.current_started_at = None
        if not process:
            return {"ok": True, "stopped": True, "processed": False, "title": final_title}
        video_path = Path(stop_result.get("output_path") or "")
        if not video_path.exists():
            video_path = self.latest_recording()
        if video_path is None:
            return {
                "ok": False,
                "stopped": True,
                "error": "OBS detuvo la grabacion pero no encontre el archivo de salida.",
            }
        if self.config.process_background:
            job = self.enqueue_process(video_path, title=final_title)
            return {
                "ok": True,
                "stopped": True,
                "processing": "background",
                "job": job,
                "message": "Grabacion detenida. Procesamiento OBS en segundo plano.",
            }
        processed = self.process_file(video_path, title=final_title)
        return {"ok": processed.get("ok", False), "stopped": True, "process": processed}

    def process_latest(self, title: str | None = None, *, background: bool | None = None) -> dict:
        latest = self.latest_recording()
        if latest is None:
            return {"ok": False, "error": "No encontre grabaciones OBS para procesar."}
        if self.config.process_background if background is None else bool(background):
            return {
                "ok": True,
                "processing": "background",
                "job": self.enqueue_process(latest, title=title or latest.stem),
            }
        return self.process_file(latest, title=title or latest.stem)

    def enqueue_process(self, path: str | Path, title: str | None = None) -> dict:
        video_path = Path(path).expanduser().resolve()
        job_id = uuid.uuid4().hex[:8]
        record = {
            "job_id": job_id,
            "status": "queued",
            "title": title or video_path.stem,
            "video_path": str(video_path),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "started_at": "",
            "finished_at": "",
            "result": None,
            "error": "",
        }
        with self._jobs_lock:
            self._jobs[job_id] = record
        thread = threading.Thread(
            target=self._process_job,
            args=(job_id, video_path, title),
            name=f"OBSProcess-{job_id}",
            daemon=True,
        )
        thread.start()
        return self._public_job(record)

    def jobs(self, limit: int = 10) -> list[dict]:
        with self._jobs_lock:
            records = list(self._jobs.values())[-max(1, limit):]
        return [self._public_job(item) for item in reversed(records)]

    def _process_job(self, job_id: str, video_path: Path, title: str | None) -> None:
        with self._jobs_lock:
            self._jobs[job_id]["status"] = "running"
            self._jobs[job_id]["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        result = self.process_file(video_path, title=title)
        with self._jobs_lock:
            self._jobs[job_id]["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._jobs[job_id]["result"] = result
            self._jobs[job_id]["status"] = "done" if result.get("ok") else "failed"
            self._jobs[job_id]["error"] = "" if result.get("ok") else result.get("error", "")
            public = self._public_job(self._jobs[job_id])
        if self.on_job_done is not None:
            try:
                self.on_job_done(public)
            except Exception:
                pass

    @staticmethod
    def _public_job(record: dict) -> dict:
        result = record.get("result") or {}
        return {
            "job_id": record.get("job_id", ""),
            "status": record.get("status", ""),
            "title": record.get("title", ""),
            "video_path": record.get("video_path", ""),
            "created_at": record.get("created_at", ""),
            "started_at": record.get("started_at", ""),
            "finished_at": record.get("finished_at", ""),
            "note_path": result.get("note_path", ""),
            "video_deleted": result.get("video_deleted", False),
            "error": record.get("error", ""),
        }

    def process_file(self, path: str | Path, title: str | None = None) -> dict:
        video_path = Path(path).expanduser().resolve()
        if not video_path.exists():
            return {"ok": False, "error": f"Video no existe: {video_path}"}
        if video_path.suffix.lower() not in self.config.video_extensions:
            return {"ok": False, "error": f"Extension no soportada: {video_path.suffix}"}
        if not self._video_path_allowed(video_path):
            return {
                "ok": False,
                "error": (
                    "Video fuera de JARVIS_OBS_RECORDING_DIR. "
                    "Configura JARVIS_OBS_ALLOW_EXTERNAL_VIDEO_PATHS=true para procesar rutas externas."
                ),
            }

        session_id = uuid.uuid4().hex[:8]
        session_title = safe_filename(redact_secrets(title or video_path.stem))
        work_dir = self.config.data_dir / "sessions" / f"{time.strftime('%Y%m%d_%H%M%S')}_{session_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            self._wait_until_stable(video_path)
            if self.config.analysis_mode in {"course", "learning", "curso"}:
                return self._process_course_file(video_path, session_title, work_dir)
            artifacts = extract_artifacts(
                video_path,
                work_dir,
                keyframes=self.config.keyframes_per_video,
            )
            transcript = ""
            if artifacts.wav_path is not None:
                transcript = self.transcriber.transcribe(artifacts.wav_path)
            transcript = redact_secrets(transcript)
            if len(transcript) > self.config.max_transcript_chars:
                transcript = transcript[: self.config.max_transcript_chars].rstrip() + "\n\n[truncado]"
            transcript_path = work_dir / "transcript.md"
            transcript_path.write_text(transcript, encoding="utf-8")

            synthesis = self.synthesizer.synthesize(
                title=session_title,
                transcript=transcript,
                frame_paths=artifacts.frame_paths,
                duration_s=artifacts.duration_s,
                source_video=video_path,
            )
            note_path = self.writer.write_session(
                title=session_title,
                markdown=synthesis.markdown,
                source_video=video_path,
                transcript_path=transcript_path,
                frame_paths=artifacts.frame_paths,
                duration_s=artifacts.duration_s,
                used_reasoner=synthesis.used_reasoner,
                retention=self.config.retention,
            )
            deleted = self._apply_retention(video_path)
            return {
                "ok": True,
                "note_path": str(note_path.relative_to(self.vault.vault_path)),
                "video_deleted": deleted,
                "transcript_chars": len(transcript),
                "keyframes": len(artifacts.frame_paths),
                "used_reasoner": synthesis.used_reasoner,
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _process_course_file(self, video_path: Path, session_title: str, work_dir: Path) -> dict:
        analyzer = OBSCourseAnalyzer(transcriber=self.transcriber, config=self.config)
        analysis = analyzer.analyze(
            video_path=video_path,
            work_dir=work_dir,
            course_name=session_title,
            course_source=f"OBS Studio - {video_path.name}",
        )
        transcript_path = work_dir / "transcript.md"
        transcript = redact_secrets(analysis.transcript)
        if len(transcript) > self.config.max_transcript_chars:
            transcript = transcript[: self.config.max_transcript_chars].rstrip() + "\n\n[truncado]"
        transcript_path.write_text(transcript, encoding="utf-8")
        frame_paths = sorted(work_dir.rglob("frame_*.png"))
        note_path = self.writer.write_session(
            title=session_title,
            markdown=analysis.markdown,
            source_video=video_path,
            transcript_path=transcript_path,
            frame_paths=frame_paths[: self.config.keyframes_per_video],
            duration_s=video_duration_safe(video_path),
            used_reasoner=analysis.used_reasoner,
            retention=self.config.retention,
        )
        deleted = self._apply_retention(video_path)
        return {
            "ok": True,
            "note_path": str(note_path.relative_to(self.vault.vault_path)),
            "video_deleted": deleted,
            "transcript_chars": len(transcript),
            "keyframes": len(frame_paths),
            "used_reasoner": analysis.used_reasoner,
            "analysis_mode": "course",
            "chunks": analysis.chunks_count,
        }

    def latest_recording(self) -> Path | None:
        base = self.config.recording_dir
        if base is None or not base.exists():
            return None
        files = [
            p
            for p in base.iterdir()
            if p.is_file() and p.suffix.lower() in self.config.video_extensions
        ]
        if not files:
            return None
        return max(files, key=lambda p: p.stat().st_mtime)

    def _apply_retention(self, video_path: Path) -> bool:
        if self.config.retention not in {"delete_video_after_success", "delete"}:
            return False
        try:
            video_path.unlink()
            return True
        except Exception:
            return False

    def _video_path_allowed(self, video_path: Path) -> bool:
        if self.config.allow_external_video_paths:
            return True
        if self.config.recording_dir is None:
            return False
        return is_inside_root(video_path, self.config.recording_dir)

    @staticmethod
    def _wait_until_stable(video_path: Path, timeout_s: float = 12.0) -> None:
        """Wait briefly until OBS finishes flushing the recording file."""
        deadline = time.time() + timeout_s
        previous_size = -1
        stable_ticks = 0
        while time.time() < deadline:
            size = video_path.stat().st_size
            if size == previous_size and size > 0:
                stable_ticks += 1
                if stable_ticks >= 2:
                    return
            else:
                stable_ticks = 0
                previous_size = size
            time.sleep(0.5)


def video_duration_safe(video_path: Path) -> int:
    try:
        from .media import video_duration_s

        return video_duration_s(video_path)
    except Exception:
        return 0
