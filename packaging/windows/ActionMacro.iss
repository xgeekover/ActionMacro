; Inno Setup 6 script for the ActionMacro Windows installer.
; Built by packaging/build_release.py, which passes the values below with /D:
;   ISCC /DAppVersion=0.1.0 /DSourceDir=dist\pyinstaller\ActionMacro /DOutputDir=dist\release
;        /DOutputBaseFilename=ActionMacro-0.1.0-windows-x64-setup /DIconFile=packaging\assets\ActionMacro.ico
;        packaging\windows\ActionMacro.iss
;
; Installs for the current user by default (no administrator rights needed); the
; first page lets the user choose an all-users install instead.

#ifndef AppVersion
  #error AppVersion is not defined (pass /DAppVersion=X.Y.Z)
#endif
#ifndef SourceDir
  #error SourceDir is not defined (the PyInstaller output folder)
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "ActionMacro-" + AppVersion + "-windows-x64-setup"
#endif

#define AppName "ActionMacro"
#define AppExeName "ActionMacro.exe"
#define AppUrl "https://github.com/xgeekover/ActionMacro"

[Setup]
; Never change AppId: Windows uses it to find an earlier install to upgrade or remove.
AppId={{C2722023-3879-4FB3-939D-DF8CBC8550FA}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppName}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}/issues
AppUpdatesURL={#AppUrl}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
#ifdef IconFile
SetupIconFile={#IconFile}
#endif
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(CompilerPath + "Languages\Korean.isl")
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller leaves no runtime files in {app}; remove the folder if it is empty.
Type: dirifempty; Name: "{app}"
