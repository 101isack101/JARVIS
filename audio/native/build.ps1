param(
    [string]$OutDir = "$PSScriptRoot\bin",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$Source = Join-Path $PSScriptRoot "jarvis_audio_native.cpp"
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$Dll = Join-Path $OutDir "jarvis_audio_native.dll"
$Obj = Join-Path $OutDir "jarvis_audio_native.obj"

if ($Clean -and (Test-Path $OutDir)) {
    Remove-Item -LiteralPath $OutDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Find-CommandPath([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$cl = Find-CommandPath "cl.exe"
if (-not $cl) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($install) {
            $vcvars = Join-Path $install "VC\Auxiliary\Build\vcvars64.bat"
            if (Test-Path $vcvars) {
                cmd /c "`"$vcvars`" && cl /std:c++17 /O2 /LD /EHsc /nologo /Fo:`"$Obj`" `"$Source`" /Fe:`"$Dll`""
                Write-Host "Built $Dll"
                exit 0
            }
        }
    }
}

if ($cl) {
    & $cl /std:c++17 /O2 /LD /EHsc /nologo /Fo:$Obj $Source /Fe:$Dll
    Write-Host "Built $Dll"
    exit 0
}

$clang = Find-CommandPath "clang++.exe"
if ($clang) {
    & $clang -std=c++17 -O3 -shared -o $Dll $Source
    Write-Host "Built $Dll"
    exit 0
}

$gpp = Find-CommandPath "g++.exe"
if ($gpp) {
    & $gpp -std=c++17 -O3 -shared -o $Dll $Source
    Write-Host "Built $Dll"
    exit 0
}

throw "No C++ compiler found. Install Visual Studio Build Tools with Desktop development with C++, or put clang++/g++ on PATH."
