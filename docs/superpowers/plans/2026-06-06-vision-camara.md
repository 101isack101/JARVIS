# Visión por Cámara — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dar a JARVIS visión por la webcam frontal, bajo comando: ver lo que Isaac le muestre, analizarlo, investigarlo y mostrar un preview con crosshair del objeto enfocado.

**Architecture:** Un motor `CameraCapture` (cv2), consumido por (a) tool on-demand `camera_look` que reutiliza el side-channel `__attach_image` ya probado por `screen_look`, (b) un `CameraWatchController` ("modo visión") que streamea frames a ~1fps por `session.send_video_frame()` → `send_realtime_input(video=)`, y (c) `overlay/camera_preview.py` que pinta el preview y dibuja el crosshair (retícula central + box semántico vía `camera_focus`). Ningún hilo de cámara toca tkinter; todo va por `_tk()`.

**Tech Stack:** Python 3.11 (H:\Python311, sin venv), `opencv-python`, Pillow, `google-genai` (Gemini Live `gemini-3.1-flash-live-preview`), tkinter, pytest.

**Spec:** `docs/superpowers/specs/2026-06-06-vision-camara-design.md`

**Branch:** `feature/vision-camara` (ya creada).

---

## Estructura de archivos

| Archivo | Estado | Responsabilidad |
|---|---|---|
| `vision/camera.py` | Crear | `CameraFrame`, `CameraCapture` (motor cv2), `CameraWatchController` |
| `vision/prompts.py` | Modificar | añadir source `"camera"` a `visual_capture_prompt` |
| `gemini/session.py` | Modificar | `send_video_frame()` + `_async_send_video()`; soporte `source` en `__attach_image` |
| `memory/tools.py` | Modificar | `ToolContext.camera`/`.camera_watch`; 3 decls; 3 handlers; dispatch |
| `jarvis.py` | Modificar | instanciar `CameraCapture`/`CameraWatchController`, wiring ctx, hotkey, teardown |
| `overlay/hotkeys.py` | Modificar | callback + hotkey `ctrl+shift+c` |
| `overlay/camera_preview.py` | Crear | ventana preview live + crosshair (main thread) |
| `gemini/system_prompt.py` | Modificar | mapear "modo visión"/"mira esto" a las tools |
| `telemetry/costs.py` | Modificar | fila de pricing del modelo de detección `camera_focus` |
| `.env.example` | Modificar | vars `JARVIS_CAMERA_*` |
| `requirements.txt` | Modificar | `opencv-python` |
| `tests/test_camera.py` | Crear | unit tests del motor + look |
| `tests/test_camera_watch.py` | Crear | unit tests del controller |
| `tests/test_camera_focus.py` | Crear | unit tests de parseo de box_2d |
| `tests/test_visual_prompts.py` | Modificar | test del prompt "camera" |

---

# FASE 1 — On-demand (`camera_look`)

## Task 1: Dependencia + motor `CameraCapture`

**Files:**
- Modify: `requirements.txt`
- Create: `vision/camera.py`
- Test: `tests/test_camera.py`

- [ ] **Step 1: Añadir dependencia**

En `requirements.txt`, añadir una línea (junto al resto de deps de visión):

```
opencv-python>=4.9
```

Instalar: `& H:\Python311\python.exe -m pip install "opencv-python>=4.9"`

- [ ] **Step 2: Escribir el test que falla**

Crear `tests/test_camera.py`:

```python
import sys
from pathlib import Path

import pytest

from vision.camera import CameraCapture, CameraFrame


class FakeDevice:
    """Simula cv2.VideoCapture: devuelve frames RGB sinteticos."""

    def __init__(self, frames=None, opened=True):
        # frame BGR 8x8 (lo que entrega cv2). numpy opcional: usamos lista->np en read.
        import numpy as np
        self._frames = frames or [np.full((8, 8, 3), 127, dtype=np.uint8)]
        self._i = 0
        self._opened = opened
        self.released = False

    def isOpened(self):
        return self._opened

    def read(self):
        import numpy as np
        if not self._opened:
            return False, None
        frame = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        return True, frame

    def release(self):
        self.released = True


def _factory(opened=True):
    return lambda index: FakeDevice(opened=opened)


def test_capture_returns_valid_frame(tmp_path):
    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory())
    frame = cam.capture()
    assert isinstance(frame, CameraFrame)
    assert frame.mime_type == "image/jpeg"
    assert frame.jpeg_bytes[:3] == b"\xff\xd8\xff"  # cabecera JPEG
    assert frame.path.exists()
    assert frame.width > 0 and frame.height > 0


def test_capture_device_not_opened_raises(tmp_path):
    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory(opened=False))
    with pytest.raises(RuntimeError):
        cam.capture()


def test_capture_releases_device_in_ondemand(tmp_path):
    holder = {}
    def factory(index):
        dev = FakeDevice()
        holder["dev"] = dev
        return dev
    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=factory)
    cam.capture()
    assert holder["dev"].released is True  # open->grab->close
```

- [ ] **Step 3: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vision.camera'`

- [ ] **Step 4: Implementar `vision/camera.py` (motor)**

Crear `vision/camera.py`:

