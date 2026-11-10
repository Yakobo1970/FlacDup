; FlacDup Setup - Inno Setup Skript
;
; Packt das mit PyInstaller gebaute, eigenstaendige dist\FlacDup\-Verzeichnis (fertige .exe
; samt Python, Qt, FFmpeg, libsndfile, PortAudio) in einen einzelnen Installer.
;
; Vor dem Bauen erst die .exe erzeugen (in der Conda-Umgebung "flacdup"):
;   python -m PyInstaller --noconfirm FlacDup.spec
;
; Dann bauen:
;   "C:\Program Files\Inno Setup 7\ISCC.exe" installer\FlacDup.iss
;
; Bei neuen Versionen AppId beibehalten (sonst gilt es als anderes Programm), nur AppVersion anheben.

#define AppName "FlacDup"
#define AppVersion "1.0.0"
#define AppPublisher "yakobo1970"
#define AppURL "https://www.undabtanzbar.de/software/flacdup/"
#define AppExeName "FlacDup.exe"

[Setup]
AppId={{A23219FD-F9EA-440E-A4D6-CC6371DA994C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL=https://github.com/Yakobo1970/FlacDup/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\assets\icon.ico
OutputDir=..\dist_installer
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
; Installiert pro Benutzer - keine Admin-Rechte noetig. Die Datenbank liegt unabhaengig davon in
; %LOCALAPPDATA%\FlacDup und bleibt beim Deinstallieren erhalten.
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppName}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
