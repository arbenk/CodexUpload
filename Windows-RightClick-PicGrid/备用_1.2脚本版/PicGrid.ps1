param(
    [ValidateSet('Custom','DirectCustom')]
    [string]$Mode = 'Custom',
    [string]$InputPath,
    [string[]]$Paths,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$RemainingPaths
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()

# 前台窗口辅助：兼容模式仍由隐藏 PowerShell 运行，但设置窗口会主动抢到前台。
try {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PicGridForeground {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
'@ -ErrorAction SilentlyContinue
} catch { }


$script:AppName = 'PicGrid'
$script:Version = '1.3.1-compat'
$script:AppDir = Join-Path $env:LOCALAPPDATA 'PicGrid'
$script:SettingsFile = Join-Path $script:AppDir 'settings.json'
$script:LogFile = Join-Path $script:AppDir 'picgrid.log'
$script:SupportedExtensions = @('.jpg','.jpeg','.jfif','.png','.bmp','.gif','.tif','.tiff')

function Ensure-AppDir {
    if (-not (Test-Path $script:AppDir)) {
        New-Item -ItemType Directory -Path $script:AppDir -Force | Out-Null
    }
}

function Write-Log([string]$Text) {
    try {
        Ensure-AppDir
        $line = ('{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text)
        Add-Content -LiteralPath $script:LogFile -Value $line -Encoding UTF8
    } catch { }
}

function Show-Error([string]$Message) {
    Write-Log ('ERROR: ' + $Message)
    [System.Windows.Forms.MessageBox]::Show(
        $Message, '拼图工具',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    ) | Out-Null
}

function Show-Info([string]$Message) {
    [System.Windows.Forms.MessageBox]::Show(
        $Message, '拼图工具',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
}

function Get-NaturalSortKey([string]$Name) {
    return [regex]::Replace($Name.ToLowerInvariant(), '\d+', {
        param($m)
        $m.Value.PadLeft(20, '0')
    })
}

function Normalize-Paths([string[]]$InputPaths) {
    $result = @()
    $seen = @{}
    foreach ($p in $InputPaths) {
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        try { $full = [System.IO.Path]::GetFullPath($p.Trim('"')) }
        catch { continue }
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { continue }
        $ext = [System.IO.Path]::GetExtension($full).ToLowerInvariant()
        if ($script:SupportedExtensions -notcontains $ext) { continue }
        $key = $full.ToLowerInvariant()
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $result += $full
        }
    }
    return @($result | Sort-Object @{Expression={ Get-NaturalSortKey ([System.IO.Path]::GetFileName($_)) }})
}

function Get-Orientation([System.Drawing.Image]$Image) {
    try {
        if ($Image.PropertyIdList -contains 0x0112) {
            $prop = $Image.GetPropertyItem(0x0112)
            return [int]$prop.Value[0]
        }
    } catch { }
    return 1
}

function Apply-Orientation([System.Drawing.Image]$Image) {
    $o = Get-Orientation $Image
    switch ($o) {
        2 { $Image.RotateFlip([System.Drawing.RotateFlipType]::RotateNoneFlipX) }
        3 { $Image.RotateFlip([System.Drawing.RotateFlipType]::Rotate180FlipNone) }
        4 { $Image.RotateFlip([System.Drawing.RotateFlipType]::Rotate180FlipX) }
        5 { $Image.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipX) }
        6 { $Image.RotateFlip([System.Drawing.RotateFlipType]::Rotate90FlipNone) }
        7 { $Image.RotateFlip([System.Drawing.RotateFlipType]::Rotate270FlipX) }
        8 { $Image.RotateFlip([System.Drawing.RotateFlipType]::Rotate270FlipNone) }
    }
}

function Get-ImageInfo([string]$Path) {
    $img = $null
    try {
        $img = [System.Drawing.Image]::FromFile($Path)
        $o = Get-Orientation $img
        $w = [double]$img.Width
        $h = [double]$img.Height
        if ($o -in 5,6,7,8) { $tmp=$w; $w=$h; $h=$tmp }
        if ($w -le 0 -or $h -le 0) { throw '图片尺寸无效' }
        return [pscustomobject]@{ Path=$Path; Width=$w; Height=$h; Ratio=($w/$h) }
    } finally {
        if ($null -ne $img) { $img.Dispose() }
    }
}

function Get-ImageInfos([string[]]$ImagePaths) {
    $infos = @()
    foreach ($p in $ImagePaths) {
        try { $infos += (Get-ImageInfo $p) }
        catch { Write-Log ("Skip unreadable image: $p ; " + $_.Exception.Message) }
    }
    return $infos
}

function New-Canvas([int]$Width, [int]$Height) {
    if ($Width -lt 1 -or $Height -lt 1) { throw '输出尺寸无效。' }
    $pixels = [long]$Width * [long]$Height
    if ($Width -gt 30000 -or $Height -gt 30000 -or $pixels -gt 120000000) {
        throw ('输出图片过大：{0} × {1}（约 {2:N0} 万像素）。请降低分辨率倍率或减少每排图片数量。' -f $Width,$Height,($pixels/10000.0))
    }
    return [System.Drawing.Bitmap]::new($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
}

function Configure-Graphics([System.Drawing.Graphics]$G) {
    $G.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceOver
    $G.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $G.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $G.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
    $G.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
}

function Draw-ImageInRect([System.Drawing.Graphics]$G, [string]$Path, [System.Drawing.RectangleF]$Rect) {
    $img = $null
    try {
        $img = [System.Drawing.Image]::FromFile($Path)
        Apply-Orientation $img
        $G.DrawImage($img, $Rect)
    } finally {
        if ($null -ne $img) { $img.Dispose() }
    }
}

function Get-OutputPath([string]$FirstImage, [string]$Format) {
    $dir = [System.IO.Path]::GetDirectoryName($FirstImage)
    $ext = if ($Format -eq 'PNG') { '.png' } else { '.jpg' }
    $baseName = '拼图_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
    $candidate = Join-Path $dir ($baseName + $ext)
    $i = 2
    while (Test-Path -LiteralPath $candidate) {
        $candidate = Join-Path $dir (('{0}_{1}{2}' -f $baseName,$i,$ext))
        $i++
    }
    return $candidate
}

function Save-Bitmap([System.Drawing.Bitmap]$Bitmap, [string]$Path, [string]$Format) {
    if ($Format -eq 'PNG') {
        $Bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        return
    }
    $codec = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object { $_.MimeType -eq 'image/jpeg' } | Select-Object -First 1
    $encParams = [System.Drawing.Imaging.EncoderParameters]::new(1)
    $encParams.Param[0] = [System.Drawing.Imaging.EncoderParameter]::new([System.Drawing.Imaging.Encoder]::Quality, [long]92)
    try { $Bitmap.Save($Path, $codec, $encParams) }
    finally { $encParams.Dispose() }
}

function Default-Settings {
    return [pscustomobject]@{
        Columns = 4
        AdaptMode = 'Width'
        Spacing = 6
        Margin = 6
        Format = 'JPG'
        OpenAfter = $true
    }
}

function Load-Settings {
    Ensure-AppDir
    $s = Default-Settings
    if (Test-Path -LiteralPath $script:SettingsFile) {
        try {
            $j = Get-Content -LiteralPath $script:SettingsFile -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($name in 'Columns','AdaptMode','Spacing','Margin','Format','OpenAfter') {
                if ($null -ne $j.$name) { $s.$name = $j.$name }
            }
            # v1.1 兼容：旧的 AdaptiveWidth=true 对应新的“宽度自适应”。
            if ($null -eq $j.AdaptMode -and $null -ne $j.AdaptiveWidth) {
                if ([bool]$j.AdaptiveWidth) { $s.AdaptMode = 'Width' }
            }
        } catch { }
    }
    return $s
}

function Save-Settings($Settings) {
    Ensure-AppDir
    $Settings | ConvertTo-Json | Set-Content -LiteralPath $script:SettingsFile -Encoding UTF8
}

function Get-WidthAdaptiveLayout([object[]]$Infos, [int]$Columns, [int]$Gap, [int]$Margin, [double]$Scale) {
    # 宽度自适应：同一行同高，宽度按图片比例变化。
    # 倍率决定该行的基础高度：取该行原始高度平均值 × 倍率。
    $rows = @()
    for ($start=0; $start -lt $Infos.Count; $start += $Columns) {
        $count = [Math]::Min($Columns, $Infos.Count-$start)
        $sumH = 0.0
        for ($j=0; $j -lt $count; $j++) { $sumH += [double]$Infos[$start+$j].Height }
        $rowH = [Math]::Max(1.0, [Math]::Round(($sumH/[double]$count) * $Scale))
        $widths = @()
        $rowW = 0.0
        for ($j=0; $j -lt $count; $j++) {
            $rw = [Math]::Max(1.0, [Math]::Round([double]$Infos[$start+$j].Ratio * $rowH))
            $widths += $rw
            $rowW += $rw
        }
        if ($count -gt 1) { $rowW += ($count-1)*$Gap }
        $rows += [pscustomobject]@{ Start=$start; Count=$count; Height=$rowH; Width=$rowW; Widths=$widths }
    }
    $contentW = 1.0
    $contentH = 0.0
    foreach ($row in $rows) {
        if ($row.Width -gt $contentW) { $contentW = $row.Width }
        $contentH += $row.Height
    }
    if ($rows.Count -gt 1) { $contentH += ($rows.Count-1)*$Gap }
    return [pscustomobject]@{
        Mode='Width'; Rows=$rows
        Width=[int][Math]::Ceiling($contentW + 2*$Margin)
        Height=[int][Math]::Ceiling($contentH + 2*$Margin)
    }
}

function Get-HeightAdaptiveLayout([object[]]$Infos, [int]$Columns, [int]$Gap, [int]$Margin, [double]$Scale) {
    # 高度自适应：同一列同宽，高度按图片比例变化。
    # 倍率决定每一列的基础宽度：取该列原始宽度平均值 × 倍率。
    $rowsCount = [int][Math]::Ceiling($Infos.Count / [double]$Columns)
    $colWidths = @()
    for ($c=0; $c -lt $Columns; $c++) {
        $sumW = 0.0; $count = 0
        for ($r=0; $r -lt $rowsCount; $r++) {
            $idx = $r*$Columns + $c
            if ($idx -lt $Infos.Count) { $sumW += [double]$Infos[$idx].Width; $count++ }
        }
        if ($count -gt 0) { $cw = [Math]::Max(1.0,[Math]::Round(($sumW/[double]$count)*$Scale)) }
        else { $cw = 1.0 }
        $colWidths += $cw
    }

    $rowHeights = @()
    $itemHeights = @{}
    for ($r=0; $r -lt $rowsCount; $r++) {
        $rh = 1.0
        for ($c=0; $c -lt $Columns; $c++) {
            $idx = $r*$Columns + $c
            if ($idx -ge $Infos.Count) { continue }
            $ih = [Math]::Max(1.0, [Math]::Round([double]$colWidths[$c] / [double]$Infos[$idx].Ratio))
            $itemHeights[$idx] = $ih
            if ($ih -gt $rh) { $rh = $ih }
        }
        $rowHeights += $rh
    }

    $contentW = ($colWidths | Measure-Object -Sum).Sum
    if ($Columns -gt 1) { $contentW += ($Columns-1)*$Gap }
    $contentH = ($rowHeights | Measure-Object -Sum).Sum
    if ($rowsCount -gt 1) { $contentH += ($rowsCount-1)*$Gap }
    return [pscustomobject]@{
        Mode='Height'; Columns=$Columns; RowsCount=$rowsCount
        ColWidths=$colWidths; RowHeights=$rowHeights; ItemHeights=$itemHeights
        Width=[int][Math]::Ceiling($contentW + 2*$Margin)
        Height=[int][Math]::Ceiling($contentH + 2*$Margin)
    }
}

function Get-Layout([object[]]$Infos, $Settings) {
    $cols = [Math]::Min([Math]::Max(1,[int]$Settings.Columns), $Infos.Count)
    $gap = [Math]::Max(0,[int]$Settings.Spacing)
    $margin = [Math]::Max(0,[int]$Settings.Margin)
    $scale = [Math]::Max(0.05,[double]$Settings.Scale)
    if ($Settings.AdaptMode -eq 'Height') {
        return Get-HeightAdaptiveLayout $Infos $cols $gap $margin $scale
    }
    return Get-WidthAdaptiveLayout $Infos $cols $gap $margin $scale
}

function Compose-FromLayout([object[]]$Infos, $Settings, $Layout) {
    $bmp = New-Canvas $Layout.Width $Layout.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        Configure-Graphics $g
        $g.Clear([System.Drawing.Color]::White)
        $gap = [int]$Settings.Spacing
        $margin = [int]$Settings.Margin

        if ($Layout.Mode -eq 'Width') {
            $y = [double]$margin
            foreach ($row in $Layout.Rows) {
                $x = [double]$margin + (($Layout.Width - 2*$margin - $row.Width) / 2.0)
                for ($j=0; $j -lt $row.Count; $j++) {
                    $idx = $row.Start + $j
                    $rw = [double]$row.Widths[$j]
                    $rect = [System.Drawing.RectangleF]::new([single]$x,[single]$y,[single]$rw,[single]$row.Height)
                    Draw-ImageInRect $g $Infos[$idx].Path $rect
                    $x += $rw + $gap
                }
                $y += $row.Height + $gap
            }
        } else {
            $y = [double]$margin
            for ($r=0; $r -lt $Layout.RowsCount; $r++) {
                $x = [double]$margin
                $rh = [double]$Layout.RowHeights[$r]
                for ($c=0; $c -lt $Layout.Columns; $c++) {
                    $idx = $r*$Layout.Columns + $c
                    $cw = [double]$Layout.ColWidths[$c]
                    if ($idx -lt $Infos.Count) {
                        $ih = [double]$Layout.ItemHeights[$idx]
                        $iy = $y + (($rh-$ih)/2.0)
                        $rect = [System.Drawing.RectangleF]::new([single]$x,[single]$iy,[single]$cw,[single]$ih)
                        Draw-ImageInRect $g $Infos[$idx].Path $rect
                    }
                    $x += $cw + $gap
                }
                $y += $rh + $gap
            }
        }

        $out = Get-OutputPath $Infos[0].Path $Settings.Format
        Save-Bitmap $bmp $out $Settings.Format
        return $out
    } finally {
        $g.Dispose(); $bmp.Dispose()
    }
}

function Show-SettingsDialog([object[]]$Infos) {
    $s = Load-Settings
    $form = New-Object System.Windows.Forms.Form
    $form.Text = ('拼图设置  ·  已选 {0} 张' -f $Infos.Count)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ClientSize = [System.Drawing.Size]::new(460, 420)
    $form.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', [single]9)

    $lblCols = New-Object System.Windows.Forms.Label
    $lblCols.Text = '每排图片：'
    $lblCols.Location = [System.Drawing.Point]::new(24, 24)
    $lblCols.AutoSize = $true
    $form.Controls.Add($lblCols)

    $numCols = New-Object System.Windows.Forms.NumericUpDown
    $numCols.Location = [System.Drawing.Point]::new(120, 20)
    $numCols.Size = [System.Drawing.Size]::new(90, 26)
    $numCols.Minimum = 1; $numCols.Maximum = 30
    $numCols.Value = [Math]::Min(30, [Math]::Max(1, [int]$s.Columns))
    $form.Controls.Add($numCols)

    $grpAdapt = New-Object System.Windows.Forms.GroupBox
    $grpAdapt.Text = '适应方式'
    $grpAdapt.Location = [System.Drawing.Point]::new(24, 60)
    $grpAdapt.Size = [System.Drawing.Size]::new(412, 82)
    $form.Controls.Add($grpAdapt)

    $radWidth = New-Object System.Windows.Forms.RadioButton
    $radWidth.Text = '宽度自适应（同一行同高，宽度按比例）'
    $radWidth.Location = [System.Drawing.Point]::new(16, 24)
    $radWidth.AutoSize = $true
    $radWidth.Checked = ($s.AdaptMode -ne 'Height')
    $grpAdapt.Controls.Add($radWidth)

    $radHeight = New-Object System.Windows.Forms.RadioButton
    $radHeight.Text = '高度自适应（同一列同宽，高度按比例）'
    $radHeight.Location = [System.Drawing.Point]::new(16, 50)
    $radHeight.AutoSize = $true
    $radHeight.Checked = ($s.AdaptMode -eq 'Height')
    $grpAdapt.Controls.Add($radHeight)

    $lblScale = New-Object System.Windows.Forms.Label
    $lblScale.Text = '分辨率倍率：'
    $lblScale.Location = [System.Drawing.Point]::new(24, 161)
    $lblScale.AutoSize = $true
    $form.Controls.Add($lblScale)

    $numScale = New-Object System.Windows.Forms.NumericUpDown
    $numScale.Location = [System.Drawing.Point]::new(120, 157)
    $numScale.Size = [System.Drawing.Size]::new(90, 26)
    $numScale.DecimalPlaces = 2
    $numScale.Increment = [decimal]0.05
    $numScale.Minimum = [decimal]0.05
    $numScale.Maximum = [decimal]4.00
    $numScale.Value = [decimal]0.50
    $form.Controls.Add($numScale)

    $btnHalf = New-Object System.Windows.Forms.Button
    $btnHalf.Text = '0.5×'
    $btnHalf.Location = [System.Drawing.Point]::new(224, 156)
    $btnHalf.Size = [System.Drawing.Size]::new(64, 28)
    $form.Controls.Add($btnHalf)

    $btnFull = New-Object System.Windows.Forms.Button
    $btnFull.Text = '1.0×'
    $btnFull.Location = [System.Drawing.Point]::new(296, 156)
    $btnFull.Size = [System.Drawing.Size]::new(64, 28)
    $form.Controls.Add($btnFull)

    $lblExample = New-Object System.Windows.Forms.Label
    $lblExample.Location = [System.Drawing.Point]::new(120, 188)
    $lblExample.Size = [System.Drawing.Size]::new(316, 22)
    $lblExample.ForeColor = [System.Drawing.Color]::DimGray
    $form.Controls.Add($lblExample)

    $lblSpace = New-Object System.Windows.Forms.Label
    $lblSpace.Text = '图片间距：'
    $lblSpace.Location = [System.Drawing.Point]::new(24, 226)
    $lblSpace.AutoSize = $true
    $form.Controls.Add($lblSpace)

    $numSpace = New-Object System.Windows.Forms.NumericUpDown
    $numSpace.Location = [System.Drawing.Point]::new(120, 222)
    $numSpace.Size = [System.Drawing.Size]::new(90, 26)
    $numSpace.Minimum = 0; $numSpace.Maximum = 200
    $numSpace.Value = [Math]::Min(200, [Math]::Max(0, [int]$s.Spacing))
    $form.Controls.Add($numSpace)

    $lblMargin = New-Object System.Windows.Forms.Label
    $lblMargin.Text = '外边距：'
    $lblMargin.Location = [System.Drawing.Point]::new(240, 226)
    $lblMargin.AutoSize = $true
    $form.Controls.Add($lblMargin)

    $numMargin = New-Object System.Windows.Forms.NumericUpDown
    $numMargin.Location = [System.Drawing.Point]::new(320, 222)
    $numMargin.Size = [System.Drawing.Size]::new(90, 26)
    $numMargin.Minimum = 0; $numMargin.Maximum = 300
    $numMargin.Value = [Math]::Min(300, [Math]::Max(0, [int]$s.Margin))
    $form.Controls.Add($numMargin)

    $lblFormat = New-Object System.Windows.Forms.Label
    $lblFormat.Text = '输出格式：'
    $lblFormat.Location = [System.Drawing.Point]::new(24, 269)
    $lblFormat.AutoSize = $true
    $form.Controls.Add($lblFormat)

    $cmbFormat = New-Object System.Windows.Forms.ComboBox
    $cmbFormat.DropDownStyle = 'DropDownList'
    $cmbFormat.Location = [System.Drawing.Point]::new(120, 265)
    $cmbFormat.Size = [System.Drawing.Size]::new(90, 26)
    [void]$cmbFormat.Items.Add('JPG'); [void]$cmbFormat.Items.Add('PNG')
    $cmbFormat.SelectedItem = if ($s.Format -eq 'PNG') { 'PNG' } else { 'JPG' }
    $form.Controls.Add($cmbFormat)

    $chkOpen = New-Object System.Windows.Forms.CheckBox
    $chkOpen.Text = '完成后在文件夹中选中结果'
    $chkOpen.Location = [System.Drawing.Point]::new(240, 267)
    $chkOpen.AutoSize = $true
    $chkOpen.Checked = [bool]$s.OpenAfter
    $form.Controls.Add($chkOpen)

    $panelInfo = New-Object System.Windows.Forms.Panel
    $panelInfo.BorderStyle = 'FixedSingle'
    $panelInfo.Location = [System.Drawing.Point]::new(24, 308)
    $panelInfo.Size = [System.Drawing.Size]::new(412, 48)
    $form.Controls.Add($panelInfo)

    $lblOutput = New-Object System.Windows.Forms.Label
    $lblOutput.Text = '预计输出：计算中...'
    $lblOutput.Location = [System.Drawing.Point]::new(12, 7)
    $lblOutput.Size = [System.Drawing.Size]::new(386, 22)
    $lblOutput.Font = [System.Drawing.Font]::new('Microsoft YaHei UI', [single]9, [System.Drawing.FontStyle]::Bold)
    $panelInfo.Controls.Add($lblOutput)

    $lblNote = New-Object System.Windows.Forms.Label
    $lblNote.Text = '倍率越大越清晰，同时输出图片也会更大。'
    $lblNote.Location = [System.Drawing.Point]::new(12, 27)
    $lblNote.Size = [System.Drawing.Size]::new(386, 18)
    $lblNote.ForeColor = [System.Drawing.Color]::DimGray
    $panelInfo.Controls.Add($lblNote)

    $btnOK = New-Object System.Windows.Forms.Button
    $btnOK.Text = '开始拼图'
    $btnOK.Location = [System.Drawing.Point]::new(260, 374)
    $btnOK.Size = [System.Drawing.Size]::new(82, 32)
    $btnOK.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.AcceptButton = $btnOK
    $form.Controls.Add($btnOK)

    $btnCancel = New-Object System.Windows.Forms.Button
    $btnCancel.Text = '取消'
    $btnCancel.Location = [System.Drawing.Point]::new(354, 374)
    $btnCancel.Size = [System.Drawing.Size]::new(74, 32)
    $btnCancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.CancelButton = $btnCancel
    $form.Controls.Add($btnCancel)

    $updatePreview = {
        try {
            $scale = [double]$numScale.Value
            $first = $Infos[0]
            $fw = [int][Math]::Round([double]$first.Width*$scale)
            $fh = [int][Math]::Round([double]$first.Height*$scale)
            $lblExample.Text = ('首张示例：{0}×{1} → 约 {2}×{3} px' -f [int]$first.Width,[int]$first.Height,$fw,$fh)
            $previewMode = 'Width'
            if ($radHeight.Checked) { $previewMode = 'Height' }
            $tmp = [pscustomobject]@{
                Columns=[int]$numCols.Value
                AdaptMode=$previewMode
                Spacing=[int]$numSpace.Value
                Margin=[int]$numMargin.Value
                Scale=$scale
            }
            $layout = Get-Layout $Infos $tmp
            $pixels = [long]$layout.Width * [long]$layout.Height
            if ($layout.Width -gt 30000 -or $layout.Height -gt 30000 -or $pixels -gt 120000000) {
                $lblOutput.Text = ('预计输出：{0} × {1} px  ·  尺寸过大' -f $layout.Width,$layout.Height)
                $lblOutput.ForeColor = [System.Drawing.Color]::Firebrick
                $btnOK.Enabled = $false
            } else {
                $lblOutput.Text = ('预计输出：{0} × {1} px  ·  约 {2:N1} MP' -f $layout.Width,$layout.Height,($pixels/1000000.0))
                $lblOutput.ForeColor = [System.Drawing.Color]::Black
                $btnOK.Enabled = $true
            }
        } catch {
            $lblOutput.Text = '预计输出：无法计算'
            $lblOutput.ForeColor = [System.Drawing.Color]::Firebrick
            $btnOK.Enabled = $false
        }
    }

    $btnHalf.Add_Click({ $numScale.Value = [decimal]0.50 })
    $btnFull.Add_Click({ $numScale.Value = [decimal]1.00 })
    $numCols.Add_ValueChanged($updatePreview)
    $radWidth.Add_CheckedChanged($updatePreview)
    $radHeight.Add_CheckedChanged($updatePreview)
    $numScale.Add_ValueChanged($updatePreview)
    $numSpace.Add_ValueChanged($updatePreview)
    $numMargin.Add_ValueChanged($updatePreview)
    & $updatePreview

    $form.Add_Shown({
        try {
            $form.TopMost = $true
            [void][PicGridForeground]::ShowWindow($form.Handle, 9)
            [void][PicGridForeground]::BringWindowToTop($form.Handle)
            [void][PicGridForeground]::SetForegroundWindow($form.Handle)
            $form.Activate()
            $btnOK.Focus()
            $focusTimer = New-Object System.Windows.Forms.Timer
            $focusTimer.Interval = 240
            $focusTimer.Add_Tick({
                param($sender, $e)
                try {
                    $sender.Stop()
                    $form.TopMost = $false
                    $form.Activate()
                } finally {
                    $sender.Dispose()
                }
            })
            $focusTimer.Start()
        } catch { }
    })

    $result = $form.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK) {
        $form.Dispose(); return $null
    }

    $selectedMode = 'Width'
    if ($radHeight.Checked) { $selectedMode = 'Height' }
    $ret = [pscustomobject]@{
        Columns = [int]$numCols.Value
        AdaptMode = $selectedMode
        Scale = [double]$numScale.Value
        Spacing = [int]$numSpace.Value
        Margin = [int]$numMargin.Value
        Format = [string]$cmbFormat.SelectedItem
        OpenAfter = [bool]$chkOpen.Checked
    }
    # 保存常用布局设置；倍率故意不保存，每次打开都默认 0.5×。
    Save-Settings ([pscustomobject]@{
        Columns=$ret.Columns; AdaptMode=$ret.AdaptMode; Spacing=$ret.Spacing; Margin=$ret.Margin;
        Format=$ret.Format; OpenAfter=$ret.OpenAfter
    })
    $form.Dispose()
    return $ret
}

function Compose-Custom([object[]]$Infos) {
    if ($Infos.Count -lt 2) { Show-Info '请至少选择 2 张图片。'; return }
    $s = Show-SettingsDialog $Infos
    if ($null -eq $s) { return }
    $layout = Get-Layout $Infos $s
    Write-Log ("Compose mode={0} count={1} cols={2} scale={3} output={4}x{5}" -f $s.AdaptMode,$Infos.Count,$s.Columns,$s.Scale,$layout.Width,$layout.Height)
    $out = Compose-FromLayout $Infos $s $layout
    if ($s.OpenAfter -and $out) {
        Start-Process explorer.exe -ArgumentList ('/select,"' + $out + '"')
    }
}

function Decode-QueueLine([string]$Line) {
    try { return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Line)) }
    catch { return $null }
}

