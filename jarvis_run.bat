@echo off
REM Jarvis launcher.
REM El logging ahora lo maneja telemetry/logger.py (loguru con rotacion
REM 10 MB y retencion 7 dias). NO redirigir stdout aqui: Loguru abre el
REM archivo con handle exclusivo y un redirect concurrente causa
REM PermissionError. Si necesitas ver salida en vivo, usa:
REM     Get-Content data\jarvis.log -Wait -Tail 30

setlocal
set JARVIS_DIR=%~dp0
cd /d "%JARVIS_DIR%"

REM Asegurar carpeta data (loguru tambien la crea, pero esto evita
REM una posible race condition al primer arranque).
if not exist "data" mkdir "data"

REM Ejecutar Jarvis. -u = unbuffered, util si en el futuro quieres
REM volver a redirigir stdout puntualmente con `> file.txt`.
"H:\Python311\python.exe" -u jarvis.py

endlocal
