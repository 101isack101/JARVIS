"""Human-in-the-loop approvals for risky Jarvis actions."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class PendingAction:
    id: str
    risk: str
    title: str
    details: str
    created_at: float = field(default_factory=time.time)
    timeout_s: float = 30.0


class ApprovalBroker:
    """Thread-safe broker that blocks until Isaac approves/rejects in UI."""

    def __init__(self, timeout_s: float = 30.0) -> None:
        self.timeout_s = timeout_s
        self._handler: Callable[[PendingAction], None] | None = None
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, bool | None]] = {}

    def set_handler(self, handler: Callable[[PendingAction], None]) -> None:
        self._handler = handler

    def request(self, risk: str, title: str, details: str, timeout_s: float | None = None) -> bool:
        """Return True only if the UI explicitly approves before timeout."""
        action = PendingAction(
            id=uuid.uuid4().hex,
            risk=risk,
            title=title,
            details=details,
            timeout_s=timeout_s or self.timeout_s,
        )
        event = threading.Event()
        with self._lock:
            self._pending[action.id] = (event, None)

        if self._handler is None:
            self.resolve(action.id, False)
        else:
            try:
                self._handler(action)
            except Exception:
                self.resolve(action.id, False)

        event.wait(action.timeout_s)
        with self._lock:
            _, approved = self._pending.pop(action.id, (event, False))
        return approved is True

    def resolve(self, action_id: str, approved: bool) -> None:
        with self._lock:
            pending = self._pending.get(action_id)
            if pending is None:
                return
            event, _ = pending
            self._pending[action_id] = (event, bool(approved))
            event.set()


class AutoApprovalBroker:
    """Small test helper: deterministic approve/reject without UI."""

    def __init__(self, approve: bool) -> None:
        self.approve = approve
        self.requests: list[tuple[str, str, str]] = []

    def request(self, risk: str, title: str, details: str, timeout_s: float | None = None) -> bool:
        self.requests.append((risk, title, details))
        return self.approve
