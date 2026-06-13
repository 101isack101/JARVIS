# JARVIS Desktop UI — Tauri + React Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retirar tkinter del modo web de JARVIS, reconstruir la UI como app React (Vite + Tailwind) servida por el bridge Python existente, y empaquetarla en un shell nativo Tauri v2 que gestiona el ciclo de vida del backend Python como proceso hijo.

**Architecture:** El backend Python (Gemini Live + audio + tools) ya expone un bridge SSE/HTTP en `overlay/web_overlay.py` con el mismo contrato que el overlay tkinter. Este plan (1) hace ese bridge 100% headless reemplazando el `tk.Tk()` oculto por un scheduler de thread propio, (2) amplía el protocolo de eventos con feed de tools, telemetría de audio y latencia, (3) reemplaza la web UI vanilla por una app React con estética cyber-minimalista, y (4) añade un shell Tauri que lanza/supervisa el Python y carga `http://127.0.0.1:8765`. El overlay tkinter clásico (`JARVIS_UI=tk`) se conserva intacto como fallback (necesario para `WDA_EXCLUDEFROMCAPTURE` y el RegionSelector).

**Tech Stack:** Python 3.11 (H:\Python311, global, sin venv) · stdlib `http.server` SSE (se conserva, NO se migra a FastAPI — ver Decisiones) · React 18 + TypeScript + Vite + Tailwind v4 + framer-motion + react-markdown · Tauri v2 (Rust stable-msvc) · pytest.

**Working directory:** `c:\Users\Isaac\Desktop\PROYECTOS\JARVIS` (todos los paths relativos a esta raíz; los comandos asumen PowerShell salvo nota).

**Tests:** `$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest` (suite actual ~183+ verde).

---

## Decisiones de diseño (cerradas con Isaac 2026-06-11)

1. **Shell: Tauri v2 completo** (no Edge --app). Requiere instalar Rust + MSVC Build Tools (Etapa 4, en H: para no llenar C:).
2. **Frontend: rebuild React + Vite + Tailwind** en `ui/`, build a `overlay/web_dist/`. La web_ui vanilla actual queda como fallback hasta paridad y se retira en Etapa 5.
3. **Git: consolidar a main primero** (Etapa 0), rama de trabajo `feature/ui-tauri`.
4. **Alcance visual:** thought log de tools + telemetría (budget, ERLE, wake-word, latencia) + camera preview web + markdown con syntax highlighting + estética elegante dark premium.
5. **Transporte: se CONSERVA el bridge SSE + HTTP POST existente.** Ya tiene auth por token, aprobaciones HITL, throttling de audio y reconexión trivial (`EventSource` reconecta solo). Migrar a FastAPI/WebSocket añadiría deps (uvicorn) y reescritura sin ganancia funcional: los comandos cliente→servidor son raros y POST los cubre. YAGNI.
6. **tkinter NO se elimina del repo:** `overlay/window.py` (modo tk) queda intacto. Lo que se elimina es toda dependencia de tkinter cuando `JARVIS_UI=web`: el `tk.Tk()` oculto, `root.after`, y `CameraPreviewWindow`.
7. **Captura de región (Ctrl+Alt+S) en modo web:** degrada con gracia a captura de pantalla completa + aviso. El RegionSelector tkinter sigue disponible en modo tk. Reemplazarlo con ventana Tauri transparente queda FUERA de este plan (ver "Siguientes" al final).
8. **Reasoner sigue siendo `claude-sonnet-4-6`** — decisión canónica de Isaac, este plan no toca modelos.

## Mapa de archivos

| Acción | Path | Responsabilidad |
|---|---|---|
| Create | `overlay/scheduler.py` | `UiScheduler`: timers `after()` en thread propio, sin Tcl |
| Create | `tests/test_ui_scheduler.py` | Tests del scheduler |
| Modify | `overlay/web_overlay.py` | Headless (sin tk), watchdog supervisado, cámara por SSE, eventos nuevos |
| Create | `tests/test_web_overlay_headless.py` | Tests del overlay headless |
| Modify | `overlay/window.py` | Añadir `after()`, `record_tool_start/end`, `record_audio_telemetry`, `record_turn_latency` (wrappers/stubs) |
| Modify | `jarvis.py` | `overlay.after()` en vez de `overlay.root.after` (3 sitios + pump), guard de región, telemetría, tool feed genérico |
| Create | `ui/` (app Vite completa) | Front-end React |
| Create | `desktop/` (app Tauri) | Shell nativo + ciclo de vida del sidecar Python |
| Modify | `.env.example` | Vars nuevas (`JARVIS_SUPERVISED`, `JARVIS_WEB_UI_DIR`) |
| Modify | `overlay/web_ui/README.md` → `docs/WEB_UI.md` | Documentación del protocolo actualizado |

---

# ETAPA 0 — Consolidación git (housekeeping)

Estado actual: rama `feature/vision-camara` activa con ~25 paths sin commitear; `feature/continuidad-fase1` sin mergear a main; `main` desactualizado.

### Task 0.1: Commitear trabajo pendiente y consolidar ramas

- [ ] **Step 1: Revisar y commitear lo pendiente de vision-camara**

```powershell
git status --short
git add -A
git commit -m "chore(vision): consolidar trabajo pendiente de vision-camara antes de UI Tauri"
```

Si `git status --short` muestra archivos que claramente son basura temporal (p.ej. `*.tmp`, capturas de prueba), añadirlos a `.gitignore` antes del `git add -A`.

- [ ] **Step 2: Correr la suite completa**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
```

Expected: todo verde (≥183 passed). Si algo falla, arreglarlo ANTES de mergear (no mergear rojo a main).

- [ ] **Step 3: Verificar ancestría de continuidad-fase1**

```powershell
git log feature/vision-camara..feature/continuidad-fase1 --oneline
```

Expected (probable): salida vacía → vision-camara ya contiene todo el KAG y continuidad-fase1 es ancestro. Si hay commits únicos, mergearlos primero a vision-camara (`git merge feature/continuidad-fase1`) y resolver conflictos.

- [ ] **Step 4: Merge a main + push**

```powershell
git checkout main
git merge feature/vision-camara
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
git push origin main
```

Expected: merge fast-forward o limpio, suite verde, push OK.

- [ ] **Step 5: Crear rama de trabajo**

```powershell
git checkout -b feature/ui-tauri
```

---

# ETAPA 1 — Overlay web headless (retirar tkinter del modo web)

Hoy `WebJarvisOverlay` crea un `tk.Tk()` oculto que usa para: (a) mainloop en `run()`, (b) timers `root.after` (refresh de paneles cada 500ms, timeout de aprobaciones, abrir browser), (c) `CameraPreviewWindow` tkinter, (d) `jarvis.py` usa `overlay.root.after` para el UI pump, el flush de telemetría y el thinking watchdog, y `overlay.root` como parent del RegionSelector. Esta etapa elimina (a)-(d) en modo web.

### Task 1.1: UiScheduler — timers sin tkinter

**Files:**
- Create: `overlay/scheduler.py`
- Test: `tests/test_ui_scheduler.py`

- [ ] **Step 1: Escribir tests que fallan**

```python
# tests/test_ui_scheduler.py
"""Tests de overlay/scheduler.py — timers de UI sin tkinter."""
import threading
import time

from overlay.scheduler import UiScheduler


def test_after_fires_callback():
    s = UiScheduler()
    fired = threading.Event()
    s.after(10, fired.set)
    assert fired.wait(timeout=1.0), "callback no disparo en 1s"
    s.shutdown()


def test_after_zero_fires_immediately():
    s = UiScheduler()
    fired = threading.Event()
    s.after(0, fired.set)
    assert fired.wait(timeout=1.0)
    s.shutdown()


def test_cancel_prevents_callback():
    s = UiScheduler()
    fired = threading.Event()
    handle = s.after(50, fired.set)
    s.cancel(handle)
    assert not fired.wait(timeout=0.3), "callback disparo pese a cancel"
    s.shutdown()


def test_callbacks_run_in_scheduler_thread():
    s = UiScheduler(name="TestSched")
    names: list[str] = []
    done = threading.Event()

    def record():
        names.append(threading.current_thread().name)
        done.set()

    s.after(5, record)
    assert done.wait(timeout=1.0)
    assert names == ["TestSched"]
    s.shutdown()


def test_exception_in_callback_does_not_kill_loop():
    s = UiScheduler()
    fired = threading.Event()

    def boom():
        raise RuntimeError("boom")

    s.after(5, boom)
    s.after(20, fired.set)
    assert fired.wait(timeout=1.0), "el loop murio tras una excepcion"
    s.shutdown()


def test_ordering_respects_deadlines():
    s = UiScheduler()
    order: list[str] = []
    done = threading.Event()
    s.after(60, lambda: (order.append("late"), done.set()))
    s.after(10, lambda: order.append("early"))
    assert done.wait(timeout=1.0)
    assert order == ["early", "late"]
    s.shutdown()


def test_shutdown_is_idempotent_and_stops_pending():
    s = UiScheduler()
    fired = threading.Event()
    s.after(500, fired.set)
    s.shutdown()
    s.shutdown()  # no debe lanzar
    assert not fired.wait(timeout=0.7), "callback pendiente disparo tras shutdown"
```

- [ ] **Step 2: Verificar que fallan**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest tests/test_ui_scheduler.py -q
```

Expected: `ModuleNotFoundError: No module named 'overlay.scheduler'` (o ImportError).

- [ ] **Step 3: Implementar `overlay/scheduler.py`**

