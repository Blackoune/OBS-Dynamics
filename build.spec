# -*- mode: python ; coding: utf-8 -*-
# Build: py -m PyInstaller build.spec --noconfirm --clean
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH)

# Propriétés du fichier (clic droit > Propriétés > Détails) : nom du produit et
# version, exigés par SignPath. Version lue du tag de release (v1.2.3) en CI,
# 0.0.0 en local.
import os
import re
_tag = os.environ.get("GITHUB_REF_NAME", "")
_nums = [int(n) for n in re.findall(r"\d+", _tag.split("-")[0])[:3]] if _tag.startswith("v") else []
_nums = (_nums + [0, 0, 0])[:3]
_version = ".".join(map(str, _nums))
VERSION_FILE = ROOT / "build" / "version_info.txt"
VERSION_FILE.parent.mkdir(exist_ok=True)
# Ressource de version PE : Windows seulement (pefile n'est installé que là).
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
    )
    VERSION_FILE.write_text(str(VSVersionInfo(
        ffi=FixedFileInfo(filevers=(*_nums, 0), prodvers=(*_nums, 0)),
        kids=[
            StringFileInfo([StringTable("040C04B0", [
                StringStruct("CompanyName", "Blackoune"),
                StringStruct("FileDescription", "OBS Dynamics"),
                StringStruct("FileVersion", _version),
                StringStruct("InternalName", "Dynamics"),
                StringStruct("LegalCopyright", "© Blackoune, GPL-3.0"),
                StringStruct("OriginalFilename", "Dynamics.exe"),
                StringStruct("ProductName", "OBS Dynamics"),
                StringStruct("ProductVersion", _version),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x040C, 1200])]),
        ],
    )), encoding="utf-8")

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

# Version lue par update_check.py pour comparer avec la dernière release.
(ROOT / "build" / "version.txt").write_text(_version, encoding="utf-8")
datas.append((str(ROOT / "build" / "version.txt"), "."))

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
        "music_smtc",      # sonde SMTC du widget musique
        "music_overlay",   # hub et page d'overlay du widget musique
        "music_catalog",   # lecteurs connus, couleurs et logos
        "music_style",     # apparence de l'overlay, templates, gabarit
        "music_audio",     # niveau audio par application
        "pycaw",           # compteurs de session audio
        "comtypes",        # interfaces COM chargees dynamiquement
        "ui_music_style",  # fenêtre Widget
        "ui_music",        # vue Widget Musique
        # Chargée à la volée par la projection WinRT, jamais importée par nos
        # fichiers : PyInstaller ne peut pas la déduire de l'arbre d'imports.
        "winrt.windows.foundation.collections",
        "requests",
        "pynput",
        "tkinterdnd2",     # glisser-déposer (extension Tcl tkdnd)
    ] + ([
        # macOS : modules choisis à l'exécution, invisibles à l'analyse.
        "keyring.backends.macOS",   # Trousseau (secret_store)
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
    ] if sys.platform == "darwin" else []),
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

if sys.platform == "darwin":
    # macOS : une app `.app` en mode dossier. Le mode un-seul-fichier dans un
    # .app est refusé par PyInstaller 7 et relancerait l'extraction à chaque
    # ouverture. Signature ad hoc (`codesign_identity=None`) : sans aucune
    # signature, un Mac à puce Apple dit « endommagée » au lieu de proposer
    # « Ouvrir quand même ».
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Dynamics",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch="arm64",
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
                   strip=False, upx=False, name="Dynamics")
    app = BUNDLE(
        coll,
        name="OBS Dynamics.app",
        icon=str(ROOT / "assets" / "icon.ico"),  # converti en .icns via Pillow
        # Identifiant STABLE : macOS rattache les autorisations (Enregistrement
        # de l'écran, Surveillance de l'entrée) à lui. Le changer d'une version
        # à l'autre ferait tout redemander à chaque mise à jour.
        bundle_identifier="io.github.blackoune.obsdynamics",
        version=_version,
        info_plist={
            "CFBundleName": "OBS Dynamics",
            "CFBundleDisplayName": "OBS Dynamics",
            "CFBundleShortVersionString": _version,
            "CFBundleVersion": _version,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "© Blackoune, GPL-3.0",
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="Dynamics",
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
        version=str(VERSION_FILE),
    )
