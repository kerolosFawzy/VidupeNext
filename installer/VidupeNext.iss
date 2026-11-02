#define MyAppName "Vidupe Next"
#define MyAppVersion "0.1.0"
#define MyAppExeName "VidupeNext.exe"
[Setup]
AppId={{66D2C81B-52F0-4DD8-89F8-D7E1198C2A5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\Vidupe Next
DefaultGroupName=Vidupe Next
OutputDir=..\installer-output
OutputBaseFilename=VidupeNext_Setup_0.1.0
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
[Files]
Source: "..\dist\VidupeNext\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Vidupe Next"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Vidupe Next"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Vidupe Next"; Flags: nowait postinstall skipifsilent