```python
"""Timers de UI sin tkinter.

UiScheduler reemplaza root.after() cuando JARVIS corre en modo web headless:
un solo thread daemon ejecuta los callbacks en orden de deadline. Mantiene la
propiedad clave del mainloop de tkinter que el resto del codigo asume: todos
los callbacks de UI corren serializados en UN solo thread (aqui, el del
scheduler), asi que el patron UiThread.drain() sigue siendo valido.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from typing import Callable


class UiScheduler:
    def __init__(self, name: str = "JarvisUiScheduler") -> None:
        self._heap: list[tuple[float, int, Callable[[], None]]] = []
        self._cancelled: set[int] = set()
        self._counter = itertools.count()
        self._cv = threading.Condition()
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()

    def after(self, delay_ms: int, fn: Callable[[], None]) -> int:
        """Programa fn() para dentro de delay_ms. Thread-safe. Devuelve handle
        cancelable. Mismo contrato que tkinter root.after(ms, fn)."""
        handle = next(self._counter)
        deadline = time.monotonic() + max(0, int(delay_ms)) / 1000.0
        with self._cv:
            if self._stopping:
                return handle
            heapq.heappush(self._heap, (deadline, handle, fn))
            self._cv.notify()
        return handle

    def cancel(self, handle: int) -> None:
        with self._cv:
            self._cancelled.add(handle)
            self._cv.notify()

    def shutdown(self, timeout_s: float = 2.0) -> None:
        with self._cv:
            if self._stopping:
                return
            self._stopping = True
            self._cv.notify()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=timeout_s)

    def _loop(self) -> None:
        while True:
            fn: Callable[[], None] | None = None
            with self._cv:
                while not self._stopping:
                    now = time.monotonic()
                    if self._heap and self._heap[0][0] <= now:
                        break
                    timeout = (self._heap[0][0] - now) if self._heap else None
                    self._cv.wait(timeout=timeout)
                if self._stopping:
                    return
                _, handle, fn = heapq.heappop(self._heap)
                if handle in self._cancelled:
                    self._cancelled.discard(handle)
                    continue
            try:
                fn()
            except Exception:
                # Un callback roto nunca mata el loop de UI (paridad con
                # el comportamiento defensivo del pump de tkinter).
                pass
```

- [ ] **Step 4: Verificar que pasan**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest tests/test_ui_scheduler.py -q
```

Expected: `7 passed`.

- [ ] **Step 5: Commit**

```powershell
git add overlay/scheduler.py tests/test_ui_scheduler.py
git commit -m "feat(overlay): UiScheduler - timers de UI sin tkinter"
```

### Task 1.2: Protocolo `overlay.after()` — desacoplar jarvis.py de overlay.root

`jarvis.py` usa `self.overlay.root.after(...)` en 4 sitios: `_install_ui_pump` (líneas ~1195 y ~1199), `_schedule_usage_flush` (~1222) y `_schedule_thinking_watchdog` (~1238). Se reemplazan por un método `after()` que ambos overlays implementan.

**Files:**
- Modify: `overlay/window.py` (clase `JarvisOverlay`)
- Modify: `jarvis.py:1185-1240` aprox.
- Test: `tests/test_web_overlay_headless.py` (se crea en Task 1.3; aquí solo el método tk)

- [ ] **Step 1: Añadir `after()` a `JarvisOverlay` en `overlay/window.py`**

Localizar la clase principal (`class JarvisOverlay`) y añadir junto a sus métodos públicos:

```python
    def after(self, delay_ms: int, fn) -> object:
        """Programa fn en el loop de UI. Contrato comun con WebJarvisOverlay."""
        return self.root.after(int(delay_ms), fn)
```

- [ ] **Step 2: Reemplazar los 4 usos en `jarvis.py`**

En `_install_ui_pump`, cambiar las DOS llamadas:

```python
# antes:  self.overlay.root.after(delay_ms, _pump)
# despues:
self.overlay.after(delay_ms, _pump)
```
```python
# antes:  self.overlay.root.after(self._UI_ACTIVE_POLL_MS, _pump)
# despues:
self.overlay.after(self._UI_ACTIVE_POLL_MS, _pump)
```

En `_schedule_usage_flush`:

```python
# antes:  self.overlay.root.after(USAGE_FLUSH_MS, self._schedule_usage_flush)
# despues:
self.overlay.after(USAGE_FLUSH_MS, self._schedule_usage_flush)
```

En `_schedule_thinking_watchdog`:

```python
# antes:  self.overlay.root.after(THINKING_WATCHDOG_MS, self._schedule_thinking_watchdog)
# despues:
self.overlay.after(THINKING_WATCHDOG_MS, self._schedule_thinking_watchdog)
```

Verificar que no quedan más usos fuera del RegionSelector (que se trata en Task 1.4):

```powershell
Select-String -Path jarvis.py -Pattern "overlay\.root"
```

Expected: solo la línea del RegionSelector (`RegionSelector(self.overlay.root, ...)`).

- [ ] **Step 3: Suite completa (regresión modo tk)**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
```

Expected: verde. (El modo tk sigue funcionando: `after()` delega a `root.after`.)

- [ ] **Step 4: Commit**

```powershell
git add overlay/window.py jarvis.py
git commit -m "refactor(overlay): protocolo overlay.after() en vez de overlay.root.after"
```

### Task 1.3: WebJarvisOverlay headless

Eliminar `tk.Tk()` de `overlay/web_overlay.py`: scheduler para timers, `run()` bloqueante con Event, cámara por SSE.

**Files:**
- Modify: `overlay/web_overlay.py`
- Test: `tests/test_web_overlay_headless.py`

- [ ] **Step 1: Escribir tests que fallan**

```python
# tests/test_web_overlay_headless.py
"""WebJarvisOverlay debe funcionar sin tkinter (modo headless para Tauri)."""
import json
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker


@pytest.fixture()
def overlay(monkeypatch):
    # Puerto 0 = efimero; no abrir browser; no supervisado.
    monkeypatch.setenv("JARVIS_WEB_UI_PORT", "0")
    monkeypatch.setenv("JARVIS_WEB_UI_OPEN_BROWSER", "false")
    monkeypatch.delenv("JARVIS_SUPERVISED", raising=False)
    from overlay.web_overlay import WebJarvisOverlay

    ov = WebJarvisOverlay(TokenTracker(), BudgetGate())
    yield ov
    ov.close()


def test_source_has_no_tkinter_import():
    src = (Path(__file__).resolve().parent.parent / "overlay" / "web_overlay.py").read_text(
        encoding="utf-8"
    )
    assert "import tkinter" not in src
    assert "from tkinter" not in src
    assert "CameraPreviewWindow" not in src


def test_overlay_has_no_root_attribute(overlay):
    assert getattr(overlay, "root", None) is None


def test_state_endpoint_serves_snapshot(overlay):
    with urllib.request.urlopen(f"{overlay.url}state", timeout=3) as resp:
        snap = json.loads(resp.read().decode("utf-8"))
    assert snap["state"] == "idle"
    assert "uiToken" in snap


def test_after_runs_callback(overlay):
    fired = threading.Event()
    overlay.after(10, fired.set)
    assert fired.wait(timeout=1.0)


def test_run_blocks_until_close(overlay):
    finished = threading.Event()

    def runner():
        overlay.run()
        finished.set()

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    time.sleep(0.15)
    assert not finished.is_set(), "run() retorno sin close()"
    overlay.close()
    assert finished.wait(timeout=2.0), "run() no desbloqueo tras close()"


def test_approval_timeout_auto_rejects(overlay):
    decisions: list[tuple[str, bool]] = []
    action = SimpleNamespace(
        id="a1", risk="low", title="test", details="d", timeout_s=0.1
    )
    overlay.show_approval(action, lambda aid, ok: decisions.append((aid, ok)))
    time.sleep(0.5)
    assert decisions == [("a1", False)], "timeout no auto-rechazo la aprobacion"


def test_camera_methods_emit_without_tk(overlay):
    # No debe lanzar aunque no haya ventana tkinter.
    overlay.set_camera_active(True)
    frame = SimpleNamespace(jpeg_bytes=b"\xff\xd8fakejpeg\xff\xd9")
    overlay.update_camera_preview(frame)
    overlay.set_camera_focus((1, 2, 3, 4), "obj")
    overlay.set_camera_active(False)
```

- [ ] **Step 2: Verificar que fallan**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest tests/test_web_overlay_headless.py -q
```

Expected: FAIL — `test_source_has_no_tkinter_import` y `test_overlay_has_no_root_attribute` fallan seguro; otros pueden fallar por `root.after` inexistente según orden.

- [ ] **Step 3: Modificar `overlay/web_overlay.py`**

Cambios exactos (el resto del archivo no se toca):

3a. Imports — quitar `import tkinter as tk`, añadir:

```python
import base64
from overlay.scheduler import UiScheduler
```

3b. En `__init__`, REEMPLAZAR el bloque tkinter:

```python
        # antes (ELIMINAR):
        # self.root = tk.Tk()
        # self.root.withdraw()
        # self.root.title(f"JARVIS {JARVIS_VERSION_LABEL} Web UI")
        # from overlay.camera_preview import CameraPreviewWindow
        # self._camera_preview = CameraPreviewWindow(self.root)

        # despues:
        self.root = None  # contrato: modo web no tiene Tk; jarvis.py lo chequea
        self._scheduler = UiScheduler(name="JarvisWebUiSched")
        self._stop_event = threading.Event()
        self._camera_active = False
        self._last_camera_emit_ts = 0.0
        self._supervised = _env_truthy("JARVIS_SUPERVISED", False)
        self._had_client = False
        self._last_client_seen = time.monotonic()
```

3c. En `__init__`, reemplazar los dos `self.root.after(...)` finales:

```python
        # antes: self.root.after(REFRESH_MS, self._refresh_runtime_panels)
        self.after(REFRESH_MS, self._refresh_runtime_panels)
        if self._supervised:
            self.after(10_000, self._watchdog_check)
        # antes: self.root.after(250, lambda: webbrowser.open(self.url))
        if _env_truthy("JARVIS_WEB_UI_OPEN_BROWSER", True) and not self._supervised:
            self.after(250, lambda: webbrowser.open(self.url))
```

3d. Añadir métodos `after` y `_watchdog_check`:

```python
    def after(self, delay_ms: int, fn: Callable[[], None]) -> int:
        """Contrato comun con JarvisOverlay (tk): programa fn en el loop de UI."""
        return self._scheduler.after(delay_ms, fn)

    def _watchdog_check(self) -> None:
        """En modo supervisado (Tauri), si la ventana desaparece >60s, apagarse
        para no dejar un backend zombi con el microfono abierto."""
        if self._closed:
            return
        with self._clients_lock:
            n = len(self._clients)
        if n > 0:
            self._last_client_seen = time.monotonic()
        elif self._had_client and time.monotonic() - self._last_client_seen > 60.0:
            print("[web-ui] supervisado y sin clientes 60s; cerrando JARVIS")
            self.close()
            return
        self.after(10_000, self._watchdog_check)
