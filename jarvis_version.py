"""Version metadata for JARVIS."""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().with_name("VERSION")


def _read_version() -> str:
    try:
        version = _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        version = "0.00"
    return version or "0.00"


JARVIS_VERSION = _read_version()
JARVIS_VERSION_LABEL = f"v{JARVIS_VERSION}"
