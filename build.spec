# -*- mode: python ; coding: utf-8 -*-
# Build: py -m PyInstaller build.spec --noconfirm --clean
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

# assets/ (icon, images) et data/ (games.json vierge) sont embarqués s'ils
# existent déjà à la racine du projet ; sinon PyInstaller les recrée au
# premier lancement via get_base_path()/DATA_DIR.mkdir(exist_ok=True).
datas = []

# tkinterdnd2 embarque une extension Tcl (dossier tkdnd/) chargée à l'exécution :
# sans ses binaires, l'exe se lance mais le glisser-déposer est muet.
try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files("tkinterdnd2")
except Exception:
    pass

if (ROOT / "assets").exists():
    datas.append((str(ROOT / "assets"), "assets"))
if (ROOT / "i18n.json").exists():
    datas.append((str(ROOT / "i18n.json"), "."))

a = Analysis(
    ["obs_dynamics.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "simpleobsws",
        "cv2",
        "numpy",
        "psutil",
        "customtkinter",
        "PIL._tkinter_finder",
        "i18n",
        "cover_service",   # jaquettes (requests + PIL)
        "hotkeys",         # hotkeys globales (pynput, optionnel au runtime)
        "triggers",        # règles raccourci -> média
        "screen_match",    # agent de comparaison écran/référence
        "overlay_server",  # serveur HTTP local des sources navigateur OBS
        "twitch_chat",     # connecteur de chat Twitch + hub
        # Modules issus du découpage d'obs_dynamics.py (2026-09-09). Le point
        # d'entrée les importe explicitement, donc PyInstaller les trouverait
        # seul ; les lister protège d'un futur import différé.
        "app_paths",       # chemins, journalisation, éveil DPI
        "env_config",      # lecture/écriture du .env utilisateur
        "secret_store",    # chiffrement DPAPI des identifiants au repos
        "games",           # scan Steam, modèle Game, persistance
        "detection",       # processus + comparaison visuelle
        "obs_client",      # WebSocket OBS v5 et boucle de scan
        "ui_common",       # palette, police, libellés d'état, glisser-déposer
        "ui_dashboard",    # grille de cartes de jeu
        "ui_game_dialogs", # fiche de jeu et relecture des patchs
        "ui_settings",     # vue Paramètres
        "ui_triggers",     # vue Raccourcis & Overlays
        "ui_twitch_chat",  # vue Chat Twitch
        "requests",
        "pynput",
        "tkinterdnd2",     # glisser-déposer (extension Tcl tkdnd)
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pandas", "tkinter.test"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OBSDynamics",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # False = pas de fenêtre console noire derrière la GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico") if (ROOT / "assets" / "icon.ico").exists() else None,
)