```

3e. En `register_client`, marcar primer cliente (añadir una línea):

```python
    def register_client(self) -> queue.Queue[str]:
        client: queue.Queue[str] = queue.Queue(maxsize=256)
        with self._clients_lock:
            self._clients.add(client)
        self._had_client = True
        return client
```

3f. `show_approval` — reemplazar la última línea (`self.root.after(...)`):

```python
        self.after(int(action.timeout_s * 1000), lambda: self.resolve_web_approval(action.id, False))
```

3g. `handle_web_command` — reemplazar los tres `self.root.after(0, ...)` por `self.after(0, ...)` (mismo callable).

3h. `_refresh_runtime_panels` — en el `finally`, reemplazar:

```python
        finally:
            try:
                self.after(REFRESH_MS, self._refresh_runtime_panels)
            except Exception:
                pass
```

3i. Cámara — REEMPLAZAR la sección `# ---- Camera preview ----` completa:

```python
    # ---- Camera preview (sin tkinter: frames JPEG via SSE) ----

    CAMERA_EMIT_MIN_INTERVAL_S = 0.25  # tope 4 fps hacia el browser

    def set_camera_active(self, active: bool) -> None:
        self._camera_active = bool(active)
        self.emit("setCameraActive", self._camera_active)
        if active:
            self.log_event("CAMARA ACTIVA (modo vision)", "warn")
        else:
            self.log_event("Camara apagada", "ok")

    def update_camera_preview(self, frame) -> None:
        now = time.monotonic()
        if now - self._last_camera_emit_ts < self.CAMERA_EMIT_MIN_INTERVAL_S:
            return
        self._last_camera_emit_ts = now
        self.emit("cameraFrame", base64.b64encode(frame.jpeg_bytes).decode("ascii"))

    def set_camera_focus(self, box_px, label: str = "") -> None:
        self.emit(
            "cameraFocus",
            {"box": list(box_px) if box_px else None, "label": label},
        )
```

3j. `close()` — reemplazar el cuerpo para soltar scheduler y run():

```python
    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._on_close()
        finally:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            try:
                self._scheduler.shutdown()
            except Exception:
                pass
            self._stop_event.set()
```

3k. `run()` — reemplazar:

```python
    def run(self) -> None:
        print(f"[web-ui] JARVIS disponible en {self.url}")
        self._stop_event.wait()
```

3l. En `snapshot()`, añadir al dict (después de `"budget"`):

```python
            "cameraActive": self._camera_active,
```

- [ ] **Step 4: Verificar tests + suite completa**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest tests/test_web_overlay_headless.py -q
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
```

Expected: nuevos 8 passed; suite completa verde.

- [ ] **Step 5: Commit**

```powershell
git add overlay/web_overlay.py tests/test_web_overlay_headless.py
git commit -m "feat(overlay): WebJarvisOverlay headless - sin tkinter, scheduler propio, camara por SSE, watchdog supervisado"
```

### Task 1.4: Guard del RegionSelector en modo web

`_show_region_selector` en `jarvis.py` (~línea 819) hace `RegionSelector(self.overlay.root, ...)`. Con `root=None` en web, debe degradar a captura completa.

**Files:**
- Modify: `jarvis.py:819-827` aprox.

- [ ] **Step 1: Reemplazar `_show_region_selector`**

```python
    def _show_region_selector(self) -> None:
        """Crea y muestra el RegionSelector. DEBE correr en main thread (modo tk).

        En modo web (overlay.root is None) no hay Tk para el selector: degradamos
        a captura de pantalla completa con aviso. El selector nativo para la UI
        web queda como mejora futura (ventana Tauri transparente)."""
        root = getattr(self.overlay, "root", None)
        if root is None:
            self._log("[REGION] selector no disponible en UI web; capturando pantalla completa")
            self._tk(lambda: self.overlay.log_event(
                "Region no disponible en UI web; captura completa", "warn"
            ))
            self._on_capture_screen()
            return
        try:
            RegionSelector(
                root,
                on_select=self._on_region_selected,
            ).show()
        except Exception as exc:
            self._log(f"[REGION] no pude mostrar selector: {type(exc).__name__}: {exc}")
```

- [ ] **Step 2: Suite + smoke manual del modo web**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
```

Expected: verde.

Smoke manual (primera validación E2E del headless — IMPORTANTE):

```powershell
$env:JARVIS_UI="web"; & "H:\Python311\python.exe" jarvis.py
```

Expected: arranca sin ventana tkinter; imprime `[web-ui] JARVIS disponible en http://127.0.0.1:8765/`; el browser abre la UI vanilla actual y funciona (estado, transcript, PTT con Ctrl). `Ctrl+Alt+Q` cierra limpio. Si Isaac no está para hablar, basta verificar arranque + UI + cierre.

- [ ] **Step 3: Commit**

```powershell
git add jarvis.py
git commit -m "feat(capture): degradar region a captura completa en UI web sin Tk"
```

---

# ETAPA 2 — Protocolo de eventos ampliado (thought log + telemetría)

Los hooks ya existen en `jarvis.py`: `_on_tool_start/_on_tool_end` (TODOS los tools, líneas ~1043-1064), el log de barge-in con `self._aec_erle_peak` / `self._wakeword_peak` (~1117-1121), y `self.latency.format_turn(turn)` en `_on_turn_complete` (~1071). Esta etapa los expone como eventos SSE.

### Task 2.1: Feed genérico de tools (`agentToolStart`/`agentToolEnd`)

**Files:**
- Modify: `overlay/web_overlay.py`
- Modify: `overlay/window.py`
- Modify: `jarvis.py:1051,1061`
- Test: `tests/test_web_overlay_events.py`

- [ ] **Step 1: Tests que fallan**

```python
# tests/test_web_overlay_events.py
"""Eventos nuevos del bridge web: tool feed, telemetria de audio, latencia."""
import pytest

from telemetry.budgets import BudgetGate
from telemetry.tracker import TokenTracker


@pytest.fixture()
def overlay(monkeypatch):
    monkeypatch.setenv("JARVIS_WEB_UI_PORT", "0")
    monkeypatch.setenv("JARVIS_WEB_UI_OPEN_BROWSER", "false")
    from overlay.web_overlay import WebJarvisOverlay

    ov = WebJarvisOverlay(TokenTracker(), BudgetGate())
    yield ov
    ov.close()


def test_record_tool_start_and_end_tracks_any_tool(overlay):
    overlay.record_tool_start("ask_claude_deep", {"prompt": "hola"})
    events = overlay.agent_events
    assert events[-1]["name"] == "ask_claude_deep"
    assert events[-1]["status"] == "running"

    overlay.record_tool_end("ask_claude_deep", 1234.5, True, {"ok": True})
    events = overlay.agent_events
    assert events[-1]["status"] == "ok"
    assert events[-1]["elapsedMs"] == pytest.approx(1234.5)


def test_record_tool_delegates_to_memory_panel_for_memory_tools(overlay):
    overlay.record_tool_start("jarvis_recall", {"query": "x"})
    assert overlay.memory_events[-1]["status"] == "running"
    overlay.record_tool_end("jarvis_recall", 10.0, True, {"found": 1})
    assert overlay.memory_events[-1]["status"] == "ok"


def test_record_audio_telemetry_stored_and_in_snapshot(overlay):
    overlay.record_audio_telemetry({"erlePeakDb": 24.3, "wakewordPeak": 0.41})
    snap = overlay.snapshot()
    assert snap["audioTelemetry"]["erlePeakDb"] == 24.3


def test_record_turn_latency_keeps_recent_lines(overlay):
    for i in range(25):
        overlay.record_turn_latency(f"turn {i}: ttfb=500ms")
    snap = overlay.snapshot()
    assert len(snap["latency"]) == 20
    assert snap["latency"][-1].endswith("turn 24: ttfb=500ms") or "turn 24" in snap["latency"][-1]


def test_snapshot_includes_agent_events(overlay):
    overlay.record_tool_start("spotify_play", {"track": "a"})
    snap = overlay.snapshot()
    assert any(e["name"] == "spotify_play" for e in snap["agentEvents"])
```

- [ ] **Step 2: Verificar que fallan**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest tests/test_web_overlay_events.py -q
```

Expected: `AttributeError: ... record_tool_start` etc.

- [ ] **Step 3: Implementar en `overlay/web_overlay.py`**

3a. En `__init__` (junto a `self._memory_events`):

```python
        self._agent_events: list[dict[str, Any]] = []
        self._audio_telemetry: dict[str, Any] = {}
        self._latency_lines: list[str] = []
```

3b. Property + métodos nuevos (junto a `record_memory_tool_start`):

```python
    @property
    def agent_events(self) -> list[dict[str, Any]]:
        return self._agent_events

    def record_tool_start(self, name: str, args: dict[str, Any] | None = None) -> None:
        """Feed generico de tools (thought log). Cubre TODAS las tools; las de
        memoria ademas alimentan el panel de memoria existente."""
        args = args or {}
        entry = {
            "stamp": time.strftime("%H:%M:%S"),
            "name": name,
            "summary": self._tool_args_summary(name, args),
            "status": "running",
            "detail": "En progreso",
            "elapsedMs": None,
        }
        self._agent_events.append(entry)
        self._agent_events = self._agent_events[-150:]
        self.emit("agentToolStart", entry)
        self.record_memory_tool_start(name, args)

    def record_tool_end(
        self, name: str, elapsed_ms: float, ok: bool, response: Any = None
    ) -> None:
        status = "ok" if ok and not self._response_failed(response) else "error"
        entry = {
            "stamp": time.strftime("%H:%M:%S"),
            "name": name,
            "summary": "",
            "status": status,
            "detail": self._memory_response_summary(name, response),
            "elapsedMs": elapsed_ms,
        }
        for idx in range(len(self._agent_events) - 1, -1, -1):
            existing = self._agent_events[idx]
            if existing.get("name") == name and existing.get("status") == "running":
                entry["summary"] = existing.get("summary", "")
                self._agent_events[idx] = entry
                break
        else:
            self._agent_events.append(entry)
        self._agent_events = self._agent_events[-150:]
        self.emit("agentToolEnd", entry)
        self.record_memory_tool_end(name, elapsed_ms, ok, response)

    def _tool_args_summary(self, name: str, args: dict[str, Any]) -> str:
        if self._is_memory_tool(name):
            return self._memory_args_summary(name, args)
        try:
            rendered = json.dumps(args, ensure_ascii=False)
        except Exception:
            rendered = str(args)
        return self._clip(rendered, 90)

    def record_audio_telemetry(self, payload: dict[str, Any]) -> None:
        self._audio_telemetry = {**payload, "stamp": time.strftime("%H:%M:%S")}
        self.emit("audioTelemetry", self._audio_telemetry)

    def record_turn_latency(self, line: str) -> None:
        line = " ".join((line or "").split())
        if not line:
            return
        self._latency_lines.append(line)
        self._latency_lines = self._latency_lines[-20:]
        self.emit("turnLatency", line)
