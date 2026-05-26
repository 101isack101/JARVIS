# assets/setup_shortcut.ps1
# Crea o actualiza el shortcut "JARVIS.lnk" en el escritorio de Isaac apuntando
# a jarvis_run.bat con el icono JARVIS. Idempotente: correr cuantas
# veces haga falta, no rompe nada existente.
#
# Uso:
#   PowerShell -ExecutionPolicy Bypass -File assets\setup_shortcut.ps1
#
# Output esperado: ruta del .lnk + valores aplicados. Si el icono no
# refresca en el escritorio, presiona F5 ahi mismo.

$ErrorActionPreference = 'Stop'

# Rutas absolutas (Jarvis siempre vive aqui en la maquina de Isaac).
$JarvisDir = "C:\Users\Isaac\Desktop\PROYECTOS\Jarvis"
$BatFile  = Join-Path $JarvisDir "jarvis_run.bat"
$IcoFile  = Join-Path $JarvisDir "assets\icon.ico"
$LnkPath  = Join-Path ([Environment]::GetFolderPath('Desktop')) "JARVIS.lnk"

# Verifica que los archivos fuente existan antes de tocar el shortcut.
foreach ($req in @($BatFile, $IcoFile)) {
    if (-not (Test-Path $req)) {
        Write-Error "Falta archivo requerido: $req"
        exit 1
    }
}

# Crea o abre el shortcut (CreateShortcut es upsert: si existe lo abre,
# si no existe lo prepara para guardar).
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($LnkPath)
$sc.TargetPath       = $BatFile
$sc.Arguments        = ""
$sc.WorkingDirectory = $JarvisDir
$sc.IconLocation     = "$IcoFile,0"
$sc.Description      = "JARVIS - Asistente conversacional"
# WindowStyle 7 = minimizado (la consola del .bat no molesta).
# Si prefieres ver la consola para debug, dejar 1 (normal) o quitar la linea.
$sc.WindowStyle      = 7
$sc.Save()

Write-Output "Shortcut actualizado: $LnkPath"
Write-Output "  Target:  $($sc.TargetPath)"
Write-Output "  Icon:    $($sc.IconLocation)"
Write-Output "  WorkDir: $($sc.WorkingDirectory)"
Write-Output "  Tooltip: $($sc.Description)"

# Refrescar icon cache de Windows. Sin esto Explorer puede seguir mostrando
# el icono viejo cacheado hasta el proximo logoff.
Write-Output ""
Write-Output "Refrescando icon cache..."
try {
    & "$env:windir\system32\ie4uinit.exe" -ClearIconCache 2>$null
    Write-Output "Cache limpiado. Si no se ve, presiona F5 en el escritorio."
} catch {
    Write-Output "No se pudo limpiar cache (no critico)."
}