function Enqueue-ExplorerSelection([string[]]$IncomingPaths) {
    $IncomingPaths = @($IncomingPaths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($IncomingPaths.Count -eq 0) { return }
    $queueDir = Join-Path $env:TEMP 'PicGridQueue'
    if (-not (Test-Path $queueDir)) { New-Item -ItemType Directory -Path $queueDir -Force | Out-Null }
    $queueFile = Join-Path $queueDir 'Custom.queue'

    $createdNew = $false
    $leader = [System.Threading.Mutex]::new($false, 'Local\PicGridLeader_Custom', [ref]$createdNew)
    $writer = [System.Threading.Mutex]::new($false, 'Local\PicGridWriter_Custom')
    $utf8NoBom = [System.Text.UTF8Encoding]::new($false)

    try {
        [void]$writer.WaitOne()
        try {
            if (Test-Path $queueFile) {
                $age = ((Get-Date) - (Get-Item $queueFile).LastWriteTime).TotalSeconds
                if ($age -gt 30) { Remove-Item $queueFile -Force -ErrorAction SilentlyContinue }
            }
            foreach ($incoming in $IncomingPaths) {
                $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($incoming))
                [System.IO.File]::AppendAllText($queueFile, $encoded + [Environment]::NewLine, $utf8NoBom)
            }
        } finally {
            try { $writer.ReleaseMutex() } catch { }
        }

        if (-not $createdNew) { return }

        $deadline = (Get-Date).AddSeconds(10)
        $lastStamp = [DateTime]::MinValue
        $stable = 0
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 250
            [void]$writer.WaitOne()
            try {
                if (Test-Path $queueFile) { $stamp = (Get-Item $queueFile).LastWriteTimeUtc }
                else { $stamp = [DateTime]::MinValue }
            } finally {
                try { $writer.ReleaseMutex() } catch { }
            }
            if ($stamp -eq $lastStamp -and $stamp -ne [DateTime]::MinValue) {
                $stable++
                if ($stable -ge 3) { break }
            } else { $lastStamp=$stamp; $stable=0 }
        }

        [void]$writer.WaitOne()
        try {
            if (Test-Path $queueFile) {
                $lines = @(Get-Content -LiteralPath $queueFile -Encoding UTF8 | Where-Object { $_ })
                Remove-Item $queueFile -Force -ErrorAction SilentlyContinue
            } else { $lines=@() }
        } finally {
            try { $writer.ReleaseMutex() } catch { }
        }

        $collected = @($lines | ForEach-Object { Decode-QueueLine $_ } | Where-Object { $_ })
        $valid = @(Normalize-Paths $collected)
        Write-Log ("Queue collected={0}, valid={1}" -f $collected.Count,$valid.Count)
        $infos = @(Get-ImageInfos $valid)
        Write-Log ("Readable images={0}" -f $infos.Count)
        Compose-Custom $infos
    } finally {
        $writer.Dispose(); $leader.Dispose()
    }
}

try {
    Write-Log ("Start v=$script:Version mode=$Mode input=$InputPath directCount=" + @($Paths).Count)
    if ($Mode -eq 'Custom') {
        Enqueue-ExplorerSelection (@($InputPath) + @($RemainingPaths))
    } elseif ($Mode -eq 'DirectCustom') {
        $valid = @(Normalize-Paths $Paths)
        $infos = @(Get-ImageInfos $valid)
        Compose-Custom $infos
    }
} catch {
    $detail = $_.Exception.Message
    Write-Log ("Unhandled: " + $_.Exception.ToString())
    Show-Error ("拼图失败。`r`n`r`n" + $detail + "`r`n`r`n日志：" + $script:LogFile)
}