```

3c. En `snapshot()`, añadir:

```python
            "agentEvents": self._agent_events[-30:],
            "audioTelemetry": self._audio_telemetry,
            "latency": self._latency_lines,
```

- [ ] **Step 4: Wrappers en `overlay/window.py` (modo tk no rompe)**

En `class JarvisOverlay`, añadir:

```python
    def record_tool_start(self, name: str, args: dict | None = None) -> None:
        self.record_memory_tool_start(name, args or {})

    def record_tool_end(self, name: str, elapsed_ms: float, ok: bool, response=None) -> None:
        self.record_memory_tool_end(name, elapsed_ms, ok, response)

    def record_audio_telemetry(self, payload: dict) -> None:
        return None  # el overlay tk no tiene panel de telemetria de audio

    def record_turn_latency(self, line: str) -> None:
        return None
```

(Si `JarvisOverlay` no tiene `record_memory_tool_start/end`, delegar a `return None` igual que los stubs — verificar con `Select-String -Path overlay/window.py -Pattern "record_memory_tool"`.)

- [ ] **Step 5: Cambiar call-sites en `jarvis.py`**

En `_on_tool_start` (~1051): `record_memory_tool_start` → `record_tool_start`.
En `_on_tool_end` (~1061): `record_memory_tool_end` → `record_tool_end`.

- [ ] **Step 6: Tests + suite**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest tests/test_web_overlay_events.py -q
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
```

Expected: 5 passed nuevos; suite verde.

- [ ] **Step 7: Commit**

```powershell
git add overlay/web_overlay.py overlay/window.py jarvis.py tests/test_web_overlay_events.py
git commit -m "feat(events): feed generico de tools + telemetria de audio + latencia en bridge web"
```

### Task 2.2: Emitir telemetría desde jarvis.py

**Files:**
- Modify: `jarvis.py` (~1071 y ~1117-1121)

- [ ] **Step 1: Latencia por turno**

En `_on_turn_complete`, justo después de `self._log(self.latency.format_turn(turn))` (dentro del `if turn is not None:`):

```python
        if turn is not None:
            line = self.latency.format_turn(turn)
            self._log(line)
            self._tk(lambda l=line: self.overlay.record_turn_latency(l))
```

(Reemplaza el `self._log(self.latency.format_turn(turn))` existente para no formatear dos veces.)

- [ ] **Step 2: Telemetría de audio del barge-in**

Localizar el bloque del log `[BARGE-IN] wake-word peak=` (~línea 1117):

```powershell
Select-String -Path jarvis.py -Pattern "wake-word peak="
```

Justo DESPUÉS de ese bloque de log existente, añadir (mismo nivel de indentación que el `if` que lo contiene):

```python
        if self._libre_speaking or self.mode == "LIBRE":
            payload: dict = {}
            if self._aec is not None:
                payload["erlePeakDb"] = round(float(self._aec_erle_peak), 1)
            if self._wakeword is not None:
                payload["wakewordPeak"] = round(float(self._wakeword_peak), 2)
            if payload:
                self._tk(lambda p=payload: self.overlay.record_audio_telemetry(p))
```

Nota: leer las variables ANTES de que el código existente las resetee (`self._wakeword_peak = 0.0` está cerca, ~línea 1014 y en el flujo del turno). Colocar la emisión inmediatamente después del log que ya imprime esos mismos valores garantiza que siguen vigentes.

- [ ] **Step 3: Suite + commit**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
git add jarvis.py
git commit -m "feat(telemetry): emitir latencia por turno y ERLE/wake-word al overlay web"
```

---

# ETAPA 3 — React UI (Vite + Tailwind v4 + framer-motion)

App React en `ui/`, build a `overlay/web_dist/`. El bridge Python sirve `web_dist/` si existe; si no, cae a la `web_ui/` vanilla (transición segura). En dev, Vite proxyea `/events`, `/state`, `/approval`, `/command` al backend en 8765, así se itera la UI con hot-reload contra un JARVIS vivo.

**Dirección de arte (cyber-minimalista, dark premium):** fondo `#0d0f12`, paneles glassmorphism (`bg-white/[0.03]` + `backdrop-blur` + borde `white/10`), acento cian `#22d3ee` con glow sutil (`shadow-[0_0_18px_rgba(34,211,238,0.25)]`), tipografías self-hosted: Space Grotesk (títulos), Inter (UI), JetBrains Mono (telemetría/código). Animaciones framer-motion discretas: el movimiento siempre justifica una acción.

**Layout bento (3 columnas):**

```
┌──────────────────────── StatusBar (modo, conexión, versión, privacidad) ───────────────────────┐
│ ┌── Core ──────────┐ ┌── Transcript (markdown + código) ─────────┐ ┌── ThoughtLog (tools) ───┐ │
│ │ núcleo reactivo  │ │                                           │ │ feed agentTool*         │ │
│ │ 5 estados        │ │                                           │ ├── CameraPanel ──────────┤ │
│ ├── Telemetry ─────┤ │                                           │ │ (visible solo activo)   │ │
│ │ budgets + ERLE   │ │                                           │ │                         │ │
│ │ + latencia       │ └───────────────────────────────────────────┘ └─────────────────────────┘ │
│ └──────────────────┘            ApprovalModal (overlay flotante, glow por riesgo)              │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Task 3.1: Scaffold del proyecto Vite

**Files:**
- Create: `ui/` (scaffold), `ui/vite.config.ts`, `ui/.gitignore`

- [ ] **Step 1: Crear proyecto e instalar deps**

```powershell
cd c:\Users\Isaac\Desktop\PROYECTOS\JARVIS
npm create vite@latest ui -- --template react-ts
cd ui
npm install
npm install tailwindcss @tailwindcss/vite framer-motion react-markdown remark-gfm rehype-highlight highlight.js @fontsource/inter @fontsource/space-grotesk @fontsource/jetbrains-mono
```

Expected: scaffold creado, deps instaladas sin errores (Node v24 ya instalado).

- [ ] **Step 2: Configurar `ui/vite.config.ts`**

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Backend bridge de JARVIS (overlay/web_overlay.py)
const BRIDGE = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../overlay/web_dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/events": { target: BRIDGE, changeOrigin: true },
      "/state": { target: BRIDGE, changeOrigin: true },
      "/approval": { target: BRIDGE, changeOrigin: true },
      "/command": { target: BRIDGE, changeOrigin: true },
    },
  },
});
```

- [ ] **Step 3: Verificar que `ui/.gitignore` incluye `node_modules` y añadir `overlay/web_dist/` al `.gitignore` raíz**

En `.gitignore` del repo (raíz JARVIS), añadir línea:

```
overlay/web_dist/
ui/node_modules/
```

- [ ] **Step 4: Commit**

```powershell
git add ui .gitignore
git commit -m "feat(ui): scaffold Vite + React + Tailwind para la nueva UI"
```

### Task 3.2: Theme + tipos + bridge hook

**Files:**
- Replace: `ui/src/index.css`
- Create: `ui/src/types.ts`, `ui/src/useJarvis.ts`
- Replace: `ui/src/main.tsx`, `ui/index.html`
- Delete: `ui/src/App.css`, `ui/src/assets/` (basura del template)

- [ ] **Step 1: `ui/index.html`**

```html
<!doctype html>
<html lang="es" class="dark">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>JARVIS</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: `ui/src/index.css` (theme tokens)**

```css
@import "tailwindcss";
@import "@fontsource/inter/400.css";
@import "@fontsource/inter/500.css";
@import "@fontsource/inter/600.css";
@import "@fontsource/space-grotesk/500.css";
@import "@fontsource/space-grotesk/700.css";
@import "@fontsource/jetbrains-mono/400.css";
@import "@fontsource/jetbrains-mono/500.css";
@import "highlight.js/styles/github-dark.css";

@theme {
  --color-bg: #0d0f12;
  --color-panel: rgba(255, 255, 255, 0.03);
  --color-edge: rgba(255, 255, 255, 0.08);
  --color-accent: #22d3ee;
  --color-accent-dim: rgba(34, 211, 238, 0.25);
  --color-warn: #fbbf24;
  --color-danger: #f87171;
  --color-ok: #34d399;
  --font-display: "Space Grotesk", sans-serif;
  --font-body: "Inter", sans-serif;
  --font-mono: "JetBrains Mono", monospace;
}

html, body, #root { height: 100%; }
body {
  background: var(--color-bg);
  color: rgba(255, 255, 255, 0.86);
  font-family: var(--font-body);
  overflow: hidden;
}

.glass {
  background: var(--color-panel);
  border: 1px solid var(--color-edge);
  border-radius: 1rem;
  backdrop-filter: blur(14px);
}
.glow-accent { box-shadow: 0 0 18px var(--color-accent-dim); }
.mono { font-family: var(--font-mono); }

/* scrollbars discretos */
*::-webkit-scrollbar { width: 6px; height: 6px; }
*::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 3px; }
*::-webkit-scrollbar-track { background: transparent; }
```

- [ ] **Step 3: `ui/src/types.ts`**

```ts
export type CoreState = "idle" | "listening" | "thinking" | "speaking" | "blocked";

