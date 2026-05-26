"""Hard kill-switch helpers."""

from __future__ import annotations

import os


def hard_exit(code: int = 130) -> None:
    """Terminate the Python process immediately, without cleanup waits."""
    os._exit(code)
