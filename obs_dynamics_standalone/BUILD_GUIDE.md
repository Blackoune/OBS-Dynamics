# OBS Dynamics — Build & Distribution

## 1. Installer les dépendances (dev, une fois)
```powershell
cd "C:\Users\Utilisateur\Documents\GitHub\Interface-de-Gestion-OBS-Dynamics"
py -m pip install -r requirements.txt --upgrade
```

## 2. Générer l'icône (une fois, ou après modif du design)
```powershell
py generate_icon.py
```
Produit `assets\icon.ico`.

## 3. Tester en local avant compilation
```powershell
py obs_dynamics.py
```

## 4. Compiler en .exe autonome (PyInstaller)
```powershell
py -m PyInstaller build.spec --clean
```
Résultat : `dist\OBS Dynamics.exe` — un seul fichier, aucune dépendance Python requise sur la machine cible, icône embarquée (barre de titre + barre des tâches).

## 5. Générer l'installateur (Inno Setup)
1. Installer [Inno Setup](https://jrsoftware.org/isdl.php) (une fois, poste de dev uniquement).
2. Compiler :
```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```
Résultat : `dist_installer\OBSDynamics_Setup.exe` — installateur classique (assistant, choix du dossier, case "Créer un raccourci Bureau" cochée par défaut, lancement auto en fin d'install).

## 6. Distribution à l'utilisateur final
Donne uniquement `OBSDynamics_Setup.exe`. L'utilisateur :
- double-clique, suit l'assistant (aucun PowerShell, aucun Python requis)
- trouve le raccourci sur son Bureau automatiquement
- au premier lancement, va dans **Paramètres** et renseigne l'hôte/port/mot de passe OBS WebSocket (écrit dans `.env` à côté de l'exe)

## Notes
- `env.example` est copié dans le dossier d'installation à titre indicatif ; le vrai `.env` est créé/modifié automatiquement par l'onglet Paramètres de la GUI.
- Le détecteur Steam lit le registre Windows (`HKCU\Software\Valve\Steam`) — fonctionne uniquement sur la machine où Steam est installé, aucune requête réseau.
- Icône barre des tâches : `SetCurrentProcessExplicitAppUserModelID` est appelé au démarrage pour éviter que Windows affiche l'icône Python générique.