```python
"""
vision/camera.py - Captura de webcam para Jarvis.

Gemelo de vision/screen.py pero para la camara frontal (cv2). Encode JPEG
(la webcam comprime mucho mejor que PNG y el path de video realtime de
Gemini Live espera image/jpeg). El motor es inyectable para tests
(device_factory) y nunca toca tkinter.
"""

from __future__ import annotations

import io
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

try:  # cv2 es pesado; en algunos entornos de test se inyecta device_factory
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore


def _default_device_factory(index: int) -> Any:
    if cv2 is None:  # pragma: no cover
        raise RuntimeError("opencv-python (cv2) no esta instalado")
    # CAP_DSHOW = backend DirectShow, el correcto en Windows.
    return cv2.VideoCapture(index, cv2.CAP_DSHOW)


@dataclass
class CameraFrame:
    path: Path
    width: int
    height: int
    jpeg_bytes: bytes
    mime_type: str = "image/jpeg"

    def as_dict(self) -> dict:
        return {
            "captured": True,
            "path": str(self.path),
            "width": self.width,
            "height": self.height,
            "mime_type": self.mime_type,
        }


class CameraCapture:
    def __init__(
        self,
        out_dir: Path,
        index: int | None = None,
        max_side: int = 1280,
        retention_hours: float | None = None,
        fps: float | None = None,
        warmup_frames: int = 3,
        device_factory: Callable[[int], Any] = _default_device_factory,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.index = int(os.environ.get("JARVIS_CAMERA_INDEX", "0")) if index is None else int(index)
        self.max_side = int(os.environ.get("JARVIS_CAMERA_MAX_SIDE", str(max_side)))
        if retention_hours is None:
            retention_hours = float(os.environ.get("JARVIS_CAMERA_RETENTION_HOURS", "24"))
        self.retention_hours = max(0.0, float(retention_hours))
        self.fps = float(os.environ.get("JARVIS_CAMERA_FPS", "1.0")) if fps is None else float(fps)
        self.warmup_frames = max(0, int(warmup_frames))
        self._device_factory = device_factory
        self._device: Any | None = None
        self._lock = threading.Lock()
        self._last: CameraFrame | None = None
        self.cleanup_old()

    @property
    def last(self) -> CameraFrame | None:
        return self._last

    # ---- On-demand: open -> warmup -> grab -> close ----

    def capture(self) -> CameraFrame:
        with self._lock:
            dev = self._device_factory(self.index)
            try:
                if not dev.isOpened():
                    raise RuntimeError(
                        "No pude abrir la camara. Verifica que no este en uso por "
                        "otra app y revisa Configuracion -> Privacidad -> Camara."
                    )
                self._warmup(dev)
                ok, frame_bgr = dev.read()
                if not ok or frame_bgr is None:
                    raise RuntimeError("La camara no devolvio frame.")
                return self._finalize(frame_bgr, prefix="cam")
            finally:
                try:
                    dev.release()
                except Exception:
                    pass

    # ---- Watch: open once / read N / close ----

    def open(self) -> None:
        with self._lock:
            if self._device is not None:
                return
            dev = self._device_factory(self.index)
            if not dev.isOpened():
                try:
                    dev.release()
                except Exception:
                    pass
                raise RuntimeError("No pude abrir la camara para modo vision.")
            self._warmup(dev)
            self._device = dev

    def read_frame(self) -> CameraFrame:
        with self._lock:
            if self._device is None:
                raise RuntimeError("Camara no abierta (llama open() primero).")
            ok, frame_bgr = self._device.read()
            if not ok or frame_bgr is None:
                raise RuntimeError("La camara no devolvio frame.")
            return self._finalize(frame_bgr, prefix="watch")

    def close(self) -> None:
        with self._lock:
            if self._device is not None:
                try:
                    self._device.release()
                except Exception:
                    pass
                self._device = None

    # ---- internals ----

    def _warmup(self, dev: Any) -> None:
        for _ in range(self.warmup_frames):
            dev.read()

    def _finalize(self, frame_bgr: np.ndarray, prefix: str) -> CameraFrame:
        self.cleanup_old()
        # cv2 entrega BGR; PIL espera RGB.
        frame_rgb = frame_bgr[:, :, ::-1]
        img = Image.fromarray(np.ascontiguousarray(frame_rgb)).convert("RGB")
        img.thumbnail((self.max_side, self.max_side), Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        jpeg_bytes = buf.getvalue()

        ts = time.strftime("%Y%m%d-%H%M%S")
        path = self.out_dir / f"{prefix}-{ts}-{int(time.time()*1000) % 1000:03d}.jpg"
        path.write_bytes(jpeg_bytes)

        frame = CameraFrame(path=path, width=img.width, height=img.height, jpeg_bytes=jpeg_bytes)
        self._last = frame
        return frame

    def cleanup_old(self) -> int:
        cutoff = time.time() - (self.retention_hours * 3600)
        removed = 0
        for path in self.out_dir.glob("*.jpg"):
            try:
                if path.stat().st_mtime <= cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
```

