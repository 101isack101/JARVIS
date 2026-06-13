# Diseño: Visión por Cámara para JARVIS

- **Fecha:** 2026-06-06
- **Autor:** Isaac + Claude (brainstorming)
- **Estado:** Aprobado — listo para plan de implementación
- **Enfoque elegido:** B (motor de cámara en capas: on-demand + continuo acotado), construido en fases.

---

## 1. Problema y objetivo

JARVIS hoy solo "ve" la pantalla (`vision/screen.py` → `send_image` → Gemini Live).
Isaac quiere que, **bajo su comando**, JARVIS pueda **ver por la webcam frontal lo que
le muestre en tiempo real, analizarlo y —si se lo pide— investigarlo**.

Casos de uso priorizados por Isaac:
1. **Mostrar objetos a demanda** ("mira esto").
2. **Conciencia continua acotada** ("modo visión": que vea en vivo mientras trabaja/habla).
3. **Trabajo manual / FPV / electrónica** (guiarse mientras suelda o configura, manos ocupadas).

Capacidades transversales pedidas:
- **Analizar** cualquier cosa que se le muestre cuando Isaac lo indique.
- **Investigar** lo que ve si Isaac lo pide (encadenar tools existentes).
- **OCR → Obsidian** y **leer instrumentos** (multímetro, cargador LiPo, valores Betaflight)
  como casos particulares de "analiza lo que te muestro".
- **Preview en vivo** de lo que JARVIS ve.
- **Crosshair** sobre el objeto que JARVIS enfoca cuando Isaac se lo indica.

### No-objetivos (de este spec)
- Comprensión de video a alta tasa (30fps) tipo film.
- Multi-cámara (Isaac tiene solo webcam frontal; FPV se cubre reorientándola).
- Pre-filtro local de presencia/movimiento (diferido a Fase 3 — ver Roadmap).
- Reconocimiento facial / identidad.

---

## 2. Restricciones y contexto del codebase

- **Modelo:** Gemini Live `gemini-3.1-flash-live-preview` sobre WebSocket bidireccional
  (`gemini/session.py`), con `send_realtime_input(audio=...)` ya en uso.
- **API verificada (context7, google-genai):** `session.send_realtime_input(video=...)`
  acepta frames de imagen (gemelo del path de audio). El crosshair semántico usa
  `client.models.generate_content` con `response_json_schema` (salida estructurada),
  **fuera** de la sesión Live, porque obtener coordenadas precisas por el canal de voz
  Live es frágil y NO se asume.
