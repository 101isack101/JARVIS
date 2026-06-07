"""Marshalling thread-safe hacia el thread del mainloop de tkinter.

tkinter/Tcl es thread-afín: solo el thread que corre root.mainloop() puede
tocar widgets. Llamar root.after() desde otro thread (p.ej. un worker de
asyncio.to_thread durante una aprobación HITL) registra handlers Tcl en el
thread equivocado y, al reciclarse ese worker, Tcl aborta el proceso con
"Tcl_AsyncDelete: async handler deleted by the wrong thread".

UiThread evita eso: cualquier thread encola callables con submit() (operación
puramente Python, sin Tcl); un pump instalado en el main thread llama drain()
periódicamente y los ejecuta allí. Ningún worker toca Tcl.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable


class UiThread:
    def __init__(self, max_pending: int = 1000) -> None:
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue(maxsize=max_pending)
        self._latest: dict[str, Callable[[], None]] = {}
        self._latest_queued: set[str] = set()
        self._latest_lock = threading.Lock()

    def submit(self, fn: Callable[[], None] | None) -> None:
        """Encola un callable para ejecutarlo en el main thread. Thread-safe;
        no ejecuta nada ni toca tkinter aquí."""
        if fn is None:
            return
        try:
            self._queue.put_nowait(fn)
        except queue.Full:
            pass

    def submit_force(self, fn: Callable[[], None] | None) -> None:
        """Encola un callable critico, descartando trabajo viejo si hace falta."""
        if fn is None:
            return
        while True:
            try:
                self._queue.put_nowait(fn)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    return
                with self._latest_lock:
                    self._latest.clear()
                    self._latest_queued.clear()

    def submit_latest(self, key: str, fn: Callable[[], None] | None) -> None:
        """Encola solo la version mas reciente de un tipo de update."""
        if fn is None:
            return
        should_enqueue = False
        with self._latest_lock:
            self._latest[key] = fn
            if key not in self._latest_queued:
                self._latest_queued.add(key)
                should_enqueue = True
        if not should_enqueue:
            return

        def _run_latest() -> None:
            with self._latest_lock:
                latest = self._latest.pop(key, None)
                self._latest_queued.discard(key)
            if latest is not None:
                latest()

        try:
            self._queue.put_nowait(_run_latest)
        except queue.Full:
            with self._latest_lock:
                self._latest.pop(key, None)
                self._latest_queued.discard(key)

    def drain(self, max_callbacks: int | None = None) -> int:
        """Ejecuta todos los callables encolados. DEBE llamarse desde el main
        thread (el del mainloop). Un fallo en un callable no detiene el resto."""
        drained = 0
        while True:
            if max_callbacks is not None and drained >= max_callbacks:
                return drained
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return drained
            drained += 1
            try:
                fn()
            except Exception:
                pass

    def pending(self) -> int:
        return self._queue.qsize()
