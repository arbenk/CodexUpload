Option Explicit
Dim sh, fso, psExe, tool, mode, inputPath, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If WScript.Arguments.Count < 2 Then WScript.Quit 1
mode = WScript.Arguments(0)
inputPath = WScript.Arguments(1)
psExe = sh.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
tool = sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\PicGrid\PicGrid.ps1"

cmd = Q(psExe) & " -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File " & Q(tool) & " -Mode " & mode & " -InputPath " & Q(inputPath)
sh.Run cmd, 0, False

Function Q(ByVal s)
    Q = Chr(34) & s & Chr(34)
End Function
