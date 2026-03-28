#define MyAppName "Toast POS Manager"
#ifndef MyAppVersion
  #define MyAppVersion "dev"
#endif
#ifndef MySourceDir
  #define MySourceDir "..\dist\ToastPOSManager"
#endif
#ifndef MyOutputDir
  #define MyOutputDir "..\release"
#endif

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\Toast POS Manager
DefaultGroupName=Toast POS Manager
OutputDir={#MyOutputDir}
OutputBaseFilename=ToastPOSManager-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Toast POS Manager"; Filename: "{app}\ToastPOSManager.exe"
Name: "{autodesktop}\Toast POS Manager"; Filename: "{app}\ToastPOSManager.exe"

[Run]
Filename: "{app}\ToastPOSManager.exe"; Description: "Launch Toast POS Manager"; Flags: nowait postinstall skipifsilent
