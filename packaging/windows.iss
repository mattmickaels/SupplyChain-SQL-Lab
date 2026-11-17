#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{3E3392D3-6956-441A-BF08-103605BF2429}
AppName=SupplyChain SQL Lab
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\SupplyChain SQL Lab
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DefaultGroupName=SupplyChain SQL Lab
DisableProgramGroupPage=yes
OutputDir=..\dist\installers
OutputBaseFilename=SupplyChain-SQL-Lab-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\SupplyChain SQL Lab.exe
CloseApplications=yes

[Files]
Source: "..\dist\SupplyChain SQL Lab\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\SupplyChain SQL Lab"; Filename: "{app}\SupplyChain SQL Lab.exe"
Name: "{userdesktop}\SupplyChain SQL Lab"; Filename: "{app}\SupplyChain SQL Lab.exe"

[Run]
Filename: "{app}\SupplyChain SQL Lab.exe"; Description: "Open SupplyChain SQL Lab"; Flags: nowait postinstall skipifsilent
