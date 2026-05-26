"""Study Mode controller for Jarvis."""

from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass

from actions.chrome_reader import ChromeReader
from security.secret_filter import redact_secrets

from .ledger import Evidence, StudyLedger
from .obsidian_writer import StudyObsidianWriter
from .synthesizer import StudySynthesizer


@dataclass
class StudyModeConfig:
    capture_interval_s: float = 45.0
    max_chars_per_capture: int = 9000

    @classmethod
    def from_env(cls) -> "StudyModeConfig":
        return cls(
            capture_interval_s=float(os.environ.get("JARVIS_STUDY_CAPTURE_INTERVAL_S", "45")),
            max_chars_per_capture=int(os.environ.get("JARVIS_STUDY_MAX_CHARS", "9000")),
        )


class StudyModeController:
    """Lifecycle de JARVIS Study Mode.

    Start crea una sesion explicita. Mientras esta activa, puede capturar la
    pagina de Chrome por comando o en background cada N segundos. Flush sintetiza
    la evidencia pendiente y la persiste en Obsidian.
    """

    def __init__(self, *, vault, reasoner=None, config: StudyModeConfig | None = None) -> None:
        self.vault = vault
        self.reasoner = reasoner
        self.config = config or StudyModeConfig.from_env()
        self.reader = ChromeReader()
        self.writer = StudyObsidianWriter(vault)
        self.synthesizer = StudySynthesizer(reasoner=reasoner)
        self._ledger: StudyLedger | None = None
        self._note_abs_path = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused = False
        self._continuous = False

    def start(
        self,
        *,
        title: str | None = None,
        note_path: str | None = None,
        continuous: bool = True,
        capture_now: bool = True,
    ) -> dict:
        with self._lock:
            if self._ledger is not None:
                return {"ok": False, "error": "Study Mode ya esta activo", **self.status()}

            session_title = (title or "Jarvis Study Session").strip()
            note_abs = self.writer.resolve_note_path(note_path, session_title)
            self.writer.ensure_note(note_abs, session_title, source_hint="JARVIS Study Mode")
            rel_note = str(note_abs.relative_to(self.vault.vault_path)).replace("\\", "/")
            self._ledger = StudyLedger(
                session_id=uuid.uuid4().hex[:8],
                title=session_title,
                note_path=rel_note,
            )
            self._note_abs_path = note_abs
            self._paused = False
            self._continuous = bool(continuous)
            self._stop_event.clear()
            if self._continuous:
                self._thread = threading.Thread(
                    target=self._observer_loop,
                    name="JarvisStudyObserver",
                    daemon=True,
                )
                self._thread.start()

        captured = self.capture_page() if capture_now else {"captured": False}
        return {
            "ok": True,
            "message": f"Study Mode activo para '{session_title}'.",
            "note_path": self._ledger.note_path if self._ledger else "",
            "continuous": self._continuous,
            "initial_capture": captured,
        }

    def pause(self) -> dict:
        with self._lock:
            if self._ledger is None:
                return {"ok": False, "error": "Study Mode no esta activo"}
            self._paused = True
            return {"ok": True, "message": "Study Mode pausado.", **self.status()}

    def resume(self) -> dict:
        with self._lock:
            if self._ledger is None:
                return {"ok": False, "error": "Study Mode no esta activo"}
            self._paused = False
            return {"ok": True, "message": "Study Mode reanudado.", **self.status()}

    def stop(self, *, flush: bool = True) -> dict:
        with self._lock:
            if self._ledger is None:
                return {"ok": False, "error": "Study Mode no esta activo"}
        flush_result = self.flush_now() if flush else {"ok": True, "flushed": False}
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        with self._lock:
            final_status = self.status()
            self._ledger = None
            self._note_abs_path = None
            self._thread = None
            self._paused = False
            self._continuous = False
        return {
            "ok": True,
            "message": "Study Mode terminado.",
            "flush": flush_result,
            "final_status": final_status,
        }

    def status(self) -> dict:
        with self._lock:
            if self._ledger is None:
                return {"active": False}
            return {
                "active": True,
                "paused": self._paused,
                "continuous": self._continuous,
                **self._ledger.status(),
            }

    def capture_page(self, *, intent: str = "reading") -> dict:
        with self._lock:
            if self._ledger is None:
                return {"ok": False, "error": "Study Mode no esta activo"}
            if self._paused:
                return {"ok": False, "error": "Study Mode esta pausado"}
            ledger = self._ledger

        page = self.reader.read_active_page(max_chars=self.config.max_chars_per_capture)
        if not page.ok or not page.text:
            return {
                "ok": False,
                "captured": False,
                "source": page.source,
                "error": page.error,
                "warnings": page.warnings or [],
            }
        safe_page = page.as_dict(max_chars=self.config.max_chars_per_capture)

        item = Evidence(
            source_type="reading_web",
            title=safe_page.get("title") or "Chrome page",
            url=safe_page.get("url") or "",
            text=safe_page.get("text") or "",
            confidence=0.85 if page.source == "uia" else 0.95,
            metadata={
                "reader_source": page.source,
                "intent": intent,
                "untrusted_content": True,
                "redacted": True,
            },
        )
        with self._lock:
            added = ledger.add(item)
            status = ledger.status()
        return {
            "ok": True,
            "captured": added,
            "duplicate": not added,
            "title": item.title,
            "url": item.url,
            "chars": len(item.text),
            "status": status,
        }

    def add_observation(self, text: str, *, title: str = "Isaac observation") -> dict:
        clean = redact_secrets((text or "").strip())
        if not clean:
            return {"ok": False, "error": "Observacion vacia"}
        with self._lock:
            if self._ledger is None:
                return {"ok": False, "error": "Study Mode no esta activo"}
            item = Evidence(
                source_type="user_observation",
                title=title,
                text=clean,
                confidence=1.0,
            )
            added = self._ledger.add(item)
            return {"ok": True, "captured": added, "status": self._ledger.status()}

    def flush_now(self, *, intent: str = "study_notes") -> dict:
        with self._lock:
            if self._ledger is None or self._note_abs_path is None:
                return {"ok": False, "error": "Study Mode no esta activo"}
            pending = self._ledger.pending()
            ledger = self._ledger
            note_abs = self._note_abs_path

        if not pending:
            return {"ok": True, "flushed": False, "message": "No habia evidencia nueva."}

        synthesis = self.synthesizer.synthesize(
            session_title=ledger.title,
            evidence=pending,
            intent=intent,
        )
        write_result = self.writer.append_synthesis(
            note_abs,
            synthesis.markdown,
            section_title="Jarvis Study Synthesis",
        )
        with self._lock:
            ledger.mark_flushed(pending)
        return {
            "ok": True,
            "flushed": True,
            "evidence_count": synthesis.evidence_count,
            "used_reasoner": synthesis.used_reasoner,
            "write": write_result,
        }

    def _observer_loop(self) -> None:
        while not self._stop_event.wait(self.config.capture_interval_s):
            with self._lock:
                active = self._ledger is not None
                paused = self._paused
            if not active:
                break
            if paused:
                continue
            try:
                self.capture_page(intent="continuous_observer")
            except Exception:
                pass
