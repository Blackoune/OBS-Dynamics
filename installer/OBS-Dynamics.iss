; Assistant d'installation d'OBS Dynamics (Inno Setup 6).
; Compilé par .github/workflows/release.yml :
;   iscc /DAppVersion=1.0.1 installer\OBS-Dynamics.iss
; Entrée : dist_release\Dynamics.exe. Sortie : dist_release\OBS-Dynamics-Setup.exe.
;
; Installation par utilisateur : pas de fenêtre administrateur (UAC), dossier
; %LOCALAPPDATA%\Programs\OBS Dynamics. Les réglages restent dans %APPDATA%
; (app_paths.py) : une mise à jour ou une désinstallation n'y touche pas.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; Version numérique pour les propriétés du fichier (1.0.0-test -> 1.0.0).
#ifndef NumVersion
  #define NumVersion AppVersion
#endif
#define AppName "OBS Dynamics"
#define AppExe "Dynamics.exe"

[Setup]
; AppId fixe : une nouvelle version remplace l'ancienne au lieu de s'ajouter.
AppId={{EB8DF88E-36CF-458C-BD27-F034F2AEB199}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Blackoune
AppPublisherURL=https://blackoune.github.io/
AppSupportURL=https://blackoune.github.io/contact/
AppUpdatesURL=https://blackoune.github.io/installation/
AppCopyright=© Blackoune, GPL-3.0
VersionInfoVersion={#NumVersion}
VersionInfoDescription={#AppName} - installation

PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
CloseApplications=yes

WizardStyle=modern
WizardSizePercent=110
SetupIconFile=..\assets\icon.ico
WizardImageFile=wizard-100.bmp,wizard-200.bmp
WizardSmallImageFile=petit-100.bmp,petit-200.bmp
WizardImageStretch=no
WizardImageBackColor=$170F0B

LicenseFile=..\LICENSE
OutputDir=..\dist_release
OutputBaseFilename=OBS-Dynamics-Setup
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"; InfoBeforeFile: "infos-fr.txt"
Name: "en"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "infos-en.txt"

[Tasks]
Name: "bureau"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist_release\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Le raccourci s'appelle « OBS Dynamics », sans .exe.
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: bureau

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
