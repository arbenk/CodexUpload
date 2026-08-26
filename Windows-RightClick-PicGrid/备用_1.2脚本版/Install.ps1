$ErrorActionPreference = 'Stop'
$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDir = Join-Path $env:LOCALAPPDATA 'PicGrid'
$regBase = 'HKCU:\Software\Classes\SystemFileAssociations\image\shell'

try {
    if (-not (Test-Path $installDir)) { New-Item -ItemType Directory -Path $installDir -Force | Out-Null }
    foreach ($name in 'PicGrid.ps1','Launcher.vbs','Uninstall.ps1') {
        Copy-Item -LiteralPath (Join-Path $sourceDir $name) -Destination (Join-Path $installDir $name) -Force
    }

    New-Item -Path $regBase -Force | Out-Null
    # 清掉 v1.1 的“拼图”快速菜单，只保留“拼图...”。
    Remove-Item -LiteralPath (Join-Path $regBase 'PicGridQuick') -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $regBase 'PicGridCustom') -Recurse -Force -ErrorAction SilentlyContinue

    $k = Join-Path $regBase 'PicGridCustom'
    $cmdKey = Join-Path $k 'command'
    New-Item -Path $cmdKey -Force | Out-Null
    Set-Item -Path $k -Value '拼图...'
    New-ItemProperty -Path $k -Name 'MultiSelectModel' -Value 'Player' -PropertyType String -Force | Out-Null

    $wscript = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $launcher = Join-Path $installDir 'Launcher.vbs'
    $cmd = ('"{0}" "{1}" Custom "%1"' -f $wscript,$launcher)
    Set-Item -Path $cmdKey -Value $cmd

    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "PicGrid v1.2 安装完成。`r`n`r`n现在多选图片后右键只会看到：`r`n  · 拼图...`r`n`r`n右键启动已改为无控制台方式，不会再闪 PowerShell 窗口。",
        '拼图工具',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        ("安装失败：`r`n" + $_.Exception.Message),
        '拼图工具',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
    exit 1
}
