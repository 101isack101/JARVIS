"""Tests para overlay/scheduler.py (UiScheduler).

TDD — estos tests deben FALLAR hasta que se implemente overlay/scheduler.py.
Ciclo: red -> green -> commit.
"""

from __future__ import annotations

import threading
import time

import pytest


def test_scheduler_fires_callback():
    """after(delay_ms, fn) ejecuta fn aproximadamente tras delay_ms ms."""
    from overlay.scheduler import UiScheduler

    fired = threading.Event()
    sched = UiScheduler()
    sched.after(50, fired.set)
    assert fired.wait(timeout=1.0), "callback no disparo en 1s"
    sched.shutdown()


def test_scheduler_cancel_prevents_fire():
    """cancel(handle) evita que el callback se ejecute."""
    from overlay.scheduler import UiScheduler

    fired = threading.Event()
    sched = UiScheduler()
    handle = sched.after(200, fired.set)
    sched.cancel(handle)
    assert not fired.wait(timeout=0.4), "callback disparo pese a cancel"
    sched.shutdown()


def test_scheduler_thread_name():
    """El thread interno tiene el nombre pasado al constructor."""
    from overlay.scheduler import UiScheduler

    sched = UiScheduler(name="TestScheduler")
    names = [t.name for t in threading.enumerate()]
    assert "TestScheduler" in names, f"thread no encontrado: {names}"
    sched.shutdown()


def test_scheduler_exception_isolation():
    """Una excepcion en un callback no mata el scheduler."""
    from overlay.scheduler import UiScheduler

    second = threading.Event()
    sched = UiScheduler()
    sched.after(20, lambda: 1 / 0)  # lanza ZeroDivisionError
    sched.after(80, second.set)
    assert second.wait(timeout=1.0), "scheduler murio tras excepcion en callback"
    sched.shutdown()


def test_scheduler_ordering():
    """Multiples callbacks se ejecutan en orden cronologico."""
    from overlay.scheduler import UiScheduler

    order: list[int] = []
    lock = threading.Lock()
    done = threading.Event()

    sched = UiScheduler()
    sched.after(10, lambda: _append(order, lock, 1))
    sched.after(60, lambda: _append(order, lock, 2))
    sched.after(110, lambda: [_append(order, lock, 3), done.set()])

    assert done.wait(timeout=2.0)
    assert order == [1, 2, 3], f"orden incorrecto: {order}"
    sched.shutdown()


def _append(lst: list, lock: threading.Lock, val: int) -> None:
    with lock:
        lst.append(val)


def test_scheduler_shutdown_idempotent():
    """shutdown() puede llamarse varias veces sin error."""
    from overlay.scheduler import UiScheduler

    sched = UiScheduler()
    sched.shutdown()
    sched.shutdown()  # no debe lanzar


def test_scheduler_zero_delay():
    """after(0, fn) dispara el callback lo antes posible."""
    from overlay.scheduler import UiScheduler

    fired = threading.Event()
    sched = UiScheduler()
    sched.after(0, fired.set)
    assert fired.wait(timeout=0.5), "callback de delay=0 no disparo"
    sched.shutdown()
