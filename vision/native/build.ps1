param(
    [string]$OutDir = "$PSScriptRoot\bin",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$Source = Join-Path $PSScriptRoot "jarvis_vision_sidecar.cpp"
$OutDir = [System.IO.Path]::GetFullPath($OutDir)
$Exe = Join-Path $OutDir "jarvis_vision_sidecar.exe"
$Obj = Join-Path $OutDir "jarvis_vision_sidecar.obj"

if ($Clean -and (Test-Path $OutDir)) {
    Remove-Item -LiteralPath $OutDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

function Find-CommandPath([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

$libs = "mfplat.lib mfreadwrite.lib mfuuid.lib mf.lib windowscodecs.lib ole32.lib oleaut32.lib propsys.lib"
$cl = Find-CommandPath "cl.exe"
if (-not $cl) {
    $programFilesX86 = [Environment]::GetFolderPath("ProgramFilesX86")
    if (-not $programFilesX86) { $programFilesX86 = ${env:ProgramFiles(x86)} }
    $vswhere = Join-Path $programFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $install = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($install) {
            $vcvars = Join-Path $install "VC\Auxiliary\Build\vcvars64.bat"
            if (Test-Path $vcvars) {
                cmd /c "`"$vcvars`" && cl /std:c++17 /O2 /EHsc /nologo /Fo:`"$Obj`" `"$Source`" /Fe:`"$Exe`" /link $libs"
                if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
                Write-Host "Built $Exe"
                exit 0
            }
        }
    }
}

if ($cl) {
    & $cl /std:c++17 /O2 /EHsc /nologo /Fo:$Obj $Source /Fe:$Exe /link mfplat.lib mfreadwrite.lib mfuuid.lib mf.lib windowscodecs.lib ole32.lib oleaut32.lib propsys.lib
    Write-Host "Built $Exe"
    exit 0
}

throw "No MSVC C++ compiler found. Install Visual Studio Build Tools with Desktop development with C++."
