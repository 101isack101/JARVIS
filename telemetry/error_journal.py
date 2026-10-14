"""
Structured local error journal for Jarvis.

`data/jarvis.log` is the human-readable stream. This module writes compact
JSONL records to `data/error_journal.jsonl` so regressions can be grouped,
searched, and reviewed after a run without scraping prose logs.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_MAX_TEXT = 2000


def _enabled() -> bool:
    value = os.environ.get("JARVIS_ERROR_JOURNAL", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _default_path() -> Path:
    override = os.environ.get("JARVIS_ERROR_JOURNAL_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data" / "error_journal.jsonl"


def _redact(value: Any) -> Any:
    try:
        from security.secret_filter import redact_log_text

        if isinstance(value, str):
            return redact_log_text(value, max_chars=_MAX_TEXT)
        text = json.dumps(value, ensure_ascii=False, default=str)
        return json.loads(redact_log_text(text, max_chars=_MAX_TEXT))
    except Exception:
        text = str(value)
        return text[:_MAX_TEXT]


def _exc_payload(exc: BaseException | None) -> dict[str, Any]:
    if exc is None:
        return {}
    return {
        "error_type": type(exc).__name__,
        "error_message": _redact(str(exc)),
        "traceback": _redact("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))),
    }


def record_error(
    source: str,
    *,
    exc: BaseException | None = None,
    message: str | None = None,
    severity: str = "error",
    context: dict[str, Any] | None = None,
    path: Path | None = None,
) -> None:
    """Append one redacted JSONL error record.

    This function is intentionally fail-closed for the caller: journal write
    failures are reported to stderr but never interrupt Jarvis.
    """
    if not _enabled():
        return

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "severity": severity,
        "source": source,
    }
    if message:
        record["message"] = _redact(message)
    if context:
        record["context"] = _redact(context)
    record.update(_exc_payload(exc))

    target = path or _default_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with _LOCK:
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception as write_exc:
        try:
            sys.stderr.write(
                f"[error-journal] WARN: no pude escribir {target}: "
                f"{type(write_exc).__name__}: {write_exc}\n"
            )
        except Exception:
            pass


def tail_errors(limit: int = 20, path: Path | None = None) -> list[dict[str, str]]:
    """Last N journal rows for the HUD. Path argument is ignored.

    Callers must not choose the file. Tracebacks and context stay on disk.
    """
    del path
    target = _default_path()
    if not target.is_file():
        return []
    capped = max(0, min(int(limit), 20))
    if capped == 0:
        return []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    rows: list[dict[str, str]] = []
    for line in lines[-capped:]:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        rows.append({
            "ts": str(raw.get("ts") or ""),
            "severity": str(raw.get("severity") or "error"),
            "source": str(raw.get("source") or ""),
            "message": str(raw.get("message") or raw.get("error_message") or ""),
        })
    return rows


__all__ = ["record_error", "tail_errors"]
