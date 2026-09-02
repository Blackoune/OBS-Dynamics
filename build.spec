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
        "requests",
        "pynput",
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
