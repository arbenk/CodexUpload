$ErrorActionPreference = 'Stop'
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDir = Split-Path -Parent $projectDir
$distDir = Join-Path $repoDir 'dist'

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name 'Codex-UsageMonitor' `
    --distpath $distDir `
    --workpath (Join-Path $projectDir 'build') `
    --specpath $projectDir `
    (Join-Path $projectDir 'app.py')

Write-Host "Built: $(Join-Path $distDir 'Codex-UsageMonitor.exe')"
