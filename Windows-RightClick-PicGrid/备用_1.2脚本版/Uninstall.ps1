$ErrorActionPreference = 'SilentlyContinue'
$installDir = Join-Path $env:LOCALAPPDATA 'PicGrid'
$regBase = 'HKCU:\Software\Classes\SystemFileAssociations\image\shell'
Remove-Item -LiteralPath (Join-Path $regBase 'PicGridQuick') -Recurse -Force
Remove-Item -LiteralPath (Join-Path $regBase 'PicGridCustom') -Recurse -Force

Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.MessageBox]::Show(
    "右键菜单已卸载。`r`n`r`n程序文件仍位于：`r`n$installDir`r`n`r`n如需彻底删除，可直接删除这个文件夹。",
    '拼图工具',
    [System.Windows.Forms.MessageBoxButtons]::OK,
    [System.Windows.Forms.MessageBoxIcon]::Information
) | Out-Null
