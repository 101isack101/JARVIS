"""
overlay/hotkeys.py - Listener global de hotkeys via libreria 'keyboard'.

Hotkeys:
  - Ctrl (hold)        -> PTT: presiona = empieza a hablar, suelta = termina
  - Ctrl+Shift+M       -> Toggle modo escucha libre (VAD continuous)
  - Ctrl+Shift+S       -> Capture screen completa (full virtual screen)
  - Ctrl+Alt+S         -> Capture region (snipping: arrastra rectangulo)
  - Ctrl+Alt+P         -> Pause acciones (Fase 4)
  - Ctrl+Alt+Q         -> Kill-switch: cierra Jarvis inmediatamente

La libreria 'keyboard' usa hooks low-level de Windows. Funciona globalmente
independiente del foco de la app. Atencion: requiere admin en algunas
configuraciones (no en la maquina de Isaac segun tests).

Threading: 'keyboard' tiene su propio thread interno. Los callbacks se llaman
desde ese thread, asi que el consumidor (jarvis.py) marshalla a tkinter con
root.after(0, ...) si va a tocar UI.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

import keyboard


@dataclass
class HotkeyCallbacks:
    on_ptt_press: Callable[[], None] = lambda: None
    on_ptt_release: Callable[[], None] = lambda: None
    on_toggle_listen_mode: Callable[[], None] = lambda: None
    on_capture_screen: Callable[[], None] = lambda: None
    on_capture_region: Callable[[], None] = lambda: None
    on_pause: Callable[[], None] = lambda: None
    on_kill: Callable[[], None] = lambda: None


class HotkeyListener:
    """Wrapper sobre keyboard library con polling para Ctrl PTT."""

    def __init__(self, cb: HotkeyCallbacks) -> None:
        self.cb = cb
        self._registered: list = []
        self._poll_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ctrl_was_pressed = False

    def start(self) -> None:
        """Registra hotkeys + arranca thread de polling para Ctrl PTT.

        El polling para Ctrl es necesario porque queremos detectar press y
        release del solo-Ctrl (no como modificador en combos). keyboard.add_hotkey
        no distingue bien.
        """
        # Combos: usar add_hotkey (suppress=False para no bloquear la tecla)
        self._registered.append(keyboard.add_hotkey(
            "ctrl+shift+m", self.cb.on_toggle_listen_mode, suppress=False,
        ))
        self._registered.append(keyboard.add_hotkey(
            "ctrl+shift+s", self.cb.on_capture_screen, suppress=False,
        ))
        self._registered.append(keyboard.add_hotkey(
            "ctrl+alt+s", self.cb.on_capture_region, suppress=False,
        ))
        self._registered.append(keyboard.add_hotkey(
            "ctrl+alt+p", self.cb.on_pause, suppress=False,
        ))
        self._registered.append(keyboard.add_hotkey(
            "ctrl+alt+q", self.cb.on_kill, suppress=False,
        ))

        # Polling para PTT con Ctrl puro (no combo)
        self._poll_thread = threading.Thread(
            target=self._poll_ctrl, name="JarvisHotkeyPoll", daemon=True,
        )
        self._poll_thread.start()

    def _poll_ctrl(self) -> None:
        """Detecta press/release del Ctrl puro (sin shift/alt).

        Periodo 25ms = latencia perceptible <50ms para PTT.
        """
        import time
        while not self._stop.is_set():
            ctrl_now = keyboard.is_pressed("ctrl")
            shift = keyboard.is_pressed("shift")
            alt = keyboard.is_pressed("alt")
            # Solo Ctrl puro (sin shift ni alt) cuenta como PTT
            ctrl_pure = ctrl_now and not shift and not alt
            if ctrl_pure and not self._ctrl_was_pressed:
                self._ctrl_was_pressed = True
                try:
                    self.cb.on_ptt_press()
                except Exception as exc:
                    print(f"[hotkeys] on_ptt_press error: {exc}")
            elif (not ctrl_now or shift or alt) and self._ctrl_was_pressed:
                self._ctrl_was_pressed = False
                try:
                    self.cb.on_ptt_release()
                except Exception as exc:
                    print(f"[hotkeys] on_ptt_release error: {exc}")
            time.sleep(0.025)

    def stop(self) -> None:
        self._stop.set()
        for h in self._registered:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self._registered.clear()
        if self._poll_thread:
            self._poll_thread.join(timeout=2.0)
            self._poll_thread = None


# Smoke test: imprime eventos por 10s
if __name__ == "__main__":
    import sys
    import time

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    cb = HotkeyCallbacks(
        on_ptt_press=lambda: print("[event] PTT press"),
        on_ptt_release=lambda: print("[event] PTT release"),
        on_toggle_listen_mode=lambda: print("[event] toggle listen mode"),
        on_capture_screen=lambda: print("[event] capture screen"),
        on_pause=lambda: print("[event] pause"),
        on_kill=lambda: print("[event] KILL — saliendo"),
    )

    listener = HotkeyListener(cb)
    listener.start()
    print("Listener activo 15s. Probar:")
    print("  - Mantener Ctrl (PTT press/release)")
    print("  - Ctrl+Shift+M (toggle libre)")
    print("  - Ctrl+Alt+Q (kill)")
    try:
        time.sleep(15)
    except KeyboardInterrupt:
        pass
    listener.stop()
    print("[OK] HotkeyListener smoke test passed")
