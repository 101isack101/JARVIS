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
from typing import Callable


class UiThread:
    def __init__(self) -> None:
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

    def submit(self, fn: Callable[[], None] | None) -> None:
        """Encola un callable para ejecutarlo en el main thread. Thread-safe;
        no ejecuta nada ni toca tkinter aquí."""
        if fn is None:
            return
        self._queue.put(fn)

    def drain(self) -> None:
        """Ejecuta todos los callables encolados. DEBE llamarse desde el main
        thread (el del mainloop). Un fallo en un callable no detiene el resto."""
        while True:
            try:
                fn = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn()
            except Exception:
                pass

    def pending(self) -> int:
        return self._queue.qsize()
