# Lanza JARVIS Desktop (Tauri) en modo dev.
# Carga el entorno MSVC (Build Tools en H:\BuildTools) + rutas de Rust en H:
# para que cargo encuentre link.exe. Uso:  .\dev.ps1   (o .\dev.ps1 -Release)
param([switch]$Release)

$ErrorActionPreference = "Stop"

$env:RUSTUP_HOME = "H:\rustup"
$env:CARGO_HOME  = "H:\cargo"
$env:Path = "H:\cargo\bin;$env:Path"

# Importar variables del entorno MSVC (vcvars64.bat) a esta sesion
$vcvars = "H:\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
if (-not (Test-Path $vcvars)) { throw "No encuentro vcvars64.bat en $vcvars (reinstalar MSVC Build Tools)" }
cmd /c "`"$vcvars`" >nul 2>&1 && set" | ForEach-Object {
    if ($_ -match "^([^=]+)=(.*)$") { Set-Item "env:$($matches[1])" $matches[2] }
}

Set-Location $PSScriptRoot
if ($Release) {
    Write-Host "[dev.ps1] tauri build (release)..." -ForegroundColor Cyan
    npm run build
} else {
    Write-Host "[dev.ps1] tauri dev..." -ForegroundColor Cyan
    npm run dev
}
