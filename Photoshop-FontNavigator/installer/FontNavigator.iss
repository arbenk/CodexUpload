#define MyAppName "字体导航与批量替换"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "Local"
#define MyExtensionId "com.local.fontnavigator.cep"

[Setup]
AppId={{96A45C3A-1EB8-47D1-A714-187241BC504B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
DefaultDirName={userappdata}\Adobe\CEP\extensions\{#MyExtensionId}
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Photoshop-FontNavigator
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UninstallDisplayIcon={uninstallexe}

[Files]
Source: "..\CSXS\manifest.xml"; DestDir: "{app}\CSXS"; Flags: ignoreversion
Source: "..\js\panel.js"; DestDir: "{app}\js"; Flags: ignoreversion
Source: "..\js\font-localizer.js"; DestDir: "{app}\js"; Flags: ignoreversion
Source: "..\jsx\host.jsx"; DestDir: "{app}\jsx"; Flags: ignoreversion
Source: "..\index.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\styles.css"; DestDir: "{app}"; Flags: ignoreversion

[Registry]
Root: HKCU; Subkey: "Software\Adobe\CSXS.9"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"
Root: HKCU; Subkey: "Software\Adobe\CSXS.10"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"
Root: HKCU; Subkey: "Software\Adobe\CSXS.11"; ValueType: string; ValueName: "PlayerDebugMode"; ValueData: "1"

[Icons]
Name: "{autoprograms}\字体导航\卸载字体导航"; Filename: "{uninstallexe}"

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
    MsgBox('安装完成。请完全退出并重新启动 Photoshop，然后从“窗口 > 扩展功能（旧版）”打开“字体导航”。', mbInformation, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usDone then
    MsgBox('插件已卸载。请重新启动 Photoshop。', mbInformation, MB_OK);
end;
