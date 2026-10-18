; Inno Setup script for MediaConverter-Setup.exe. Run through build\build.ps1, which passes AppVersion.
; Installs per user (no administrator prompt), so the app can also update itself silently.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8F3A2C61-5B7E-4E2A-9C1D-6A7B3E9F2D41}
AppName=Media Converter
AppVersion={#AppVersion}
AppVerName=Media Converter {#AppVersion}
AppPublisher=anglerequip
AppPublisherURL=https://github.com/anglerequip/media-converter
AppSupportURL=https://github.com/anglerequip/media-converter/issues
AppUpdatesURL=https://github.com/anglerequip/media-converter/releases
DefaultDirName={localappdata}\Programs\Media Converter
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MediaConverter-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MediaConverter.exe
UninstallDisplayName=Media Converter
Compression=lzma2/max
SolidCompression=yes
; Compress in a separate process: the compiler itself runs out of memory on ~400 MB of input.
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=2
WizardStyle=modern
; An update closes the running app first; the [Run] entry below starts it again.
CloseApplications=force
RestartApplications=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Media Converter installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Remove the previous version's libraries so an update never mixes old and new files.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\ffmpeg"

[Files]
Source: "..\dist\MediaConverter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Media Converter"; Filename: "{app}\MediaConverter.exe"
Name: "{autodesktop}\Media Converter"; Filename: "{app}\MediaConverter.exe"; Tasks: desktopicon

[Run]
; Also runs after a silent update, which is how the app reopens itself.
Filename: "{app}\MediaConverter.exe"; Description: "{cm:LaunchProgram,Media Converter}"; Flags: nowait postinstall
