; Inno Setup script for the z-finance Windows installer (UC NF10, SPEC AK20).
;
; Installs for the current user only, so no administrator password is needed: "Doppelklick,
; Weiter, Fertig". Creates a Start menu entry and, if the user leaves the box ticked, a desktop
; icon.
;
; The data directory (%LOCALAPPDATA%\z-finance, UC NF2) is deliberately absent from this file.
; The installer writes only into {app}, and the uninstaller removes only what it installed, so an
; update and an uninstall both leave accounts, bookings, receipts and backups untouched. There is
; no [UninstallDelete] section, and adding one would break that promise.
;
; Built by packaging/build_installer.py:
;   ISCC /DAppVersion=1.2.3 packaging\z-finance.iss

#define AppName "z-finance"
#define AppExeName "z-finance.exe"
#define AppPublisher "z-finance"
#define AppUrl "https://github.com/ziagl888/z-finance"
; The running program holds this named mutex (APP_MUTEX_NAME in packaging/zfinance_launcher.py,
; kept equal by tests/test_packaging.py). With it, Setup and the uninstaller see a running
; z-finance and ask the user to quit it, instead of failing later on files in use.
#define AppMutexName "z-finance-8E2C5A17-9F43-4B6E-A0D8-6C1F2B7E4A95"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "build\dist\z-finance"
#endif
#ifndef OutputDirectory
  #define OutputDirectory "build"
#endif

[Setup]
; A stable AppId is what makes the next version an update of this one instead of a second program.
AppId={{8E2C5A17-9F43-4B6E-A0D8-6C1F2B7E4A95}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppMutex={#AppMutexName}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Install into the same folder as the previous version: one program, not one per release.
UsePreviousAppDir=yes
UsePreviousTasks=yes
DisableProgramGroupPage=yes
DisableDirPage=auto
; No administrator rights: a per-user install is all a personal tool needs.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDirectory}
OutputBaseFilename={#AppName}-setup-{#AppVersion}
SetupIconFile=build\{#AppName}.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
