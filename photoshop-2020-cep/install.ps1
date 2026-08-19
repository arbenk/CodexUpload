$ErrorActionPreference = 'Stop'

$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$extensionId = 'com.local.fontnavigator.cep'
$extensionsRoot = Join-Path $env:APPDATA 'Adobe\CEP\extensions'
$destination = Join-Path $extensionsRoot $extensionId

New-Item -ItemType Directory -Path $extensionsRoot -Force | Out-Null
New-Item -ItemType Directory -Path $destination -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $destination 'CSXS') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $destination 'js') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $destination 'jsx') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'CSXS\manifest.xml') -Destination (Join-Path $destination 'CSXS\manifest.xml') -Force
Copy-Item -LiteralPath (Join-Path $source 'js\panel.js') -Destination (Join-Path $destination 'js\panel.js') -Force
Copy-Item -LiteralPath (Join-Path $source 'jsx\host.jsx') -Destination (Join-Path $destination 'jsx\host.jsx') -Force
Copy-Item -LiteralPath (Join-Path $source 'index.html') -Destination $destination
Copy-Item -LiteralPath (Join-Path $source 'styles.css') -Destination $destination
Copy-Item -LiteralPath (Join-Path $source 'js\font-localizer.js') -Destination (Join-Path $destination 'js')

New-Item -Path 'HKCU:\Software\Adobe\CSXS.9' -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Software\Adobe\CSXS.9' -Name PlayerDebugMode -Value '1' -PropertyType String -Force | Out-Null
New-Item -Path 'HKCU:\Software\Adobe\CSXS.10' -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Software\Adobe\CSXS.10' -Name PlayerDebugMode -Value '1' -PropertyType String -Force | Out-Null
New-Item -Path 'HKCU:\Software\Adobe\CSXS.11' -Force | Out-Null
New-ItemProperty -Path 'HKCU:\Software\Adobe\CSXS.11' -Name PlayerDebugMode -Value '1' -PropertyType String -Force | Out-Null

Write-Host "Installation completed: $destination"
Write-Host 'Restart Photoshop 2020-2022, then open Window > Extensions (Legacy) > Font Navigator.'
