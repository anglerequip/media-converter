; Inno Setup script for MediaConverter-Setup.exe. Run through build\build.ps1, which passes AppVersion.
; Installs per user, so no administrator password is needed.

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
DefaultDirName={localappdata}\Programs\Media Converter
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MediaConverter-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MediaConverter.exe
UninstallDisplayName=Media Converter
Compression=lzma2/ultra64
SolidCompression=yes
; Compress in a separate process: the compiler itself runs out of memory on ~400 MB of input.
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=2
WizardStyle=modern
; Installing over a running copy closes it first, so no file is left half-replaced.
CloseApplications=force
RestartApplications=no
; Tell Windows to refresh its icon cache, so the new icon replaces any cached old one straight away.
ChangesAssociations=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Media Converter installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[InstallDelete]
; Remove the previous version's libraries so a reinstall never mixes old and new files. Only done when the
; folder already holds MediaConverter.exe, so a hand-picked folder's own "ffmpeg" or "_internal" is never touched.
Type: filesandordirs; Name: "{app}\_internal"; Check: IsMediaConverterFolder
Type: filesandordirs; Name: "{app}\ffmpeg"; Check: IsMediaConverterFolder

[Files]
Source: "..\dist\MediaConverter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Media Converter"; Filename: "{app}\MediaConverter.exe"
Name: "{autodesktop}\Media Converter"; Filename: "{app}\MediaConverter.exe"; Tasks: desktopicon

[Code]
function IsMediaConverterFolder: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\MediaConverter.exe'));
end;

[Run]
; The "Launch Media Converter" checkbox on the last page.
Filename: "{app}\MediaConverter.exe"; Description: "{cm:LaunchProgram,Media Converter}"; Flags: nowait postinstall skipifsilent
