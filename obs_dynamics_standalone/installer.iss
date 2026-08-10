; OBS Dynamics — Installateur Inno Setup
; Compilation: ouvre ce fichier dans Inno Setup Compiler (iscc.exe installer.iss)
; Génère un assistant d'installation classique (dossier cible, licence, progression)
; + crée automatiquement un raccourci sur le Bureau à la fin.

#define MyAppName "OBS Dynamics"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Tristan"
#define MyAppExeName "OBS Dynamics.exe"

[Setup]
AppId={{8F2C1A4E-4B2E-4E9A-9C2F-OBSDYNAMICS01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Dossier de sortie de l'installateur généré (setup.exe)
OutputDir=dist_installer
OutputBaseFilename=OBSDynamics_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
; Case à cocher pendant l'installation — cochée par défaut = raccourci créé automatiquement
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Icônes supplémentaires:"; Flags: checkedonce

[Files]
; Exécutable produit par PyInstaller (dist\OBS Dynamics.exe)
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Fichier .env.example fourni pour que l'utilisateur configure OBS au premier lancement
Source: "env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Désinstaller {#MyAppName}"; Filename: "{uninstallexe}"
; Raccourci Bureau — dépend de la case cochée dans [Tasks] ci-dessus
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Lancer {#MyAppName}"; Flags: nowait postinstall skipifsilent
