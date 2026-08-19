$ErrorActionPreference = 'Stop'

$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$package = Join-Path $project 'package'
$work = Join-Path $project 'build'

python -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --name PSD-Font-Reporter `
  --distpath $package `
  --workpath $work `
  --specpath $project `
  --collect-all psd_tools `
  (Join-Path $project 'main.py')

if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name PSD-Font-Helper `
  --distpath (Join-Path $package 'PSD-Font-Reporter') `
  --workpath (Join-Path $work 'font-helper') `
  --specpath $project `
  (Join-Path $project 'font_helper_main.py')

if ($LASTEXITCODE -ne 0) { throw 'Font helper build failed' }

$compiler = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
if (-not (Test-Path -LiteralPath $compiler -PathType Leaf)) { throw 'Inno Setup compiler not found' }
& $compiler (Join-Path $project 'installer\PSD-Font-Reporter.iss')
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup build failed' }
