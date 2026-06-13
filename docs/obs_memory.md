# OBS Memory

OBS Memory convierte grabaciones OBS en notas episodicas para Jarvis/Obsidian.
El flujo recomendado es: Jarvis inicia OBS por WebSocket, OBS graba en MKV,
Jarvis detiene la grabacion, extrae audio/keyframes, transcribe con
`faster-whisper`, sintetiza con Claude si esta disponible y escribe una nota en
`Jarvis Memory/obs_sessions/`. Por defecto conserva el video original.

## Configuracion minima

En OBS 28+ activa `Tools -> WebSocket Server Settings`.

En `.env`:

```env
JARVIS_OBS_MEMORY_ENABLED=true
JARVIS_OBS_RECORDING_DIR=C:\Users\Isaac\Videos
JARVIS_OBS_WEBSOCKET_HOST=127.0.0.1
JARVIS_OBS_WEBSOCKET_PORT=4455
JARVIS_OBS_WEBSOCKET_PASSWORD=
JARVIS_OBS_AUTO_START=true
JARVIS_OBS_EXE=C:\Program Files\obs-studio\bin\64bit\obs64.exe
JARVIS_OBS_RETENTION=keep_video
JARVIS_OBS_ALLOW_EXTERNAL_VIDEO_PATHS=false
JARVIS_OBS_PROCESS_BACKGROUND=true
JARVIS_OBS_ANALYSIS_MODE=course
JARVIS_OBS_COURSE_CHUNK_SEC=300
JARVIS_OBS_COURSE_KEYFRAMES_PER_CHUNK=6
JARVIS_FFMPEG_DIR=
```

Instala dependencias:

```powershell
& "H:\Python311\python.exe" -m pip install -r requirements.txt
winget install Gyan.FFmpeg
```

## Comandos naturales

- "Empieza a grabar con OBS para debug de Jarvis."
- "Termina la grabacion y guarda la sesion."
- "Procesa la ultima grabacion de OBS."
- "Estado de OBS Memory."

## Retencion

`keep_video` conserva el archivo original. Si quieres borrar automaticamente
despues de escribir la nota, usa `delete_video_after_success`; esa opcion solo
debe activarse si aceptas perder el video original tras un procesamiento exitoso.
Se conservan artefactos livianos en `data/obs_memory/`, que esta fuera de Git.

`process_file` solo acepta videos dentro de `JARVIS_OBS_RECORDING_DIR`. Para
procesar una ruta externa, activa `JARVIS_OBS_ALLOW_EXTERNAL_VIDEO_PATHS=true`.

## Procesamiento en background

Deja `JARVIS_OBS_PROCESS_BACKGROUND=true`. Una grabacion de varios minutos puede
tardar en transcribirse en CPU; si se procesa dentro del turno de Gemini Live,
Jarvis queda mudo o puede reconectar. Con background, `stop` devuelve control
de inmediato y la nota aparece en Obsidian cuando el job termina.

Cuando el job termina, Jarvis avisa en el overlay:

- `OBS Memory listo: <titulo> -> <nota>` si el analisis se guardo correctamente.
- `OBS Memory fallo: <titulo> - <error>` si hubo un problema procesando el video.

Tambien puedes pedir: "estado de OBS Memory" para ver los ultimos jobs.

## Modo curso

`JARVIS_OBS_ANALYSIS_MODE=course` divide la grabacion en fragmentos, transcribe
lo que dice el video y analiza capturas visuales del curso con Claude. Este modo
esta pensado para cursos, tutoriales y videos tecnicos: extrae conceptos,
comandos, snippets, diagramas, preguntas abiertas y un analisis accionable para
Isaac.

Si solo quieres una memoria rapida de una sesion de trabajo, puedes cambiarlo a
`episodic`, pero para cursos deja `course`.
