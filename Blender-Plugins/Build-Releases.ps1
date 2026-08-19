$ErrorActionPreference = 'Stop'

$pluginRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$workspaceRoot = Split-Path -Parent $pluginRoot
$dist = Join-Path $workspaceRoot 'dist'

$packages = [ordered]@{
    'Blender-STL-Six-Views-Renderer' = 'Blender-STL-Six-Views-Renderer.zip'
    'Blender-Drop-Ground-Align' = 'Blender-Drop-Ground-Align.zip'
    'Blender-FlatFab-Plate-Flatten' = 'Blender-FlatFab-Plate-Flatten.zip'
}

New-Item -ItemType Directory -Path $dist -Force | Out-Null

foreach ($folderName in $packages.Keys) {
    $source = Join-Path $pluginRoot $folderName
    $target = Join-Path $dist $packages[$folderName]

    if (-not (Test-Path -LiteralPath $source -PathType Container)) {
        throw "Plugin source directory not found: $source"
    }

    $packageItems = Get-ChildItem -LiteralPath $source -Force | Where-Object {
        $_.Name -notin @('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.venv', 'venv') -and
        $_.Extension -notin @('.pyc', '.pyo', '.log')
    }

    if (-not $packageItems) {
        throw "Plugin source directory is empty after filtering: $source"
    }

    Compress-Archive -LiteralPath $packageItems.FullName -DestinationPath $target -CompressionLevel Optimal -Force
    Write-Host "Built $target"
}
