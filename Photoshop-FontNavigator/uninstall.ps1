$ErrorActionPreference = 'Stop'
$extensionsRoot = Join-Path $env:APPDATA 'Adobe\CEP\extensions'
$destination = Join-Path $env:APPDATA 'Adobe\CEP\extensions\com.local.fontnavigator.cep'
if (-not (Test-Path -LiteralPath $destination -PathType Container)) {
    Write-Host "Extension is not installed: $destination"
    exit 0
}
$resolvedRoot = (Resolve-Path -LiteralPath $extensionsRoot).Path.TrimEnd('\')
$resolvedDestination = (Resolve-Path -LiteralPath $destination).Path
if ((Split-Path -Parent $resolvedDestination) -ne $resolvedRoot -or (Split-Path -Leaf $resolvedDestination) -ne 'com.local.fontnavigator.cep') {
    throw "Uninstall path validation failed: $resolvedDestination"
}
$item = Get-Item -LiteralPath $resolvedDestination -Force
if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Destination is a link or junction; refusing recursive removal.' }
Remove-Item -LiteralPath $destination -Recurse
Write-Host 'Font Navigator CEP extension was removed. Restart Photoshop.'
