Option Explicit
Dim sh, fso, baseDir, psExe, scriptPath, cmd
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
psExe = sh.ExpandEnvironmentStrings("%SystemRoot%") & "\System32\WindowsPowerShell\v1.0\powershell.exe"
scriptPath = fso.BuildPath(baseDir, "Install.ps1")
cmd = Q(psExe) & " -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File " & Q(scriptPath)
sh.Run cmd, 0, True
Function Q(ByVal s)
    Q = Chr(34) & s & Chr(34)
End Function