- [ ] **Step 5: Correr el test (debe pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt vision/camera.py tests/test_camera.py
git commit -m "feat(vision): motor CameraCapture (cv2) con tests"
```

---

## Task 2: Prompt visual para cámara

**Files:**
- Modify: `vision/prompts.py`
- Test: `tests/test_visual_prompts.py`

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/test_visual_prompts.py`:

```python
def test_visual_prompt_camera_source_is_live_visual_context():
    prompt = visual_capture_prompt("camera").lower()
    assert "camara" in prompt
    assert "no busques" in prompt  # mantiene el privacy guard
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_visual_prompts.py -k camera -v`
Expected: FAIL — el prompt no contiene "camara".

- [ ] **Step 3: Implementar la rama "camera"**

En `vision/prompts.py`, dentro de `visual_capture_prompt`, añadir antes del `else`:

```python
    elif source == "camera":
        intro = (
            "Isaac te muestra esto en vivo por su camara. "
            "Trata esto como una nueva referencia visual del momento; describi lo "
            "que ve, ayudalo o opina segun lo que aparezca frente a la camara."
        )
```

- [ ] **Step 4: Correr el test (debe pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_visual_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add vision/prompts.py tests/test_visual_prompts.py
git commit -m "feat(vision): prompt visual para source camera"
```

---

## Task 3: Tool `camera_look` + soporte `source` en el side-channel

**Files:**
- Modify: `gemini/session.py:601-628` (extracción de `__attach_image`)
- Modify: `memory/tools.py` (ToolContext, decl, handler, dispatch, declarations list)
- Test: `tests/test_camera.py`

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/test_camera.py`:

```python
def test_camera_look_returns_attach_image(tmp_path):
    from memory.tools import ToolContext, camera_look

    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory())
    ctx = ToolContext(vault=None, rag=None, camera=cam)
    out = camera_look(ctx, reason="mira esto")
    assert out["captured"] is True
    assert out["reason"] == "mira esto"
    attach = out["__attach_image"]
    assert attach["mime_type"] == "image/jpeg"
    assert attach["source"] == "camera"
    assert attach["png_bytes"][:3] == b"\xff\xd8\xff"  # reutiliza la clave png_bytes


def test_camera_look_without_camera_reports_error():
    from memory.tools import ToolContext, camera_look

    ctx = ToolContext(vault=None, rag=None, camera=None)
    out = camera_look(ctx, reason="x")
    assert out["captured"] is False
    assert "error" in out
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera.py -k camera_look -v`
Expected: FAIL — `camera_look` no existe; `ToolContext` no acepta `camera`.

- [ ] **Step 3a: Añadir campos a `ToolContext`**

En `memory/tools.py`, dentro de `class ToolContext` (tras `screen: Any | None = None`):

```python
    camera: Any | None = None
    camera_watch: Any | None = None
```

- [ ] **Step 3b: Añadir la declaración `CAMERA_LOOK_DECL`**

En `memory/tools.py`, tras `SCREEN_LOOK_DECL` (≈línea 250):

```python
CAMERA_LOOK_DECL = types.FunctionDeclaration(
    name="camera_look",
    description=(
        "Captura UNA foto de la camara frontal de Isaac y te la entrega para que "
        "describas o analices lo que te esta mostrando frente a la camara: un objeto, "
        "una placa o componente FPV/electronica, una nota en papel, la pantalla de un "
        "multimetro o cargador, etc. Usala cuando Isaac diga 'mira esto', 'que es esto', "
        "'mira lo que tengo', 'fijate en este objeto' o senale algo fisico. Para ver en "
        "continuo mientras trabaja, usa camera_watch. No leas datos sensibles salvo que "
        "Isaac lo pida explicitamente en el mismo turno."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="Motivo breve de la captura.",
            ),
        },
    ),
)
```

- [ ] **Step 3c: Implementar el handler `camera_look`**

En `memory/tools.py`, tras la función `screen_look` (≈línea 1027):

```python
def camera_look(ctx: ToolContext, reason: str = "") -> dict:
    """Captura una foto de la webcam y la adjunta como contenido del siguiente turno.

    Mismo patron que screen_look: marcamos la imagen con __attach_image (clave
    privada) y el dispatcher en gemini/session.py la envia via send_client_content
    tras el tool_response. Reutiliza la clave 'png_bytes' (el side-channel es
    agnostico al formato; el mime_type indica que es JPEG).
    """
    if ctx.camera is None:
        return {"captured": False, "error": "Camara no configurada."}
    try:
        frame = ctx.camera.capture()
    except Exception as exc:
        return {"captured": False, "error": f"{type(exc).__name__}: {exc}"}
    response = frame.as_dict()
    response["reason"] = reason
    response["image_ref"] = frame.path.name
    response["note"] = (
        "Foto de camara adjuntada como user-content en el siguiente turno; "
        "analizala y responde."
    )
    response["__attach_image"] = {
        "png_bytes": frame.jpeg_bytes,
        "mime_type": frame.mime_type,
        "source": "camera",
    }
    return response
```

- [ ] **Step 3d: Registrar en declarations y dispatcher**

En `all_function_declarations()` (≈línea 678), tras `SCREEN_LOOK_DECL,`:

```python
        CAMERA_LOOK_DECL,
```

En `ToolDispatcher.__init__` `self._tools` (≈línea 1727), tras la línea de `screen_look`:

```python
            "camera_look": lambda **kw: camera_look(ctx, **kw),
```

- [ ] **Step 3e: Pasar `source` en `gemini/session.py`**

En `gemini/session.py::_handle_tool_call`, donde se extrae el adjunto (≈línea 601-606), reemplazar:

```python
            if isinstance(response, dict) and "__attach_image" in response:
                attach = response.pop("__attach_image") or {}
                png_bytes = attach.get("png_bytes")
                mime_type = attach.get("mime_type", "image/png")
                if isinstance(png_bytes, (bytes, bytearray)) and png_bytes:
                    pending_attachments.append((bytes(png_bytes), mime_type))
```

por:

```python
            if isinstance(response, dict) and "__attach_image" in response:
                attach = response.pop("__attach_image") or {}
                png_bytes = attach.get("png_bytes")
                mime_type = attach.get("mime_type", "image/png")
                source = attach.get("source", "tool")
                if isinstance(png_bytes, (bytes, bytearray)) and png_bytes:
                    pending_attachments.append((bytes(png_bytes), mime_type, source))
```

Y donde se envían (≈línea 622-628), reemplazar:

```python
            for png_bytes, mime_type in pending_attachments:
                try:
                    await self._async_send_image(
                        png_bytes,
                        mime_type,
                        prompt=visual_capture_prompt("tool"),
                    )
```

por:

```python
            for png_bytes, mime_type, source in pending_attachments:
                try:
                    await self._async_send_image(
                        png_bytes,
                        mime_type,
                        prompt=visual_capture_prompt(source),
                    )
```

Y cambiar la anotación de `pending_attachments` (≈línea 550):

```python
        pending_attachments: list[tuple[bytes, str, str]] = []  # (bytes, mime, source)
```

- [ ] **Step 4: Correr los tests (deben pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py gemini/session.py tests/test_camera.py
git commit -m "feat(vision): tool camera_look + source en side-channel de imagen"
```

---

## Task 4: Wiring en `jarvis.py` + hotkey Ctrl+Shift+C

**Files:**
- Modify: `jarvis.py` (instanciar cámara, ctx, hotkey handler, teardown, label de overlay)
- Modify: `overlay/hotkeys.py` (callback + registro hotkey)

- [ ] **Step 1: Instanciar `CameraCapture`**

En `jarvis.py`, tras `self.screen = ScreenCapture(ROOT / "data" / "screenshots")` (línea 341):

```python
        from vision.camera import CameraCapture
        self.camera = CameraCapture(ROOT / "data" / "camera")
```

(El import local evita cargar cv2 si la línea se mueve; si prefieres, súbelo al bloque de imports junto a `from vision.screen import ScreenCapture`.)

- [ ] **Step 2: Pasar `camera` al `ToolContext`**

En `jarvis.py`, en la construcción de `ToolContext` (línea 463, tras `screen=self.screen,`):

```python
            camera=self.camera,
```

- [ ] **Step 3: Añadir el handler de hotkey**

En `jarvis.py`, tras `_on_capture_screen` (línea 729), añadir un handler gemelo:

```python
    def _on_capture_camera(self) -> None:
        if not self.gate.can_invoke(self.tracker, "gemini"):
            self._log("[CAMERA] captura ignorada: gemini bloqueado por budget")
            self._set_overlay_state("blocked")
            return
        try:
            frame = self.camera.capture()
            self._log(f"[CAMERA] capturada {frame.width}x{frame.height}: {frame.path.name}")
            self._tk(lambda: self.overlay.log_event("Camara capturada", "ok"))
            self._set_overlay_state("thinking")
            self.session.send_image(
                frame.jpeg_bytes,
                mime_type=frame.mime_type,
                prompt=visual_capture_prompt("camera"),
            )
        except Exception as exc:
            self._log(f"[CAMERA] error: {type(exc).__name__}: {exc}")
            self._set_overlay_state("idle" if self.mode == "PTT" else "listening")
```

- [ ] **Step 4: Registrar el callback en `HotkeyCallbacks`**

En `overlay/hotkeys.py`, en `class HotkeyCallbacks` (tras `on_capture_region`):

```python
    on_capture_camera: Callable[[], None] = lambda: None
```

En `HotkeyListener.start()`, tras el bloque `ctrl+alt+s`:

```python
        self._registered.append(keyboard.add_hotkey(
            "ctrl+shift+c", self.cb.on_capture_camera, suppress=False,
        ))
```

Actualizar el docstring de hotkeys (cabecera del archivo) añadiendo:
`  - Ctrl+Shift+C       -> Capture camara (foto on-demand)`

- [ ] **Step 5: Conectar el callback en `jarvis.py`**

En `jarvis.py`, en la construcción de `HotkeyCallbacks` (línea 508), tras `on_capture_region=self._on_capture_region,`:

```python
            on_capture_camera=self._on_capture_camera,
```

- [ ] **Step 6: Verificación de import (sanity)**

Run: `& H:\Python311\python.exe -c "import jarvis"`
Expected: sin errores de import (puede imprimir warnings de entorno, pero no traceback).

- [ ] **Step 7: Commit**

```bash
git add jarvis.py overlay/hotkeys.py
git commit -m "feat(vision): wiring camera_look en jarvis + hotkey Ctrl+Shift+C"
```

---

## Task 5: Config `.env.example` + system prompt (Fase 1)

**Files:**
- Modify: `.env.example`
- Modify: `gemini/system_prompt.py`

- [ ] **Step 1: Añadir vars a `.env.example`**

En `.env.example`, en una sección nueva "# === Camara / Vision ===":

```
JARVIS_CAMERA_INDEX=0
JARVIS_CAMERA_MAX_SIDE=1280
JARVIS_CAMERA_RETENTION_HOURS=24
JARVIS_CAMERA_FPS=1.0
JARVIS_CAMERA_WATCH_DEFAULT_S=90
JARVIS_CAMERA_WATCH_MAX_S=180
JARVIS_CAMERA_PREVIEW=1
JARVIS_CAMERA_PREVIEW_SIZE=480
```

- [ ] **Step 2: Documentar la tool en el system prompt**

En `gemini/system_prompt.py`, tras el bloque de `screen_look` (≈línea 196-215), añadir:

```
▸ camera_look(reason)
  Captura UNA foto de la camara frontal cuando Isaac te muestra algo fisico:
  "mira esto", "que es esto", "mira lo que tengo", un componente FPV, una nota,
  un multimetro. Para ver en continuo mientras trabaja, usa camera_watch ("modo vision").
```

- [ ] **Step 3: Smoke manual (lo corre Isaac, requiere webcam)**

Arrancar JARVIS (`jarvis_run.bat`), presionar `Ctrl+Shift+C` apuntando la cámara a un objeto.
Expected: JARVIS describe el objeto por voz; aparece "Camara capturada" en el overlay.

- [ ] **Step 4: Commit**

```bash
git add .env.example gemini/system_prompt.py
git commit -m "feat(vision): config camara + doc camera_look en system prompt"
```

**✅ Hito Fase 1:** "mira esto" funciona on-demand (cubre OCR→Obsidian e instrumentos puntual encadenando obs_memory/jarvis_browse).

---

# FASE 2 — Modo continuo ("modo visión") + preview

## Task 6: `session.send_video_frame()`

**Files:**
- Modify: `gemini/session.py` (método público + coroutine)
- Test: `tests/test_camera_watch.py`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_camera_watch.py`:

```python
import asyncio

from gemini.session import JarvisSession, SessionConfig, SessionCallbacks


class FakeLiveSession:
    def __init__(self):
        self.video_blobs = []

    async def send_realtime_input(self, **kwargs):
        if "video" in kwargs:
            self.video_blobs.append(kwargs["video"])


def test_async_send_video_builds_jpeg_blob():
    cfg = SessionConfig(api_key="x")
    sess = JarvisSession(cfg, SessionCallbacks())
    fake = FakeLiveSession()
    sess._session = fake
    asyncio.run(sess._async_send_video(b"\xff\xd8\xff_fake_jpeg"))
    assert len(fake.video_blobs) == 1
    blob = fake.video_blobs[0]
    assert blob.mime_type == "image/jpeg"
    assert blob.data == b"\xff\xd8\xff_fake_jpeg"
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_watch.py -k send_video -v`
Expected: FAIL — `_async_send_video` no existe.

- [ ] **Step 3: Implementar el método (gemelo de send_audio_chunk)**

En `gemini/session.py`, tras `send_audio_chunk`/`_async_send_audio` (≈línea 667):

```python
    def send_video_frame(self, jpeg_bytes: bytes) -> None:
        """Envia un frame de video (JPEG) por el canal realtime (modo vision)."""
        if not jpeg_bytes:
            return
        self._submit(self._async_send_video(jpeg_bytes))

    async def _async_send_video(self, jpeg_bytes: bytes) -> None:
        if self._session is None:
            return
        try:
            await self._session.send_realtime_input(
                video=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
            )
        except Exception as exc:
            self.cb.on_error(exc)
```

- [ ] **Step 4: Correr el test (debe pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_watch.py -k send_video -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gemini/session.py tests/test_camera_watch.py
git commit -m "feat(vision): session.send_video_frame para modo vision"
```

---

## Task 7: `CameraWatchController`

**Files:**
- Modify: `vision/camera.py` (añadir el controller)
- Test: `tests/test_camera_watch.py`

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/test_camera_watch.py`:

```python
import time
from pathlib import Path

from vision.camera import CameraCapture, CameraWatchController
from tests.test_camera import _factory


class FakeSession:
    def __init__(self):
        self.frames = []

    def send_video_frame(self, jpeg):
        self.frames.append(jpeg)


def _make_controller(tmp_path, gate_ok=True, **kw):
    cam = CameraCapture(out_dir=tmp_path, index=0, fps=50.0, device_factory=_factory())
    sess = FakeSession()
    events = []
    ctrl = CameraWatchController(
        camera=cam,
        session=sess,
        on_state=lambda active: events.append(active),
        on_frame=lambda f: None,
        gate_check=lambda: gate_ok,
        **kw,
    )
    return ctrl, cam, sess, events


def test_watch_streams_frames_then_autostops(tmp_path):
    ctrl, cam, sess, events = _make_controller(tmp_path)
    res = ctrl.start(duration_s=0.2)
    assert res["ok"] is True and res["active"] is True
    time.sleep(0.5)
    assert ctrl.is_active() is False         # auto-stop por timeout
    assert len(sess.frames) >= 1             # streameo al menos un frame
    assert events[0] is True and events[-1] is False  # overlay ON luego OFF


def test_watch_explicit_stop_releases_camera(tmp_path):
    ctrl, cam, sess, events = _make_controller(tmp_path)
    ctrl.start(duration_s=10)
    time.sleep(0.1)
    out = ctrl.stop()
    assert out["ok"] is True
    assert ctrl.is_active() is False


def test_watch_blocked_by_budget(tmp_path):
    ctrl, cam, sess, events = _make_controller(tmp_path, gate_ok=False)
    res = ctrl.start(duration_s=1)
    assert res["ok"] is False
    assert ctrl.is_active() is False
    assert sess.frames == []
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_watch.py -k watch -v`
Expected: FAIL — `CameraWatchController` no existe.

- [ ] **Step 3: Implementar el controller en `vision/camera.py`**

Añadir al final de `vision/camera.py`:

```python
class CameraWatchController:
    """Modo vision continuo acotado. Abre la camara, streamea frames a `fps`
    por session.send_video_frame() y se apaga solo por timeout/stop/budget.

    Threading: el loop corre en un hilo daemon propio. Nunca toca tkinter:
    notifica al exterior via callbacks (on_state, on_frame) que el consumidor
    (jarvis.py) marshalla con _tk().
    """

    def __init__(
        self,
        camera: "CameraCapture",
        session: Any,
        on_state: Callable[[bool], None] = lambda _a: None,
        on_frame: Callable[[Any], None] = lambda _f: None,
        gate_check: Callable[[], bool] = lambda: True,
        on_log: Callable[[str], None] = lambda _m: None,
        default_s: float | None = None,
        max_s: float | None = None,
    ) -> None:
        self.camera = camera
        self.session = session
        self.on_state = on_state
        self.on_frame = on_frame
        self.gate_check = gate_check
        self.on_log = on_log
        self.default_s = float(os.environ.get("JARVIS_CAMERA_WATCH_DEFAULT_S", "90")) if default_s is None else float(default_s)
        self.max_s = float(os.environ.get("JARVIS_CAMERA_WATCH_MAX_S", "180")) if max_s is None else float(max_s)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active = False
        self._lock = threading.Lock()

    def is_active(self) -> bool:
        return self._active

    def start(self, duration_s: float | None = None) -> dict:
        with self._lock:
            if self._active:
                return {"ok": True, "active": True, "note": "modo vision ya activo"}
            if not self.gate_check():
                return {"ok": False, "active": False, "error": "presupuesto Gemini agotado"}
            dur = self.default_s if not duration_s else float(duration_s)
            dur = max(1.0, min(dur, self.max_s))
            try:
                self.camera.open()
            except Exception as exc:
                return {"ok": False, "active": False, "error": f"{type(exc).__name__}: {exc}"}
            self._stop.clear()
            self._active = True
            self._thread = threading.Thread(
                target=self._loop, args=(dur,), name="JarvisCameraWatch", daemon=True
            )
            self._thread.start()
        self.on_state(True)
        self.on_log(f"[WATCH] modo vision ON ({dur:.0f}s)")
        return {"ok": True, "active": True, "duration_s": dur}

    def stop(self) -> dict:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=3.0)
        self._teardown()
        return {"ok": True, "active": False}

    def _loop(self, duration_s: float) -> None:
        period = 1.0 / max(0.1, self.camera.fps)
        deadline = time.time() + duration_s
        try:
            while not self._stop.is_set() and time.time() < deadline:
                if not self.gate_check():
                    self.on_log("[WATCH] stop: presupuesto agotado")
                    break
                try:
                    frame = self.camera.read_frame()
                    self.session.send_video_frame(frame.jpeg_bytes)
                    self.on_frame(frame)
                except Exception as exc:
                    self.on_log(f"[WATCH] frame error: {type(exc).__name__}: {exc}")
                self._stop.wait(period)
        finally:
            self._teardown()

    def _teardown(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            self._thread = None
        try:
            self.camera.close()
        except Exception:
            pass
        self.on_state(False)
        self.on_log("[WATCH] modo vision OFF")
```

- [ ] **Step 4: Correr los tests (deben pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_watch.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add vision/camera.py tests/test_camera_watch.py
git commit -m "feat(vision): CameraWatchController con auto-stop y budget gate"
```

---

## Task 8: Tool `camera_watch` + wiring del controller

**Files:**
- Modify: `memory/tools.py` (decl, handler, dispatch, declarations)
- Modify: `jarvis.py` (instanciar controller, ctx, teardown)
- Test: `tests/test_camera_watch.py`

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/test_camera_watch.py`:

```python
def test_camera_watch_tool_start_stop(tmp_path):
    from memory.tools import ToolContext, camera_watch

    ctrl, cam, sess, events = _make_controller(tmp_path)
    ctx = ToolContext(vault=None, rag=None, camera_watch=ctrl)

    out = camera_watch(ctx, action="start", duration_s=10)
    assert out["ok"] is True and out["active"] is True
    out2 = camera_watch(ctx, action="stop")
    assert out2["ok"] is True and out2["active"] is False


def test_camera_watch_tool_without_controller():
    from memory.tools import ToolContext, camera_watch

    ctx = ToolContext(vault=None, rag=None, camera_watch=None)
    out = camera_watch(ctx, action="start")
    assert out["ok"] is False
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_watch.py -k tool -v`
Expected: FAIL — `camera_watch` no existe.

- [ ] **Step 3a: Declaración `CAMERA_WATCH_DECL`**

En `memory/tools.py`, tras `CAMERA_LOOK_DECL`:

```python
CAMERA_WATCH_DECL = types.FunctionDeclaration(
    name="camera_watch",
    description=(
        "Activa o desactiva el MODO VISION: JARVIS ve en continuo por la camara "
        "frontal mientras Isaac trabaja o le muestra algo en movimiento. Usa "
        "action='start' cuando Isaac diga 'modo vision', 'mira lo que hago', "
        "'guiame con esto', 'observa mientras...'. Usa action='stop' cuando diga "
        "'ya', 'salir de modo vision', 'deja de mirar', 'listo'. El modo se apaga "
        "solo tras unos segundos por seguridad. Para una sola foto usa camera_look."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "action": types.Schema(
                type=types.Type.STRING,
                description="start para activar, stop para desactivar.",
            ),
            "duration_s": types.Schema(
                type=types.Type.NUMBER,
                description="Segundos de observacion al iniciar (default 90, max 180).",
            ),
        },
        required=["action"],
    ),
)
```

- [ ] **Step 3b: Handler `camera_watch`**

En `memory/tools.py`, tras `camera_look`:

```python
def camera_watch(ctx: ToolContext, action: str = "start", duration_s: float | None = None) -> dict:
    """Activa/desactiva el modo vision continuo (CameraWatchController)."""
    ctrl = ctx.camera_watch
    if ctrl is None:
        return {"ok": False, "active": False, "error": "Modo vision no configurado."}
    act = (action or "start").strip().lower()
    if act == "stop":
        return ctrl.stop()
    if act == "start":
        return ctrl.start(duration_s=duration_s)
    return {"ok": False, "active": ctrl.is_active(), "error": f"action invalida: {action}"}
```

- [ ] **Step 3c: Registrar decl + dispatch**

En `all_function_declarations()`, tras `CAMERA_LOOK_DECL,`:

```python
        CAMERA_WATCH_DECL,
```

En `ToolDispatcher.__init__` `self._tools`, tras `camera_look`:

```python
            "camera_watch": lambda **kw: camera_watch(ctx, **kw),
```

- [ ] **Step 3d: Instanciar el controller en `jarvis.py`**

En `jarvis.py`, tras `self.dispatcher = ToolDispatcher(self.tool_ctx)` NO — el controller necesita la session, que se crea después. Crear el controller tras crear `self.session` (busca `self.session =` en jarvis.py) y luego asignarlo al ctx ya construido:

```python
        from vision.camera import CameraWatchController
        self.camera_watch = CameraWatchController(
            camera=self.camera,
            session=self.session,
            on_state=lambda active: self._tk(
                lambda: self.overlay.set_camera_active(active)
            ),
            on_frame=lambda frame: self._tk(
                lambda: self.overlay.update_camera_preview(frame)
            ),
            gate_check=lambda: self.gate.can_invoke(self.tracker, "gemini"),
            on_log=self._log,
        )
        self.tool_ctx.camera_watch = self.camera_watch
```

> Nota: `self.tool_ctx.camera_watch = ...` actualiza el dataclass ya creado (el dispatcher referencia el mismo `ctx`, así que la inyección tardía funciona). `set_camera_active`/`update_camera_preview` se implementan en Task 9.

- [ ] **Step 3e: Teardown**

En `jarvis.py`, en `stop()` (busca `self.hotkey_listener.stop()`), antes de detener hotkeys:

```python
        try:
            if getattr(self, "camera_watch", None):
                self.camera_watch.stop()
        except Exception:
            pass
```

- [ ] **Step 4: Correr los tests (deben pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_watch.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py jarvis.py tests/test_camera_watch.py
git commit -m "feat(vision): tool camera_watch (modo vision) + wiring controller"
```

---

## Task 9: Preview live + indicador de cámara en overlay

**Files:**
- Create: `overlay/camera_preview.py`
- Modify: `overlay/window.py` (clase `JarvisOverlay` — overlay tkinter por defecto)
- Modify: `overlay/web_overlay.py` (clase `WebJarvisOverlay` — overlay web, opt-in `JARVIS_UI=web`)

> **Hay DOS overlays** seleccionados por `overlay/factory.py`: `JarvisOverlay`
> (`overlay/window.py`, default `JARVIS_UI=tk`) y `WebJarvisOverlay`
> (`overlay/web_overlay.py`, `JARVIS_UI=web`). **Ambos** exponen `self.root` (un
> `tk.Tk()` real — ya lo usa `RegionSelector`), así que el preview (un `Toplevel`
> tkinter) funciona con cualquiera. Añade los 3 métodos a **ambas clases** para que
> el modo visión funcione con cualquier UI. Los métodos son idénticos en las dos.

- [ ] **Step 1: Implementar `overlay/camera_preview.py`**

Crear `overlay/camera_preview.py`:

```python
"""
overlay/camera_preview.py - Ventana de preview de lo que ve JARVIS por la camara.

DEBE crearse y actualizarse SOLO en el main thread (tkinter). El consumidor
(jarvis.py) ya marshalla via _tk(), asi que los metodos publicos asumen main thread.
Pinta cada CameraFrame (JPEG) y dibuja el crosshair (reticula central + box semantico).
"""

from __future__ import annotations

import io
import os
import tkinter as tk
from typing import Any

from PIL import Image, ImageTk


class CameraPreviewWindow:
    def __init__(self, parent: tk.Misc) -> None:
        self._parent = parent
        self._top: tk.Toplevel | None = None
        self._label: tk.Label | None = None
        self._canvas: tk.Canvas | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._size = int(os.environ.get("JARVIS_CAMERA_PREVIEW_SIZE", "480"))
        self._enabled = os.environ.get("JARVIS_CAMERA_PREVIEW", "1") == "1"
        self._box: tuple[int, int, int, int] | None = None  # px en coords del preview
        self._box_label: str = ""

    def show(self) -> None:
        if not self._enabled or self._top is not None:
            return
        self._top = tk.Toplevel(self._parent)
        self._top.title("👁 JARVIS — Camara")
        self._top.attributes("-topmost", True)
        self._top.protocol("WM_DELETE_WINDOW", self.hide)
        self._canvas = tk.Canvas(
            self._top, width=self._size, height=self._size,
            bg="black", highlightthickness=0,
        )
        self._canvas.pack()

    def hide(self) -> None:
        self._box = None
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
        self._top = None
        self._canvas = None
        self._photo = None

    def update_frame(self, jpeg_bytes: bytes) -> None:
        if not self._enabled or self._canvas is None:
            return
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        img.thumbnail((self._size, self._size), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self._canvas.delete("all")
        ox = (self._size - img.width) // 2
        oy = (self._size - img.height) // 2
        self._canvas.create_image(ox, oy, anchor="nw", image=self._photo)
        self._draw_crosshair(img.width, img.height, ox, oy)

    def set_focus_box(self, box_px: tuple[int, int, int, int] | None, label: str = "") -> None:
        """box_px = (x1, y1, x2, y2) en coords del preview, o None para limpiar."""
        self._box = box_px
        self._box_label = label

    def _draw_crosshair(self, w: int, h: int, ox: int, oy: int) -> None:
        c = self._canvas
        if c is None:
            return
        if self._box is not None:
            x1, y1, x2, y2 = self._box
            c.create_rectangle(x1, y1, x2, y2, outline="#39FF14", width=2)
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            c.create_line(cx - 10, cy, cx + 10, cy, fill="#39FF14", width=2)
            c.create_line(cx, cy - 10, cx, cy + 10, fill="#39FF14", width=2)
            if self._box_label:
                c.create_text(x1 + 2, y1 - 8, anchor="w", fill="#39FF14",
                              text=self._box_label, font=("Segoe UI", 9, "bold"))
        else:
            # Reticula central tenue
            cx, cy = ox + w // 2, oy + h // 2
            c.create_line(cx - 12, cy, cx + 12, cy, fill="#39FF1466", width=1)
            c.create_line(cx, cy - 12, cx, cy + 12, fill="#39FF1466", width=1)
```

- [ ] **Step 2: Añadir métodos al overlay principal**

En la clase del overlay (la de `log_event`), en `__init__` crear el preview (tras construir `self.root`):

```python
        from overlay.camera_preview import CameraPreviewWindow
        self._camera_preview = CameraPreviewWindow(self.root)
```

Y añadir los métodos públicos:

```python
    def set_camera_active(self, active: bool) -> None:
        """Muestra/oculta el preview e indica visualmente que la camara esta ON."""
        if active:
            self._camera_preview.show()
            self.log_event("👁 CAMARA ACTIVA (modo vision)", "warn")
        else:
            self._camera_preview.hide()
            self.log_event("Camara apagada", "ok")

    def update_camera_preview(self, frame) -> None:
        self._camera_preview.update_frame(frame.jpeg_bytes)

    def set_camera_focus(self, box_px, label: str = "") -> None:
        self._camera_preview.set_focus_box(box_px, label)
```

> Ajusta los niveles de `log_event` ("warn"/"ok") a los que tu overlay soporte
> (los mismos que usa el resto de jarvis.py).

- [ ] **Step 3: Smoke manual (lo corre Isaac)**

Arrancar JARVIS, decir "modo visión". Expected: se abre la ventana de preview mostrando
la cámara en vivo con retícula central; el overlay marca "CÁMARA ACTIVA"; al decir "ya"
se cierra y marca "Camara apagada".

- [ ] **Step 4: Commit**

```bash
git add overlay/camera_preview.py overlay/<archivo_overlay>.py
git commit -m "feat(vision): preview live + indicador de camara en overlay"
```

---

## Task 10: System prompt "modo visión"

**Files:**
- Modify: `gemini/system_prompt.py`

- [ ] **Step 1: Documentar `camera_watch` y el disparador "modo visión"**

En `gemini/system_prompt.py`, tras el bloque `camera_look` (Task 5):

```
▸ camera_watch(action, duration_s)
  MODO VISION: ver en continuo por la camara. action='start' cuando Isaac diga
  "modo vision", "mira lo que hago", "guiame con esto", "observa mientras...".
  action='stop' cuando diga "ya", "salir de modo vision", "deja de mirar", "listo".
  Mientras este activo, comenta de forma breve y natural lo que ve; no narres cada
  frame, solo lo relevante. Se apaga solo por seguridad tras unos segundos.
```

- [ ] **Step 2: Commit**

```bash
git add gemini/system_prompt.py
git commit -m "docs(vision): system prompt para modo vision (camera_watch)"
```

**✅ Hito Fase 2:** "modo visión" funciona — JARVIS ve en vivo bajo comando, con preview y auto-stop.

---

# FASE 2.5 — Crosshair semántico (`camera_focus`)

## Task 11: Detección de bounding box (one-shot `generate_content`)

**Files:**
- Create: `vision/detect.py`
- Modify: `telemetry/costs.py` (fila de pricing del modelo de detección)
- Test: `tests/test_camera_focus.py`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_camera_focus.py`:

```python
from vision.detect import parse_box_2d, box_to_pixels


def test_parse_box_2d_from_json():
    out = parse_box_2d('{"label": "multimetro", "box_2d": [100, 200, 700, 800]}')
    assert out is not None
    assert out["label"] == "multimetro"
    assert out["box_2d"] == [100, 200, 700, 800]


def test_parse_box_2d_handles_garbage():
    assert parse_box_2d("no json aqui") is None
    assert parse_box_2d('{"label": "x"}') is None  # sin box_2d


def test_box_to_pixels_denormalizes_0_1000():
    # box_2d = [ymin, xmin, ymax, xmax] en 0..1000
    px = box_to_pixels([0, 0, 1000, 1000], width=480, height=480, ox=0, oy=0)
    assert px == (0, 0, 480, 480)
    px2 = box_to_pixels([250, 250, 750, 750], width=400, height=400, ox=40, oy=40)
    assert px2 == (140, 140, 340, 340)  # 0.25*400+40 .. 0.75*400+40
```

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_focus.py -v`
Expected: FAIL — `vision.detect` no existe.

- [ ] **Step 3: Implementar `vision/detect.py`**

Crear `vision/detect.py`:

```python
"""
vision/detect.py - Deteccion one-shot de bounding box para el crosshair.

Usa client.models.generate_content (NO la sesion Live) con salida estructurada
para obtener {label, box_2d} en coords normalizadas 0..1000 (convencion de
spatial understanding de Gemini). Funciones de parseo separadas para testear
sin red.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_DETECT_MODEL = os.environ.get("JARVIS_CAMERA_DETECT_MODEL", "gemini-3.1-flash")

_PROMPT = (
    "Detecta el objeto principal que se muestra a la camara. Devuelve SOLO un JSON "
    '{"label": "<nombre corto>", "box_2d": [ymin, xmin, ymax, xmax]} con coordenadas '
    "normalizadas de 0 a 1000. Sin texto extra."
)


def parse_box_2d(text: str) -> dict | None:
    """Extrae {label, box_2d} de la respuesta del modelo. None si no hay box valido."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    box = data.get("box_2d")
    if not (isinstance(box, list) and len(box) == 4):
        return None
    try:
        box = [int(v) for v in box]
    except Exception:
        return None
    return {"label": str(data.get("label", "")), "box_2d": box}


def box_to_pixels(box_2d, width: int, height: int, ox: int = 0, oy: int = 0):
    """box_2d=[ymin,xmin,ymax,xmax] en 0..1000 -> (x1,y1,x2,y2) px del preview."""
    ymin, xmin, ymax, xmax = box_2d
    x1 = int(xmin / 1000 * width) + ox
    y1 = int(ymin / 1000 * height) + oy
    x2 = int(xmax / 1000 * width) + ox
    y2 = int(ymax / 1000 * height) + oy
    return (x1, y1, x2, y2)


def detect_object(client: Any, jpeg_bytes: bytes) -> dict | None:
    """Llamada real a Gemini (one-shot). Devuelve {label, box_2d} o None.

    `client` es un genai.Client ya construido (lo provee jarvis.py). Se aisla aqui
    para poder mockearlo en tests sin tocar red.
    """
    from google.genai import types
    try:
        resp = client.models.generate_content(
            model=_DETECT_MODEL,
            contents=[
                types.Part.from_bytes(data=jpeg_bytes, mime_type="image/jpeg"),
                types.Part(text=_PROMPT),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return parse_box_2d(getattr(resp, "text", "") or "")
    except Exception:
        return None
```

- [ ] **Step 4: Añadir pricing del modelo de detección**

En `telemetry/costs.py`, dentro de `PRICING` (sección Gemini), añadir:

```python
    # Deteccion one-shot para crosshair (camera_focus). generate_content, no Live.
    "gemini-3.1-flash:vision-in": ModelPricing(input=0.15, output=0.0),
    "gemini-3.1-flash:text-out": ModelPricing(input=0.0, output=0.40),
```

- [ ] **Step 5: Correr el test (debe pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_focus.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add vision/detect.py telemetry/costs.py tests/test_camera_focus.py
git commit -m "feat(vision): deteccion one-shot de bounding box + pricing"
```

---

## Task 12: Tool `camera_focus` + dibujo del crosshair

**Files:**
- Modify: `memory/tools.py` (decl, handler, dispatch, declarations)
- Modify: `jarvis.py` (exponer client + preview al ctx para focus)
- Test: `tests/test_camera_focus.py`

- [ ] **Step 1: Escribir el test que falla**

Añadir a `tests/test_camera_focus.py`:

```python
def test_camera_focus_tool_uses_last_frame(tmp_path, monkeypatch):
    from memory.tools import ToolContext, camera_focus
    from vision.camera import CameraCapture
    import vision.detect as detect
    from tests.test_camera import _factory

    cam = CameraCapture(out_dir=tmp_path, index=0, device_factory=_factory())
    cam.capture()  # deja un last frame

    # Mock de la deteccion para no tocar red
    monkeypatch.setattr(detect, "detect_object",
                        lambda client, jpeg: {"label": "placa", "box_2d": [100, 100, 500, 500]})

    drawn = {}
    ctx = ToolContext(vault=None, rag=None, camera=cam)
    ctx.genai_client = object()  # placeholder; el mock ignora client
    ctx.on_focus_box = lambda box_2d, label: drawn.update(box=box_2d, label=label)

    out = camera_focus(ctx, label="que es")
    assert out["found"] is True
    assert out["label"] == "placa"
    assert drawn["box"] == [100, 100, 500, 500]


def test_camera_focus_no_frame():
    from memory.tools import ToolContext, camera_focus
    ctx = ToolContext(vault=None, rag=None, camera=None)
    out = camera_focus(ctx)
    assert out["found"] is False
```

> Nota: este test usa atributos `genai_client` y `on_focus_box` en `ToolContext`.
> Añádelos al dataclass en el Step 3a.

- [ ] **Step 2: Correr el test (debe fallar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_focus.py -k focus_tool -v`
Expected: FAIL — `camera_focus` y los campos no existen.

- [ ] **Step 3a: Añadir campos a `ToolContext`**

En `memory/tools.py`, en `class ToolContext`, tras `camera_watch`:

```python
    genai_client: Any | None = None
    on_focus_box: Callable[..., None] | None = None
```

- [ ] **Step 3b: Declaración `CAMERA_FOCUS_DECL`**

En `memory/tools.py`, tras `CAMERA_WATCH_DECL`:

```python
CAMERA_FOCUS_DECL = types.FunctionDeclaration(
    name="camera_focus",
    description=(
        "Marca con un crosshair el objeto principal que Isaac te esta mostrando por "
        "la camara y dibuja un recuadro sobre el en el preview. Usala cuando Isaac "
        "diga 'enfoca esto', 'que es esto exactamente', 'senala lo que ves', "
        "'marca el objeto'. Requiere que haya una captura reciente (camera_look o "
        "modo vision activo)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "label": types.Schema(
                type=types.Type.STRING,
                description="Pista opcional de que objeto enfocar.",
            ),
        },
    ),
)
```

- [ ] **Step 3c: Handler `camera_focus`**

En `memory/tools.py`, tras `camera_watch`:

```python
def camera_focus(ctx: ToolContext, label: str = "") -> dict:
    """Detecta el objeto principal del ultimo frame y dispara el crosshair."""
    import vision.detect as detect

    if ctx.camera is None or ctx.camera.last is None:
        return {"found": False, "error": "No hay captura reciente de camara."}
    frame = ctx.camera.last
    result = detect.detect_object(ctx.genai_client, frame.jpeg_bytes)
    if result is None:
        return {"found": False, "note": "No pude ubicar el objeto con precision."}
    if ctx.on_focus_box is not None:
        try:
            ctx.on_focus_box(result["box_2d"], result.get("label", ""))
        except Exception:
            pass
    return {"found": True, "label": result.get("label", ""), "box_2d": result["box_2d"]}
```

- [ ] **Step 3d: Registrar decl + dispatch**

En `all_function_declarations()`, tras `CAMERA_WATCH_DECL,`:

```python
        CAMERA_FOCUS_DECL,
```

En `ToolDispatcher.__init__` `self._tools`, tras `camera_watch`:

```python
            "camera_focus": lambda **kw: camera_focus(ctx, **kw),
```

- [ ] **Step 3e: Wiring en `jarvis.py`**

Donde se inyecta `camera_watch` al ctx (Task 8 Step 3d), añadir el client y el callback de focus.
El `genai.Client` ya existe dentro de `JarvisSession` (`self._client`) pero es privado; lo más
limpio es crear uno aparte para detección (no Live):

```python
        from google import genai
        self.tool_ctx.genai_client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],  # confirmado: misma var que la session
            http_options={"api_version": "v1beta"},
        )
        self.tool_ctx.on_focus_box = lambda box_2d, lbl: self._tk(
            lambda: self._apply_focus_box(box_2d, lbl)
        )
```

Y un helper que convierte box_2d→px usando el tamaño del preview:

```python
    def _apply_focus_box(self, box_2d, label: str) -> None:
        from vision.detect import box_to_pixels
        size = int(os.environ.get("JARVIS_CAMERA_PREVIEW_SIZE", "480"))
        # El preview centra la imagen; aproximamos con la imagen a tamaño completo.
        px = box_to_pixels(box_2d, width=size, height=size, ox=0, oy=0)
        self.overlay.set_camera_focus(px, label)
```

> `GEMINI_API_KEY` confirmada en `.env.example` (línea 8) — es la misma que recibe
> `SessionConfig.api_key`.

- [ ] **Step 4: Correr los tests (deben pasar)**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera_focus.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add memory/tools.py jarvis.py tests/test_camera_focus.py
git commit -m "feat(vision): tool camera_focus + crosshair sobre el objeto"
```

---

## Task 13: System prompt focus + suite completa + cierre

**Files:**
- Modify: `gemini/system_prompt.py`

- [ ] **Step 1: Documentar `camera_focus`**

En `gemini/system_prompt.py`, tras el bloque `camera_watch`:

```
▸ camera_focus(label)
  Dibuja un crosshair sobre el objeto que Isaac muestra cuando diga "enfoca esto",
  "senala lo que ves", "marca el objeto". Requiere captura reciente (camera_look
  o modo vision). Tras enfocar, comenta brevemente que es.
```

- [ ] **Step 2: Correr TODA la suite de cámara**

Run: `& H:\Python311\python.exe -m pytest tests/test_camera.py tests/test_camera_watch.py tests/test_camera_focus.py tests/test_visual_prompts.py -v`
Expected: PASS (toda la suite)

- [ ] **Step 3: Correr la suite completa de JARVIS (no romper nada)**

Run: `& H:\Python311\python.exe -m pytest -q`
Expected: misma cantidad de PASS que antes + los nuevos; 0 fallos nuevos.

- [ ] **Step 4: Commit**

```bash
git add gemini/system_prompt.py
git commit -m "docs(vision): system prompt camera_focus + cierre Fase 2.5"
```

**✅ Hito Fase 2.5:** crosshair semántico sobre el objeto enfocado.

---

## Smoke test manual final (lo corre Isaac)

1. **On-demand:** apuntar cámara a un componente FPV → "JARVIS, mira esto" → describe.
2. **OCR→Obsidian:** mostrar una nota en papel → "guarda esto en Obsidian" → encadena obs_memory.
3. **Modo visión:** "modo visión" → preview con retícula → mover una placa → comenta en vivo → "ya" → se cierra.
4. **Focus:** durante modo visión → "enfoca el conector" → recuadro verde sobre el objeto.
5. **Investigar:** "busca info de este chip" → encadena jarvis_browse sobre lo que ve.

---

## Roadmap futuro (fuera de este plan)

**Fase 3** (diferida por riesgo de estabilidad, lección de modo LIBRE): pre-filtro local
OpenCV de presencia/movimiento → presencia↔ProactivityEngine y modo seguridad/ausencia.
Se planificará con su propio spec cuando Fase 2/2.5 esté estable en uso real.
