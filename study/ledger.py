"""Evidence ledger for JARVIS Study Mode."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class Evidence:
    source_type: str
    title: str = ""
    url: str = ""
    text: str = ""
    screenshot_path: str = ""
    captured_at: str = field(default_factory=now_iso)
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        basis = "\n".join([
            self.source_type,
            self.title,
            self.url,
            self.text[:2000],
            self.screenshot_path,
        ])
        return hashlib.sha256(basis.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def to_markdown(self, index: int) -> str:
        parts = [
            f"### Evidence {index}: {self.title or self.source_type}",
            "",
            f"- Type: `{self.source_type}`",
            f"- Captured: `{self.captured_at}`",
        ]
        if self.url:
            parts.append(f"- URL: {self.url}")
        if self.screenshot_path:
            parts.append(f"- Screenshot: `{self.screenshot_path}`")
        if self.text:
            parts.extend(["", self.text.strip()])
        return "\n".join(parts).strip()


@dataclass
class StudyLedger:
    session_id: str
    title: str
    note_path: str
    started_at: str = field(default_factory=now_iso)
    evidence: list[Evidence] = field(default_factory=list)
    flushed_fingerprints: set[str] = field(default_factory=set)

    def add(self, item: Evidence) -> bool:
        fp = item.fingerprint
        if any(existing.fingerprint == fp for existing in self.evidence):
            return False
        self.evidence.append(item)
        return True

    def pending(self) -> list[Evidence]:
        return [e for e in self.evidence if e.fingerprint not in self.flushed_fingerprints]

    def mark_flushed(self, items: list[Evidence]) -> None:
        for item in items:
            self.flushed_fingerprints.add(item.fingerprint)

    def status(self) -> dict:
        pending = self.pending()
        return {
            "session_id": self.session_id,
            "title": self.title,
            "note_path": self.note_path,
            "started_at": self.started_at,
            "evidence_total": len(self.evidence),
            "evidence_pending": len(pending),
            "last_evidence": self.evidence[-1].captured_at if self.evidence else None,
        }
