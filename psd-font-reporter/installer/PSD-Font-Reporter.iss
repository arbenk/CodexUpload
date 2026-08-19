#define MyAppName "PSD 字体批量查询"
#define MyAppVersion "0.2.2"
#define MyAppPublisher "Local"
#define MyAppExeName "PSD-Font-Reporter.exe"

[Setup]
AppId={{2B1581DD-FE61-4AF1-926A-F1FD5B89E8C4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
DefaultDirName={localappdata}\Programs\PSD-Font-Reporter
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=PSD-Font-Reporter-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\package\PSD-Font-Reporter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\PSD 字体批量查询"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 PSD 字体批量查询"; Flags: nowait postinstall skipifsilent