- **Threading (gotcha #1 de Isaac):** todo tkinter debe ejecutarse en el main thread vía
  el marshaller `_tk()`. Los hilos de cámara solo producen bytes; nunca tocan UI.
- **Presupuesto:** existe `gate.can_invoke(tracker, "gemini")` + `TokenTracker`.
  Tarifa `vision-in` = $0.15/1M tokens (`telemetry/costs.py`).
- **Patrón de tool con imagen probado:** `screen_look` usa el side-channel `__attach_image`
  que `gemini/session.py::_handle_tool_call` extrae y envía vía `send_client_content`.
- **Entorno:** Python global `H:\Python311` (sin venv por proyecto). Windows 10.

---

## 3. Arquitectura

```
                        ┌─────────────────────────────────────┐
                        │   Gemini Live (gemini-3.1-flash)     │
                        │   audio ⇄  +  NUEVO: video frames    │
                        └──────────────▲──────────┬────────────┘
 send_realtime_input(video=)  │          │ tool_call(camera_look / camera_watch)
                        ┌───────┴──────────▼───────────┐
                        │   gemini/session.py            │
                        │   + send_video_frame()  (NEW)  │
                        └──────▲─────────────────▲───────┘
       frames ~1fps (Fase 2)   │                 │ __attach_image (Fase 1)
                        ┌───────┴───────┐  ┌──────┴──────────────┐
                        │ CameraWatch   │  │ camera_look tool     │
                        │ Controller    │  │ (memory/tools.py)    │
                        │ (hilo daemon) │  └──────┬──────────────┘
                        └───┬───────▲───┘         │ capture()
              frame bytes   │       │ capture()   │
            (vía _tk a UI)  │  ┌────┴─────────────▼────┐
                        ┌───▼──┴───────────────────────┐
                        │   vision/camera.py  (NUEVO)    │  ← gemelo de vision/screen.py
                        │   CameraCapture (cv2)          │
                        └────────────────────────────────┘
                                  │ frame + (box_2d en focus)
                        ┌─────────▼───────────────────┐
                        │ overlay/camera_preview.py     │  preview live + crosshair
                        │ (Toplevel tkinter, main thread)│
                        └────────────────────────────────┘
```

**Principio rector:** un motor (`CameraCapture`), tres consumidores (tool on-demand,
controller continuo, preview UI). La cámara nunca toca tkinter.

---

## 4. Componentes

### 4.1 `vision/camera.py` (NUEVO — gemelo de `vision/screen.py`)

- `CameraFrame` (dataclass): `path, width, height, jpeg_bytes, mime_type="image/jpeg"`,
  `as_dict()`. → JPEG (no PNG): la webcam comprime ~10× mejor y el path de video realtime
  espera `image/jpeg`.
- `CameraCapture`:
  - `__init__(out_dir, index, max_side=1280, retention_hours=None, fps=1.0)`.
  - `cv2.VideoCapture(index, cv2.CAP_DSHOW)` (backend Windows correcto).
  - `capture() -> CameraFrame`: patrón **open → descartar ~3 frames warm-up
    (auto-exposición) → grab 1 → BGR→RGB → resize `max_side` → encode JPEG →
    guardar en `data/camera/` → close**. (On-demand: no retiene el dispositivo.)
  - `open()` / `read_frame()` / `close()`: para el modo watch (abre 1 vez, streamea, cierra).
  - `Lock` alrededor del dispositivo (acceso serializado entre look y watch).
  - `cleanup_old()`: retención configurable (igual que screenshots).

### 4.2 `gemini/session.py` — método `send_video_frame(jpeg_bytes)` (NUEVO)

- Gemelo de `send_audio_chunk`:
  `_submit(_async_send_video(...))` →
  `await self._session.send_realtime_input(video=types.Blob(data=jpeg, mime_type="image/jpeg"))`.
- Hereda el comportamiento de `_submit`: si el loop no está listo, descarta el frame
  silenciosamente (tolerante a reconexión).

### 4.3 `memory/tools.py` — declaraciones + handlers (NUEVO)

- `ToolContext` gana campo `camera` (y referencia al watch controller / session según diseño).
- **`CAMERA_LOOK_DECL` / `camera_look(ctx, reason="")`** (Fase 1):
  espejo de `screen_look`; `ctx.camera.capture()` → dict + `__attach_image{jpeg_bytes}`.
  Misma guía anti-datos-sensibles en la descripción.
- **`CAMERA_WATCH_DECL` / `camera_watch(ctx, action, duration_s=90)`** (Fase 2):
  `action = start | stop`; `duration_s` default 90, cap `JARVIS_CAMERA_WATCH_MAX_S` (180).
  Frase de activación de Isaac: **"modo visión"** → `start`; "salir de modo visión" / "ya"
  → `stop`. (El system prompt debe mapear estas frases a la tool.)
- **`CAMERA_FOCUS_DECL` / `camera_focus(ctx, label="")`** (Fase 2.5):
  toma el frame actual → detección one-shot → devuelve `box_2d` + `label` para el crosshair.

### 4.4 `vision/camera.py::CameraWatchController` (NUEVO — Fase 2)

- `start(duration_s)`: gate de presupuesto → `camera.open()` → indicador overlay
  "👁 CÁMARA ACTIVA" vía `_tk()` → hilo daemon que loopea a `fps`:
  `read_frame()` → `session.send_video_frame(jpeg)` → push del frame al preview vía `_tk()`.
- Auto-stop por: timeout (`duration_s`), `camera_watch(stop)`, comando de voz, presupuesto
  agotado, o shutdown de la app.
- `stop()`: señala al hilo, `join(timeout)`, `camera.close()`, overlay vuelve a normal.
- Toda excepción del hilo se atrapa y loguea (usa `_install_crash_capture`); jamás propaga.

### 4.5 `overlay/camera_preview.py` (NUEVO — Fase 2 / 2.5)

- `Toplevel` tkinter pequeño, creado/actualizado **solo en main thread** vía `_tk()`.
- **Preview live:** pinta cada frame con `PIL.ImageTk.PhotoImage` (100% local, $0 cloud).
- **Crosshair híbrido:**
  - **Retícula central siempre** durante watch (+ opcional realce OpenCV local de la región
    con más movimiento/saliencia). Instantáneo, sin nube.
  - **Mira semántica bajo orden** (`camera_focus`): detección one-shot
    `client.models.generate_content(model=<flash>, contents=[frame, prompt], config=
    {response_json_schema: {label:str, box_2d:[ymin,xmin,ymax,xmax] 0..1000}})` →
    desnormaliza al tamaño del preview → dibuja rectángulo + etiqueta + cruz.
- `ImageTk.PhotoImage` nunca se instancia fuera del main thread (gotcha #1).

---

## 5. Flujos de datos

**On-demand (`camera_look`, Fase 1):**
```
"JARVIS, mira esto"
 → Gemini llama camera_look(reason)
 → dispatcher (asyncio.to_thread) → CameraCapture.capture()
 → dict + __attach_image{jpeg}
 → session: send_tool_response → send_client_content(frame + prompt)
 → Gemini analiza → (opcional) encadena jarvis_browse / ask_claude_deep / obs_memory
 → responde por voz
```

**Continuo (`camera_watch` / "modo visión", Fase 2):**
```
"modo visión" / "mira lo que hago"
 → Gemini llama camera_watch(start, duration_s=90)
 → CameraWatchController: gate → camera.open() → overlay "👁 ACTIVA" + preview ON
 → hilo daemon @1fps: read_frame → session.send_video_frame(jpeg) + push a preview
 → Gemini ve en continuo, responde por VAD mientras Isaac habla
 → STOP: "ya"/"salir de modo visión" (Gemini→stop) | timeout 90s | presupuesto | shutdown
 → camera.close() → overlay normal → preview OFF
```

**Crosshair semántico (`camera_focus`, Fase 2.5):**
```
"enfoca esto" / "qué es esto"
 → Gemini llama camera_focus(label?)
 → toma frame actual → generate_content one-shot con JSON schema {label, box_2d}
 → preview dibuja rectángulo + cruz + etiqueta sobre el objeto
 → Gemini comenta por voz
```

---

## 6. Manejo de errores

- **Cámara ocupada / no disponible:** `capture()` → `{"captured": False, "error": ...}`;
  la tool lo reporta hablado, sin crash.
- **Permiso de Windows denegado:** detectar fallo de `open()` → error accionable
  ("revisa Configuración → Privacidad → Cámara").
- **Hilo de watch:** toda excepción atrapada y logueada; nunca propaga a tkinter.
- **Regla de oro:** ningún thread de cámara toca tkinter — todo por `_tk()`.
- **Shutdown limpio:** `camera.close()` en `finally`; controller registrado en teardown.
- **Reconexión Live durante watch:** frames se descartan mientras el loop no está listo
  (comportamiento de `_submit`); el timer sigue, así que igual auto-para.
- **Detección (`camera_focus`) falla o sin box:** preview cae a la retícula central; voz
  informa "no pude ubicarlo con precisión".

---

## 7. Privacidad y seguridad

- Cámara **off por defecto**; solo abre por tool/hotkey explícito.
- **Timeout duro** en watch (default 90s, cap 180s).
- **Indicador overlay imposible de ignorar** + **preview** mientras la cámara está activa
  (Isaac siempre ve lo que JARVIS ve).
- Frames en `data/camera/` con retención configurable; `JARVIS_CAMERA_RETENTION_HOURS=0`
  los borra justo tras enviarlos.
- Misma guía anti-datos-sensibles que `screen_look`.

---

## 8. Costos

Tarifa `vision-in` = $0.15/1M tokens; `media_resolution=LOW` ≈ 70–260 tokens/frame.
- **`camera_look` on-demand:** ~$0.00004 por captura. 1.000/mes ≈ **$0.04**.
- **`camera_watch` @1fps:** ~$0.04–0.14 por **hora** de frames. Ventana 90s ≈ **<$0.01**.
  Uso intenso (1h/día) ≈ **$1–4/mes** solo de frames (+ audio, que ya ocurre).
- **`camera_focus`:** ~$0.0001 por enfoque (1 imagen a generate_content flash).
- **Preview live:** **$0** (render local).
- **Hardware:** $0 (webcam existente). **Dep nueva:** `opencv-python` (~35MB).

Conclusión: el costo en dólares es marginal. El costo real es ingeniería + estabilidad.

---

## 9. Pruebas

Unit (monkeypatch `cv2.VideoCapture`, sin cámara real):
- `CameraCapture.capture()` produce `CameraFrame` válido (resize, JPEG, retención, warm-up).
- `camera_look` devuelve estructura `__attach_image` correcta.
- `CameraWatchController` start/stop con reloj falso: frames enviados, auto-stop por timeout,
  `close()` llamado, short-circuit por presupuesto.
- `send_video_frame` arma el `Blob` correcto (mock de session).
- `camera_focus`: parseo de `box_2d` y desnormalización al tamaño del preview.

Smoke test con webcam real: lo corre **Isaac** (Claude no accede a la cámara física).

---

## 10. Configuración (`.env.example`)

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

Pricing: añadir fila del modelo de detección (`camera_focus`) a `telemetry/costs.py`.
Hotkey on-demand propuesto: `Ctrl+Shift+C` (override permitido).

---

## 11. Roadmap por fases

- **Fase 1 — On-demand:** `vision/camera.py` (`CameraCapture`) + `camera_look` + hotkey +
  tests + `.env`. Cubre "mira esto", OCR→Obsidian y leer instrumentos puntual.
- **Fase 2 — Continuo acotado ("modo visión"):** `send_video_frame` en session +
  `CameraWatchController` + indicador overlay + `overlay/camera_preview.py` (preview live +
  retícula central) + auto-stop + tests. Cubre FPV/manual y conciencia continua.
- **Fase 2.5 — Crosshair semántico:** `camera_focus` (detección one-shot `generate_content`
  + JSON schema) + dibujo de box/cruz/etiqueta en el preview + fila de pricing + tests.
- **Fase 3 (roadmap, fuera de este spec):** pre-filtro local de presencia/movimiento
  (OpenCV) → presencia↔ProactivityEngine y modo seguridad/ausencia. Diferido por riesgo
  de complejidad (lección de modo LIBRE).