export interface ProviderBudget {
  provider: string;
  spentUsd: number;
  limitUsd: number;
  pct: number;
  status: string;
  blocked: boolean;
  tokens: number;
  tokensLabel: string;
  label: string;
}

export interface Budget {
  period: string;
  hardStop: boolean;
  gemini: ProviderBudget;
  claude: ProviderBudget;
  totalUsd: number;
}

export interface AgentEvent {
  stamp: string;
  name: string;
  summary: string;
  status: "running" | "ok" | "error";
  detail: string;
  elapsedMs: number | null;
}

export interface LogLine { stamp: string; level: string; message: string }

export interface Approval {
  id: string;
  risk: string;
  title: string;
  details: string;
  timeout_s: number;
}

export interface AudioTelemetry {
  erlePeakDb?: number;
  wakewordPeak?: number;
  stamp?: string;
}

export interface JarvisState {
  version: string;
  uiToken: string;
  state: CoreState;
  mode: "PTT" | "LIBRE";
  connection: { status: string; detail: string };
  privacy: string;
  input: string;
  output: string;
  events: LogLine[];
  agentEvents: AgentEvent[];
  memory: { ok: number; active: number; error: number };
  budget: Budget | null;
  audioTelemetry: AudioTelemetry;
  latency: string[];
  audioLevel: number;
  approval: Approval | null;
  cameraActive: boolean;
  cameraFrame: string | null; // base64 jpeg
  cameraFocus: { box: number[] | null; label: string } | null;
  connected: boolean; // SSE conectado
}
```

- [ ] **Step 4: `ui/src/useJarvis.ts` (cliente del bridge)**

```ts
import { useEffect, useReducer, useRef, useCallback } from "react";
import type { JarvisState } from "./types";

const initial: JarvisState = {
  version: "", uiToken: "", state: "idle", mode: "PTT",
  connection: { status: "connecting", detail: "" }, privacy: "",
  input: "", output: "", events: [], agentEvents: [],
  memory: { ok: 0, active: 0, error: 0 }, budget: null,
  audioTelemetry: {}, latency: [], audioLevel: 0.05,
  approval: null, cameraActive: false, cameraFrame: null, cameraFocus: null,
  connected: false,
};

type Cmd = { command: string; args: unknown[] };

function reducer(s: JarvisState, cmd: Cmd): JarvisState {
  const a = cmd.args as never[];
  switch (cmd.command) {
    case "snapshot": {
      const snap = cmd.args[0] as Record<string, unknown>;
      return {
        ...s,
        ...{
          version: snap.version as string,
          uiToken: snap.uiToken as string,
          state: snap.state as JarvisState["state"],
          mode: snap.mode as JarvisState["mode"],
          connection: snap.connection as JarvisState["connection"],
          privacy: snap.privacy as string,
          input: (snap.inputTranscript as string) ?? "",
          output: (snap.outputTranscript as string) ?? "",
          events: (snap.events as JarvisState["events"]) ?? [],
          agentEvents: (snap.agentEvents as JarvisState["agentEvents"]) ?? [],
          memory: snap.memory as JarvisState["memory"],
          budget: (snap.budget as JarvisState["budget"]) ?? null,
          audioTelemetry: (snap.audioTelemetry as JarvisState["audioTelemetry"]) ?? {},
          latency: (snap.latency as string[]) ?? [],
          cameraActive: Boolean(snap.cameraActive),
          connected: true,
        },
      };
    }
    case "setState": return { ...s, state: a[0] };
    case "setMode": return { ...s, mode: a[0] };
    case "setConnectionStatus": return { ...s, connection: { status: a[0], detail: a[1] ?? "" } };
    case "appendInput": return { ...s, input: (s.input + " " + a[0]).slice(-12000) };
    case "appendOutput": return { ...s, output: (s.output + a[0]).slice(-16000) };
    case "clearTranscripts": return { ...s, input: "", output: "" };
    case "logEvent": {
      const line = { stamp: new Date().toTimeString().slice(0, 5), level: a[1] ?? "info", message: a[0] };
      return { ...s, events: [...s.events.slice(-49), line] };
    }
    case "feedAudioLevel": return { ...s, audioLevel: a[0] };
    case "showApproval": return { ...s, approval: a[0] };
    case "hideApproval": return { ...s, approval: null };
    case "updateMemoryStats": return { ...s, memory: a[0] };
    case "updateBudget": return { ...s, budget: a[0] };
    case "agentToolStart":
    case "agentToolEnd": {
      const ev = a[0] as JarvisState["agentEvents"][number];
      const rest = cmd.command === "agentToolEnd"
        ? s.agentEvents.filter((e) => !(e.name === ev.name && e.status === "running"))
        : s.agentEvents;
      return { ...s, agentEvents: [...rest.slice(-149), ev] };
    }
    case "audioTelemetry": return { ...s, audioTelemetry: a[0] };
    case "turnLatency": return { ...s, latency: [...s.latency.slice(-19), a[0]] };
    case "setCameraActive": return { ...s, cameraActive: a[0], cameraFrame: a[0] ? s.cameraFrame : null };
    case "cameraFrame": return { ...s, cameraFrame: a[0] };
    case "cameraFocus": return { ...s, cameraFocus: a[0] };
    case "sseDown": return { ...s, connected: false };
    default: return s; // toggleCompact y comandos desconocidos: ignorar
  }
}

export function useJarvis() {
  const [state, dispatch] = useReducer(reducer, initial);
  const tokenRef = useRef("");
  tokenRef.current = state.uiToken;

  useEffect(() => {
    const es = new EventSource("/events");
    es.onmessage = (msg) => {
      try { dispatch(JSON.parse(msg.data)); } catch { /* línea malformada: ignorar */ }
    };
    es.onerror = () => dispatch({ command: "sseDown", args: [] });
    return () => es.close();
  }, []);

  const post = useCallback(async (path: string, body: Record<string, unknown>) => {
    await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Jarvis-Ui-Token": tokenRef.current },
      body: JSON.stringify(body),
    });
  }, []);

  const resolveApproval = useCallback(
    (id: string, approved: boolean) => post("/approval", { id, approved }),
    [post],
  );
  const sendCommand = useCallback(
    (command: string) => post("/command", { command }),
    [post],
  );

  return { state, resolveApproval, sendCommand };
}
```

- [ ] **Step 5: `ui/src/main.tsx`**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Borrar `ui/src/App.css` y `ui/src/assets/`; verificar typecheck**

```powershell
Remove-Item ui/src/App.css -ErrorAction SilentlyContinue
Remove-Item ui/src/assets -Recurse -ErrorAction SilentlyContinue
cd ui; npx tsc --noEmit
```

Expected: errores SOLO por `App.tsx` aún sin reescribir (se hace en Task 3.3). Si hay otros, arreglarlos.

- [ ] **Step 7: Commit**

```powershell
git add ui
git commit -m "feat(ui): theme tokens, tipos del protocolo y hook useJarvis (SSE + POST)"
```

### Task 3.3: Componentes

**Files:**
- Replace: `ui/src/App.tsx`
- Create: `ui/src/components/{StatusBar,Core,Transcript,ThoughtLog,Telemetry,ApprovalModal,CameraPanel}.tsx`

- [ ] **Step 1: `ui/src/components/StatusBar.tsx`**

```tsx
import type { JarvisState } from "../types";

const DOT: Record<string, string> = {
  connected: "bg-(--color-ok)",
  connecting: "bg-(--color-warn)",
  reconnecting: "bg-(--color-warn)",
  error: "bg-(--color-danger)",
  stopped: "bg-white/30",
};

export default function StatusBar({ s }: { s: JarvisState }) {
  return (
    <header className="glass flex items-center gap-4 px-5 py-2.5">
      <span className="font-(family-name:--font-display) text-lg font-bold tracking-widest text-white">
        JARVIS <span className="text-(--color-accent)">{s.version}</span>
      </span>
      <span className="mono text-xs rounded-full border border-(--color-edge) px-3 py-1">
        {s.mode === "LIBRE" ? "● ESCUCHA LIBRE" : "○ PTT (Ctrl)"}
      </span>
      <span className="flex items-center gap-2 text-xs text-white/60">
        <i className={`h-2 w-2 rounded-full ${DOT[s.connection.status] ?? "bg-white/30"}`} />
        Gemini {s.connection.status}{s.connection.detail ? ` — ${s.connection.detail}` : ""}
      </span>
      <span className="ml-auto text-xs text-white/40">{s.privacy}</span>
      {!s.connected && (
        <span className="mono text-xs text-(--color-danger)">backend desconectado…</span>
      )}
    </header>
  );
}
```

- [ ] **Step 2: `ui/src/components/Core.tsx` (núcleo reactivo)**

```tsx
import { motion } from "framer-motion";
import type { CoreState } from "../types";

const STATE_STYLE: Record<CoreState, { color: string; label: string; pulse: number }> = {
  idle: { color: "#475569", label: "EN REPOSO", pulse: 3.2 },
  listening: { color: "#22d3ee", label: "ESCUCHANDO", pulse: 1.4 },
  thinking: { color: "#a78bfa", label: "PENSANDO", pulse: 0.9 },
  speaking: { color: "#34d399", label: "HABLANDO", pulse: 0.7 },
  blocked: { color: "#f87171", label: "BLOQUEADO", pulse: 0 },
};

export default function Core({ state, audioLevel }: { state: CoreState; audioLevel: number }) {
  const cfg = STATE_STYLE[state];
  const scale = 1 + Math.min(audioLevel, 1) * 0.35;
  return (
    <div className="glass flex flex-col items-center justify-center gap-4 p-6">
      <div className="relative flex h-40 w-40 items-center justify-center">
        {[0, 1, 2].map((ring) => (
          <motion.span
            key={ring}
            className="absolute rounded-full border"
            style={{ borderColor: cfg.color, width: 80 + ring * 34, height: 80 + ring * 34, opacity: 0.5 - ring * 0.14 }}
            animate={cfg.pulse > 0 ? { scale: [1, 1.07, 1], opacity: [0.5 - ring * 0.14, 0.2, 0.5 - ring * 0.14] } : { scale: 1 }}
            transition={{ duration: cfg.pulse * (1 + ring * 0.3), repeat: Infinity, ease: "easeInOut" }}
          />
        ))}
        <motion.div
          className="h-16 w-16 rounded-full"
          style={{ background: cfg.color, boxShadow: `0 0 40px ${cfg.color}66` }}
          animate={{ scale: state === "speaking" || state === "listening" ? scale : 1 }}
          transition={{ duration: 0.08 }}
        />
      </div>
      <span className="mono text-xs tracking-[0.3em]" style={{ color: cfg.color }}>
        {cfg.label}
      </span>
    </div>
  );
}
```

- [ ] **Step 3: `ui/src/components/Transcript.tsx`**

```tsx
import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { JarvisState } from "../types";

