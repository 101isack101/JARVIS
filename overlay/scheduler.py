"""Timer scheduler headless para modo web de JARVIS.

Reemplaza root.after() de tkinter en WebJarvisOverlay: mismo contrato
(delay_ms, callable) pero sin dependencia de Tcl ni event loop de tkinter.

Internamente: un thread daemon con heapq de (deadline, handle, fn).
Un threading.Condition permite dormir hasta el proximo vencimiento sin
busy-wait, pero despertarse inmediatamente cuando se encola un nuevo timer
con deadline menor que el actual proximo.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from typing import Callable


class UiScheduler:
    """Thread daemon con cola de timers. API identica al root.after() de Tk.

    Uso:
        sched = UiScheduler()
        handle = sched.after(500, my_fn)   # dispara en ~500ms
        sched.cancel(handle)               # cancela antes de disparar
        sched.shutdown()                   # detiene el thread daemon
    """

    def __init__(self, name: str = "JarvisUiScheduler") -> None:
        self._heap: list[tuple[float, int, Callable[[], None]]] = []
        self._cancelled: set[int] = set()
        self._counter = itertools.count()
        self._cv = threading.Condition(threading.Lock())
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def after(self, delay_ms: int | float, fn: Callable[[], None]) -> int:
        """Encola fn para ejecutarse tras delay_ms milisegundos.

        Returns:
            handle opaco para pasar a cancel().
        """
        handle = next(self._counter)
        deadline = time.monotonic() + max(0.0, delay_ms) / 1000.0
        with self._cv:
            heapq.heappush(self._heap, (deadline, handle, fn))
            self._cv.notify()
        return handle

    def cancel(self, handle: int) -> None:
        """Cancela un timer pendiente. Silencioso si ya disparo o no existe."""
        with self._cv:
            self._cancelled.add(handle)

    def shutdown(self, timeout_s: float = 2.0) -> None:
        """Detiene el thread daemon. Idempotente."""
        with self._cv:
            if self._stopping:
                return
            self._stopping = True
            self._cv.notify()
        self._thread.join(timeout=timeout_s)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while True:
            with self._cv:
                # Limpiar cancelados del heap (lazy removal al frente)
                while self._heap and self._heap[0][1] in self._cancelled:
                    _, handle, _ = heapq.heappop(self._heap)
                    self._cancelled.discard(handle)

                if self._stopping:
                    return

                if not self._heap:
                    # Sin timers: dormir indefinidamente hasta notify()
                    self._cv.wait()
                    continue

                deadline, handle, fn = self._heap[0]
                wait_s = deadline - time.monotonic()
                if wait_s > 0:
                    self._cv.wait(timeout=wait_s)
                    continue

                # El proximo vencio: sacarlo y ejecutar fuera del lock
                heapq.heappop(self._heap)
                if handle in self._cancelled:
                    self._cancelled.discard(handle)
                    continue

            # Ejecutar callback fuera del lock para no bloquear after/cancel
            try:
                fn()
            except Exception:
                pass  # excepcion aislada: el loop continua
