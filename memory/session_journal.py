"""
memory/session_journal.py - Journal JSONL append-only de turnos de conversación.

Responsabilidad única: persistir cada turno (Isaac dijo / Jarvis respondió) de
forma durable e inmediata, para que el resumen de sesión sobreviva a un cierre
sucio (kill-switch Ctrl+Alt+Q hace os._exit(130) y se salta stop()).

Diseño:
  - Un objeto JSON por línea: {"ts": ISO8601, "user": str, "jarvis": str}
  - Append-only: cada turno se escribe y flushea de inmediato (crash-safe)
  - Thread-safe: se llama desde callbacks de Gemini (RLock)
  - Redacta secretos ANTES de escribir a disco (security.secret_filter)
  - Nunca propaga excepciones de I/O a la conversación (fail-safe en el caller)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from security.secret_filter import redact_secrets


class SessionJournal:
    """Journal append-only de turnos, durable y thread-safe."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def append_turn(self, user: str, jarvis: str, ts: str | None = None) -> None:
        """Persiste un turno. Redacta secretos. Ignora turnos vacíos."""
        user = (user or "").strip()
        jarvis = (jarvis or "").strip()
        if not user and not jarvis:
            return
        record = {
            "ts": ts or datetime.now().isoformat(timespec="seconds"),
            "user": redact_secrets(user),
            "jarvis": redact_secrets(jarvis),
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def has_pending(self) -> bool:
        """True si el journal existe y tiene al menos un turno legible."""
        return self.turn_count() > 0

    def read_turns(self) -> list[dict]:
        """Devuelve los turnos. Salta líneas corruptas sin romper."""
        with self._lock:
            if not self.path.exists():
                return []
            turns: list[dict] = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and ("user" in obj or "jarvis" in obj):
                    turns.append(obj)
            return turns

    def turn_count(self) -> int:
        return len(self.read_turns())

    def clear(self) -> None:
        """Borra el journal (tras síntesis exitosa)."""
        with self._lock:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
