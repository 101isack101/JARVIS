# Crea .venv en el repo JARVIS e instala requirements.txt.
# Uso:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_venv.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$py = $env:JARVIS_PYTHON
if (-not $py) {
    $candidates = @(
        "H:\Python311\python.exe",
        "python"
    )
    foreach ($c in $candidates) {
        if ($c -eq "python") { $py = $c; break }
        if (Test-Path $c) { $py = $c; break }
    }
}

Write-Host "Python: $py"
& $py -m venv .venv
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r requirements.txt
Write-Host "Listo. Tauri usara .venv si existe, o JARVIS_PYTHON."
Write-Host "Activar: .\.venv\Scripts\Activate.ps1"
