$ErrorActionPreference = "Stop"

$projectDir = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectDir ".venv\Scripts\python.exe"
$launcher = Join-Path $projectDir "launcher\ustudio_vision_launcher.py"
$desktop = [Environment]::GetFolderPath("Desktop")
$buildRoot = Join-Path $projectDir "build\desktop-launcher"

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "UStudio Vision" `
    --distpath $desktop `
    --workpath (Join-Path $buildRoot "work") `
    --specpath (Join-Path $buildRoot "spec") `
    $launcher

Write-Output (Join-Path $desktop "UStudio Vision.exe")