export default function Transcript({ s, onClear }: { s: JarvisState; onClear: () => void }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [s.output, s.input]);

  return (
    <section className="glass flex min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-(--color-edge) px-5 py-2.5">
        <h2 className="font-(family-name:--font-display) text-sm tracking-widest text-white/70">CONVERSACION</h2>
        <button onClick={onClear} className="mono text-xs text-white/40 transition hover:text-(--color-accent)">
          limpiar
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {s.input && (
          <div className="rounded-xl border border-(--color-edge) bg-white/[0.02] p-3 text-sm text-white/60">
            <span className="mono mb-1 block text-[10px] tracking-widest text-(--color-accent)">ISAAC</span>
            {s.input}
          </div>
        )}
        <div className="prose prose-invert prose-sm max-w-none text-[0.92rem] leading-relaxed [&_code]:mono [&_pre]:rounded-lg [&_pre]:border [&_pre]:border-(--color-edge)">
          <span className="mono mb-1 block text-[10px] tracking-widest text-(--color-ok)">JARVIS</span>
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {s.output || "_Esperando conversación… mantén **Ctrl** para hablar._"}
          </ReactMarkdown>
        </div>
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
```

Nota: las clases `prose` requieren el plugin typography. Instalarlo: `npm i @tailwindcss/typography` y en `index.css` añadir `@plugin "@tailwindcss/typography";` tras el import de tailwindcss.

- [ ] **Step 4: `ui/src/components/ThoughtLog.tsx`**

```tsx
import { motion, AnimatePresence } from "framer-motion";
import type { AgentEvent, LogLine } from "../types";

const STATUS_COLOR: Record<AgentEvent["status"], string> = {
  running: "text-(--color-warn)",
  ok: "text-(--color-ok)",
  error: "text-(--color-danger)",
};

export default function ThoughtLog({ tools, events }: { tools: AgentEvent[]; events: LogLine[] }) {
  return (
    <section className="glass flex min-h-0 flex-1 flex-col">
      <h2 className="border-b border-(--color-edge) px-4 py-2.5 font-(family-name:--font-display) text-sm tracking-widest text-white/70">
        ACTIVIDAD DE AGENTES
      </h2>
      <div className="mono min-h-0 flex-1 space-y-1.5 overflow-y-auto px-4 py-3 text-[11px]">
        <AnimatePresence initial={false}>
          {tools.slice(-30).map((t, i) => (
            <motion.div
              key={`${t.stamp}-${t.name}-${i}`}
              initial={{ opacity: 0, x: 12 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex gap-2"
            >
              <span className="text-white/30">{t.stamp}</span>
              <span className={STATUS_COLOR[t.status]}>
                {t.status === "running" ? "▸" : t.status === "ok" ? "✓" : "✗"}
              </span>
              <span className="text-white/80">{t.name}</span>
              <span className="truncate text-white/40">{t.summary || t.detail}</span>
              {t.elapsedMs != null && <span className="ml-auto shrink-0 text-white/30">{Math.round(t.elapsedMs)}ms</span>}
            </motion.div>
          ))}
        </AnimatePresence>
        {events.slice(-8).map((e, i) => (
          <div key={`log-${i}`} className="flex gap-2 text-white/35">
            <span>{e.stamp}</span>
            <span className={e.level === "error" ? "text-(--color-danger)" : e.level === "warn" ? "text-(--color-warn)" : ""}>
              {e.message}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 5: `ui/src/components/Telemetry.tsx`**

```tsx
import type { JarvisState } from "../types";

function Ring({ pct, color, label, sub }: { pct: number; color: string; label: string; sub: string }) {
  const r = 26, c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-3">
      <svg width="64" height="64" viewBox="0 0 64 64" className="-rotate-90">
        <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="5" />
        <circle
          cx="32" cy="32" r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.min(pct, 1))}
          style={{ filter: `drop-shadow(0 0 4px ${color})`, transition: "stroke-dashoffset 0.6s" }}
        />
      </svg>
      <div className="mono text-[11px]">
        <div className="text-white/80">{label}</div>
        <div className="text-white/40">{sub}</div>
      </div>
    </div>
  );
}

export default function Telemetry({ s }: { s: JarvisState }) {
  const b = s.budget;
  const t = s.audioTelemetry;
  const lastLatency = s.latency[s.latency.length - 1];
  return (
    <section className="glass space-y-4 p-4">
      <h2 className="font-(family-name:--font-display) text-sm tracking-widest text-white/70">TELEMETRIA</h2>
      {b && (
        <div className="space-y-3">
          <Ring pct={b.gemini.pct} color={b.gemini.blocked ? "#f87171" : "#22d3ee"} label="Gemini" sub={b.gemini.label} />
          <Ring pct={b.claude.pct} color={b.claude.blocked ? "#f87171" : "#a78bfa"} label="Claude" sub={b.claude.label} />
        </div>
      )}
      <div className="mono grid grid-cols-2 gap-2 text-[11px]">
        {t.erlePeakDb != null && (
          <div className="rounded-lg border border-(--color-edge) p-2">
            <div className="text-white/40">AEC ERLE</div>
            <div className="text-(--color-accent)">{t.erlePeakDb} dB</div>
          </div>
        )}
        {t.wakewordPeak != null && (
          <div className="rounded-lg border border-(--color-edge) p-2">
            <div className="text-white/40">Wake-word</div>
            <div className="text-(--color-accent)">{t.wakewordPeak}</div>
          </div>
        )}
        <div className="rounded-lg border border-(--color-edge) p-2">
          <div className="text-white/40">Memoria</div>
          <div>
            <span className="text-(--color-ok)">{s.memory.ok}✓</span>{" "}
            <span className="text-(--color-warn)">{s.memory.active}▸</span>{" "}
            <span className="text-(--color-danger)">{s.memory.error}✗</span>
          </div>
        </div>
        {b && (
          <div className="rounded-lg border border-(--color-edge) p-2">
            <div className="text-white/40">Total</div>
            <div className="text-white/80">${b.totalUsd.toFixed(3)}</div>
          </div>
        )}
      </div>
      {lastLatency && (
        <p className="mono truncate text-[10px] text-white/35" title={lastLatency}>{lastLatency}</p>
      )}
    </section>
  );
}
```

- [ ] **Step 6: `ui/src/components/ApprovalModal.tsx`**

```tsx
import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useState } from "react";
import type { Approval } from "../types";

export default function ApprovalModal({
  approval, onDecision,
}: { approval: Approval | null; onDecision: (id: string, ok: boolean) => void }) {
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (!approval) return;
    setLeft(approval.timeout_s);
    const t = setInterval(() => setLeft((v) => Math.max(0, v - 1)), 1000);
    return () => clearInterval(t);
  }, [approval]);

  const danger = approval?.risk === "high";
  return (
    <AnimatePresence>
      {approval && (
        <motion.div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        >
          <motion.div
            initial={{ scale: 0.92, y: 12 }} animate={{ scale: 1, y: 0 }} exit={{ scale: 0.92, opacity: 0 }}
            transition={{ type: "spring", stiffness: 320, damping: 26 }}
            className={`glass w-[480px] p-6 ${danger ? "border-(--color-danger)" : "border-(--color-warn)"}`}
            style={{ boxShadow: `0 0 32px ${danger ? "rgba(248,113,113,0.25)" : "rgba(251,191,36,0.2)"}` }}
          >
            <div className="mono mb-1 text-[10px] tracking-[0.3em] text-white/40">
              APROBACION REQUERIDA · RIESGO {approval.risk.toUpperCase()} · {left}s
            </div>
            <h3 className="font-(family-name:--font-display) mb-2 text-lg text-white">{approval.title}</h3>
            <pre className="mono mb-5 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-(--color-edge) bg-black/30 p-3 text-xs text-white/70">
              {approval.details}
            </pre>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => onDecision(approval.id, false)}
                className="rounded-lg border border-(--color-edge) px-4 py-2 text-sm text-white/70 transition hover:border-(--color-danger) hover:text-(--color-danger)"
              >
                Rechazar
              </button>
              <button
                onClick={() => onDecision(approval.id, true)}
                className="rounded-lg bg-(--color-accent) px-4 py-2 text-sm font-semibold text-black transition hover:brightness-110 glow-accent"
              >
                Aprobar
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
```

- [ ] **Step 7: `ui/src/components/CameraPanel.tsx`**

```tsx
import type { JarvisState } from "../types";

export default function CameraPanel({ s }: { s: JarvisState }) {
  if (!s.cameraActive) return null;
  return (
    <section className="glass overflow-hidden">
      <div className="flex items-center justify-between border-b border-(--color-edge) px-4 py-2">
        <h2 className="font-(family-name:--font-display) text-xs tracking-widest text-(--color-warn)">
          ● CAMARA ACTIVA
        </h2>
        {s.cameraFocus?.label && <span className="mono text-[10px] text-white/50">{s.cameraFocus.label}</span>}
      </div>
      {s.cameraFrame ? (
        <img src={`data:image/jpeg;base64,${s.cameraFrame}`} alt="camara" className="block w-full" />
      ) : (
        <div className="mono p-6 text-center text-xs text-white/30">esperando frames…</div>
      )}
    </section>
  );
}
```

- [ ] **Step 8: `ui/src/App.tsx`**

```tsx
import { useJarvis } from "./useJarvis";
import StatusBar from "./components/StatusBar";
import Core from "./components/Core";
import Transcript from "./components/Transcript";
import ThoughtLog from "./components/ThoughtLog";
import Telemetry from "./components/Telemetry";
import ApprovalModal from "./components/ApprovalModal";
import CameraPanel from "./components/CameraPanel";

export default function App() {
  const { state: s, resolveApproval, sendCommand } = useJarvis();
  return (
    <div className="relative grid h-screen grid-rows-[auto_1fr] gap-3 p-3">
      <StatusBar s={s} />
      <main className="grid min-h-0 grid-cols-[300px_1fr_340px] gap-3">
        <div className="flex min-h-0 flex-col gap-3">
          <Core state={s.state} audioLevel={s.audioLevel} />
          <Telemetry s={s} />
        </div>
        <Transcript s={s} onClear={() => sendCommand("clearTranscripts")} />
        <div className="flex min-h-0 flex-col gap-3">
          <ThoughtLog tools={s.agentEvents} events={s.events} />
          <CameraPanel s={s} />
        </div>
      </main>
      <ApprovalModal approval={s.approval} onDecision={resolveApproval} />
    </div>
  );
}
```

- [ ] **Step 9: Typecheck + dev contra backend vivo**

```powershell
cd ui; npx tsc --noEmit
```
Expected: 0 errores.

Smoke con backend (dos terminales):

```powershell
# Terminal 1
$env:JARVIS_UI="web"; $env:JARVIS_WEB_UI_OPEN_BROWSER="false"; & "H:\Python311\python.exe" jarvis.py
# Terminal 2
cd ui; npm run dev
```

Abrir `http://localhost:5173`. Expected: StatusBar con versión real, núcleo en estado idle/listening, transcript reacciona al hablar (Ctrl), thought log muestra tools al pedir algo que las dispare, budgets visibles.

- [ ] **Step 10: Commit**

```powershell
git add ui
git commit -m "feat(ui): componentes React - core reactivo, transcript markdown, thought log, telemetria, aprobaciones, camara"
```

### Task 3.4: Servir el build desde el bridge Python

**Files:**
- Modify: `overlay/web_overlay.py` (resolución de `WEB_DIR`)
- Test: añadir a `tests/test_web_overlay_headless.py`

- [ ] **Step 1: Test que falla**

Añadir a `tests/test_web_overlay_headless.py`:

```python
def test_web_dir_prefers_dist(tmp_path, monkeypatch):
    from overlay import web_overlay

    dist = tmp_path / "web_dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>dist</html>", encoding="utf-8")
    monkeypatch.setenv("JARVIS_WEB_UI_DIR", str(dist))
    assert web_overlay.resolve_web_dir() == dist

    monkeypatch.delenv("JARVIS_WEB_UI_DIR")
    resolved = web_overlay.resolve_web_dir()
    # Sin override: web_dist/ del repo si tiene index.html, si no web_ui/
    assert resolved.name in {"web_dist", "web_ui"}
```

- [ ] **Step 2: Implementar en `overlay/web_overlay.py`**

Reemplazar la constante `WEB_DIR = Path(__file__).resolve().parent / "web_ui"` por:

```python
def resolve_web_dir() -> Path:
    """UI a servir: override por env > build React (web_dist) > vanilla (web_ui)."""
    override = os.environ.get("JARVIS_WEB_UI_DIR", "").strip()
    if override:
        return Path(override)
    base = Path(__file__).resolve().parent
    dist = base / "web_dist"
    if (dist / "index.html").is_file():
        return dist
    return base / "web_ui"
```

Y en `_BridgeHandler._serve_static`, reemplazar los DOS usos de `WEB_DIR`:

```python
    def _serve_static(self, path: str) -> None:
        web_dir = resolve_web_dir()
        rel = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
        target = (web_dir / rel).resolve()
        try:
            target.relative_to(web_dir.resolve())
        except ValueError:
            ...
```

(El resto del método igual.)

- [ ] **Step 3: Build de producción + smoke**

```powershell
cd ui; npm run build
```
Expected: `overlay/web_dist/index.html` + assets generados.

```powershell
$env:JARVIS_UI="web"; & "H:\Python311\python.exe" jarvis.py
```
Expected: el browser abre la UI **React** (no la vanilla) en 8765.

- [ ] **Step 4: Tests + commit**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
git add overlay/web_overlay.py tests/test_web_overlay_headless.py
git commit -m "feat(overlay): servir build React (web_dist) con fallback a web_ui vanilla"
```

---

# ETAPA 4 — Shell Tauri v2 (sidecar Python + ciclo de vida)

El shell Tauri NO empaqueta Python (decisión: sin PyInstaller — JARVIS es personal, Python global en H:). Tauri lanza `H:\Python311\python.exe jarvis.py` como proceso hijo, espera el puerto 8765, abre la ventana apuntando al bridge, y al cerrar hace shutdown limpio (POST `/command close` con token) con kill de respaldo. El watchdog Python de Task 1.3 cubre el caso inverso (shell muerto sin avisar).

### Task 4.1: Instalar toolchain Rust (en H:, no en C:)

- [ ] **Step 1: Configurar rutas de Rust en H: ANTES de instalar**

```powershell
[Environment]::SetEnvironmentVariable("RUSTUP_HOME", "H:\rustup", "User")
[Environment]::SetEnvironmentVariable("CARGO_HOME", "H:\cargo", "User")
```

Cerrar y reabrir la terminal para que apliquen.

- [ ] **Step 2: Instalar rustup + toolchain MSVC**

```powershell
winget install --id Rustlang.Rustup -e
# nueva terminal:
rustup toolchain install stable-msvc
rustup default stable-msvc
```

- [ ] **Step 3: Instalar MSVC Build Tools (linker de C++). ~6GB — instalar en H:**

```powershell
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --override "--quiet --wait --installPath H:\BuildTools --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Esto tarda 10-30 min. Verificación final:

```powershell
rustc --version
cargo --version
```

Expected: `rustc 1.8x.x` y `cargo 1.8x.x` sin errores. **Si `cargo build` falla después con "link.exe not found", abrir "Developer PowerShell for VS 2022" o reinstalar agregando `Microsoft.VisualStudio.Component.Windows11SDK.22621`.** WebView2 runtime ya viene con Windows 10 actualizado (Edge); si faltara, Tauri lo instala al hacer bundle.

### Task 4.2: Proyecto Tauri en `desktop/`

**Files:**
- Create: `desktop/package.json`, `desktop/src-tauri/Cargo.toml`, `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/build.rs`, `desktop/src-tauri/src/main.rs`, `desktop/src-tauri/capabilities/default.json`, `desktop/src-tauri/icons/`

- [ ] **Step 1: `desktop/package.json`**

```json
{
  "name": "jarvis-desktop",
  "private": true,
  "version": "1.0.0",
  "scripts": {
    "tauri": "tauri",
    "dev": "tauri dev",
    "build": "tauri build"
  },
  "devDependencies": {
    "@tauri-apps/cli": "^2"
  }
}
```

```powershell
cd desktop; npm install
```

- [ ] **Step 2: `desktop/src-tauri/Cargo.toml`**

```toml
[package]
name = "jarvis-desktop"
version = "1.0.0"
edition = "2021"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = [] }
serde_json = "1"
ureq = { version = "2", features = ["json"] }
```

- [ ] **Step 3: `desktop/src-tauri/build.rs`**

```rust
fn main() {
    tauri_build::build()
}
```

- [ ] **Step 4: `desktop/src-tauri/tauri.conf.json`**

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "JARVIS",
  "version": "1.0.0",
  "identifier": "dev.isaac.jarvis",
  "build": {
    "frontendDist": "http://127.0.0.1:8765"
  },
  "app": {
    "windows": [],
    "security": {
      "csp": null
    }
  },
  "bundle": {
    "active": true,
    "targets": ["nsis"],
    "icon": ["icons/icon.ico"]
  }
}
```

Notas: `windows: []` porque la ventana se crea en Rust DESPUÉS de que el backend responde (evita pantalla blanca). `frontendDist` como URL remota = no hay assets propios del shell.

- [ ] **Step 5: `desktop/src-tauri/capabilities/default.json`**

```json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "windows": ["main"],
  "permissions": []
}
```

(La UI no usa IPC de Tauri — habla solo HTTP con el bridge — así que sin permisos.)

- [ ] **Step 6: Iconos**

Generar set de iconos desde un PNG cuadrado (usar `assets/` si hay logo de JARVIS, o crear uno 512x512):

```powershell
cd desktop; npx tauri icon ..\assets\jarvis_logo.png
```

Si no existe logo, crear un PNG simple 512x512 con el glow cian sobre fondo `#0d0f12` (Pillow):

```powershell
& "H:\Python311\python.exe" -c "from PIL import Image, ImageDraw; img = Image.new('RGBA', (512,512), (13,15,18,255)); d = ImageDraw.Draw(img); [d.ellipse([256-r, 256-r, 256+r, 256+r], outline=(34,211,238,max(0,180-r)), width=6) for r in (80, 120, 160)]; d.ellipse([256-50,256-50,256+50,256+50], fill=(34,211,238,255)); img.save('assets/jarvis_logo.png')"
cd desktop; npx tauri icon ..\assets\jarvis_logo.png
```

Expected: `desktop/src-tauri/icons/` poblado (icon.ico incluido).

- [ ] **Step 7: `desktop/src-tauri/src/main.rs`**

```rust
// JARVIS desktop shell: lanza el backend Python (bridge web en 8765), espera a
// que responda, abre la ventana, y al salir apaga el backend con gracia.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const BASE: &str = "http://127.0.0.1:8765";
const PYTHON: &str = "H:\\Python311\\python.exe";
const JARVIS_DIR: &str = "C:\\Users\\Isaac\\Desktop\\PROYECTOS\\JARVIS";

struct Backend(Mutex<Option<Child>>);

fn spawn_python() -> std::io::Result<Child> {
    Command::new(PYTHON)
        .arg("jarvis.py")
        .current_dir(JARVIS_DIR)
        .env("JARVIS_UI", "web")
        .env("JARVIS_WEB_UI_OPEN_BROWSER", "false")
        .env("JARVIS_WEB_UI_PORT", "8765")
        .env("JARVIS_SUPERVISED", "1")
        .env("PYTHONUTF8", "1")
        .spawn()
}

fn wait_for_backend(timeout_s: u64) -> bool {
    let deadline = Instant::now() + Duration::from_secs(timeout_s);
    let addr = "127.0.0.1:8765".parse().expect("addr");
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn graceful_shutdown(child: &mut Child) {
    // El POST /command requiere el uiToken; /state lo expone (solo localhost).
    let token = ureq::get(&format!("{BASE}/state"))
        .timeout(Duration::from_secs(2))
        .call()
        .ok()
        .and_then(|r| r.into_json::<serde_json::Value>().ok())
        .and_then(|v| v["uiToken"].as_str().map(String::from));
    if let Some(token) = token {
        let _ = ureq::post(&format!("{BASE}/command"))
            .set("X-Jarvis-Ui-Token", &token)
            .timeout(Duration::from_secs(2))
            .send_json(serde_json::json!({ "command": "close" }));
    }
    // Hasta 5s de gracia para que JARVIS persista memoria/telemetria.
    for _ in 0..20 {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let child = spawn_python().map_err(|e| format!("no pude lanzar Python: {e}"))?;
            app.manage(Backend(Mutex::new(Some(child))));
            if !wait_for_backend(45) {
                eprintln!("[desktop] backend no respondio en 45s; la ventana mostrara error de conexion");
            }
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(BASE.parse().expect("url")),
            )
            .title("JARVIS")
            .inner_size(1380.0, 860.0)
            .min_inner_size(1080.0, 700.0)
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error construyendo la app Tauri")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(backend) = app.try_state::<Backend>() {
                    if let Some(mut child) = backend.0.lock().expect("lock").take() {
                        graceful_shutdown(&mut child);
                    }
                }
            }
        });
}
```

- [ ] **Step 8: Compilar (sin correr)**

```powershell
cd desktop\src-tauri; cargo build
```

Expected: compila sin errores (primera vez tarda varios minutos bajando crates a `H:\cargo`). Errores típicos: falta `link.exe` → revisar Task 4.1 Step 3.

- [ ] **Step 9: Commit**

```powershell
git add desktop
git commit -m "feat(desktop): shell Tauri v2 con sidecar Python y shutdown con gracia"
```

### Task 4.3: Smoke E2E del shell completo

- [ ] **Step 1: Asegurar build React fresco**

```powershell
cd ui; npm run build
```

- [ ] **Step 2: Lanzar la app**

```powershell
cd desktop; npm run dev
```

Verificar (checklist manual con Isaac):

1. La ventana JARVIS abre con la UI React (sin browser externo).
2. Hablar con Ctrl (PTT) → núcleo pasa a `listening`, transcript se llena.
3. Pedir algo que dispare una tool → aparece en ACTIVIDAD DE AGENTES.
4. Pedir una acción que requiera aprobación → modal con countdown; aprobar y rechazar funcionan; dejar expirar = auto-rechazo.
5. `Ctrl+Shift+C` (cámara) → panel de cámara con frames.
6. Cerrar la ventana → el proceso Python muere en <6s (verificar con `Get-Process python -ErrorAction SilentlyContinue`).
7. Matar la ventana con el Task Manager → en ~70s el Python se auto-apaga (watchdog supervisado). Verificar log `[web-ui] supervisado y sin clientes 60s`.
8. `Ctrl+Alt+Q` (kill-switch) → Python muere; la ventana muestra "backend desconectado…".

- [ ] **Step 2b (caso 7 alternativo rápido):** en vez de esperar 70s reales, bajar temporalmente el umbral del watchdog a 10s en `_watchdog_check` para la prueba y restaurarlo.

- [ ] **Step 3: Build de release + acceso directo**

```powershell
cd desktop; npm run build
```

Expected: instalador NSIS en `desktop\src-tauri\target\release\bundle\nsis\` y exe en `desktop\src-tauri\target\release\jarvis-desktop.exe`. Crear shortcut de escritorio "JARVIS" apuntando al exe de release (reemplaza el shortcut del `.bat`).

- [ ] **Step 4: Commit de ajustes que hayan salido del smoke**

```powershell
git add -A
git commit -m "fix(desktop): ajustes post-smoke E2E del shell Tauri"
```

---

# ETAPA 5 — Documentación, env y cierre

### Task 5.1: `.env.example` + docs

- [ ] **Step 1: Añadir a `.env.example`** (sección UI):

```bash
# --- UI web / desktop (Tauri) ---
# tk = overlay tkinter clasico (necesario para ocultar de OBS/Zoom y RegionSelector)
# web = bridge SSE + UI React (la que usa el shell Tauri)
JARVIS_UI=web
JARVIS_WEB_UI_PORT=8765
JARVIS_WEB_UI_OPEN_BROWSER=false
# Solo lo setea el shell Tauri: activa watchdog de auto-apagado sin clientes
# JARVIS_SUPERVISED=1
# Override del directorio de UI servido (default: overlay/web_dist si existe, si no overlay/web_ui)
# JARVIS_WEB_UI_DIR=
```

- [ ] **Step 2: Crear `docs/WEB_UI.md`** con: protocolo SSE completo (lista de comandos existentes + los nuevos `agentToolStart/End`, `audioTelemetry`, `turnLatency`, `cameraFrame`, `cameraFocus`, `setCameraActive`), endpoints (`/state`, `/events`, `/approval`, `/command`), auth por token, cómo desarrollar la UI (`npm run dev` + proxy), cómo buildear, y cómo lanzar el shell (`desktop/`). Mover ahí el contenido vigente de `overlay/web_ui/README.md`.

- [ ] **Step 3: Actualizar `README.md`** (sección de arranque): el camino principal ahora es el exe de Tauri o `JARVIS_UI=web`; `jarvis_run.bat`/modo tk quedan documentados como fallback (captura oculta + RegionSelector).

- [ ] **Step 4: Commit**

```powershell
git add .env.example docs/WEB_UI.md README.md
git commit -m "docs: protocolo web UI, vars de entorno y arranque desktop"
```

### Task 5.2: Retirar `overlay/web_ui/` vanilla (solo tras paridad)

- [ ] **Step 1: Confirmar con Isaac que la UI React cubre todo lo que usaba de la vanilla** (checklist Task 4.3 completo y varios días de uso real). NO antes.

- [ ] **Step 2: Eliminar**

```powershell
git rm -r overlay/web_ui
```

Y en `resolve_web_dir()`, simplificar el fallback: si no hay `web_dist/index.html`, lanzar `RuntimeError("UI web no compilada: corre 'cd ui; npm run build'")` en vez de caer a `web_ui`.

- [ ] **Step 3: Suite + commit**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
git add -A
git commit -m "chore(ui): retirar web_ui vanilla; web_dist es la unica UI web"
```

### Task 5.3: Versionar y mergear

- [ ] **Step 1: Bump de versión** (seguir el patrón existente de `VERSION` + `jarvis_version.py` + `CHANGELOG.md`, como el commit `c06bee4` de la 1.02 → esta sería 1.10 o la que toque según el esquema de Isaac). Entrada de CHANGELOG: "UI desktop Tauri + React; modo web headless sin tkinter; thought log y telemetría".

- [ ] **Step 2: Suite final completa + merge**

```powershell
$env:PYTHONUTF8=1; & "H:\Python311\python.exe" -m pytest -q
git checkout main
git merge feature/ui-tauri
git push origin main
```

---

## Fuera de alcance (siguientes, NO en este plan)

- **RegionSelector sin tkinter:** ventana Tauri transparente fullscreen con drag-rectangle que hace POST del bbox al bridge. Hasta entonces: modo web degrada a captura completa; modo tk conserva el selector.
- **Ocultar la ventana Tauri de capturas** (`SetWindowDisplayAffinity` + `WDA_EXCLUDEFROMCAPTURE` vía crate `windows` con el hwnd de la ventana): trivial de añadir en `main.rs` cuando Isaac lo necesite; hasta entonces `JARVIS_UI=tk` cubre ese caso.
- **Grafo de flujo de agentes (React Flow):** los eventos `agentToolStart/End` de la Etapa 2 ya son la materia prima; el grafo es una vista nueva sobre los mismos datos.
- **Empaquetado PyInstaller del backend:** solo si algún día se distribuye a terceros.
- **System tray + always-on-top toggle:** plugins estándar de Tauri v2, añadir cuando se quiera modo "mini overlay".

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Algún test existente asume `overlay.root` no-None en web | La suite corre en cada task; si aparece, actualizar el test al contrato nuevo (`root is None` en web) |
| `_serve_static` lee `resolve_web_dir()` por request (cambio menor de perf) | Irrelevante en localhost single-user |
| Tauri + Build Tools llenan disco | RUSTUP_HOME/CARGO_HOME/BuildTools en H: (Task 4.1) |
| Puerto 8765 ocupado por instancia zombi previa | El bridge ya prueba 8765..8784; el shell asume 8765 fijo — si `wait_for_backend` falla, revisar `Get-Process python` y matar zombis |
| `cameraFrame` base64 por SSE satura con FPS alto | Throttle 4 fps en Python (`CAMERA_EMIT_MIN_INTERVAL_S`) + vision ya corre a 3 fps |
| Two `_BridgeHandler` clients (Vite dev + ventana) | Soportado: `_clients` es un set, emit hace fan-out |

## Self-review (hecho al escribir el plan)

- Cobertura: decisiones 1-8 ↔ Etapas 0-5 verificadas; alcance acordado (thought log, telemetría, cámara, markdown, elegancia) cubierto en Etapas 2-3.
- Sin placeholders: todo paso con código lo incluye; los dos puntos con incertidumbre de línea exacta (`wake-word peak=`, `record_memory_tool` en window.py) llevan comando de localización explícito.
- Consistencia de tipos: `record_tool_start/end`, `record_audio_telemetry`, `record_turn_latency`, `after()` definidos en Etapa 1-2 y consumidos con las mismas firmas en jarvis.py y en `types.ts`/`useJarvis.ts` (camelCase en payloads JSON: `elapsedMs`, `erlePeakDb`, `wakewordPeak`).


