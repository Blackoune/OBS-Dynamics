"""
build.py — Automatisation du packaging PyInstaller pour OBS Dynamics.

1. Vérifie l'intégrité i18n : toutes les clés t("...") utilisées dans
   obs_dynamics.py doivent exister dans i18n.json (langue par défaut), sinon
   le build est bloqué (évite de livrer un .exe affichant des clés brutes).
2. Lance `py -m PyInstaller build.spec --noconfirm --clean`.
3. Copie le .exe final vers dist_release/ (dossier de sortie prévisible).

Usage : python build.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE_FILE = ROOT / "obs_dynamics.py"
I18N_FILE = ROOT / "i18n.json"
BUILD_SPEC = ROOT / "build.spec"
DIST_DIR = ROOT / "dist"
RELEASE_DIR = ROOT / "dist_release"
DEFAULT_LANG = "fr"

# Repère tout appel t("CLE") ou t('CLE') — kwargs éventuels ignorés.
T_CALL_RE = re.compile(r"""\bt\(\s*["']([A-Za-z0-9_]+)["']""")


def check_i18n_integrity() -> list[str]:
    """Cross-référence chaque clé t("...") utilisée dans le code source avec
    i18n.json[DEFAULT_LANG]. Retourne la liste des clés manquantes."""
    if not SOURCE_FILE.exists():
        print(f"[i18n-check] ERREUR : {SOURCE_FILE} introuvable.")
        sys.exit(1)
    if not I18N_FILE.exists():
        print(f"[i18n-check] ERREUR : {I18N_FILE} introuvable.")
        sys.exit(1)

    source = SOURCE_FILE.read_text(encoding="utf-8")
    used_keys = set(T_CALL_RE.findall(source))

    try:
        catalog = json.loads(I18N_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[i18n-check] ERREUR : i18n.json invalide : {exc}")
        sys.exit(1)

    available = set(catalog.get(DEFAULT_LANG, {}).keys())
    missing = sorted(used_keys - available)
    return missing


def check_all_langs_consistent() -> dict[str, list[str]]:
    """Vérifie que toutes les langues déclarées dans i18n.json possèdent le
    même jeu de clés que la langue par défaut (pas de régression partielle
    lors de l'ajout d'une langue)."""
    catalog = json.loads(I18N_FILE.read_text(encoding="utf-8"))
    default_keys = set(catalog.get(DEFAULT_LANG, {}).keys())
    gaps: dict[str, list[str]] = {}
    for lang, table in catalog.items():
        if lang == DEFAULT_LANG:
            continue
        lang_keys = set(table.keys())
        missing = sorted(default_keys - lang_keys)
        if missing:
            gaps[lang] = missing
    return gaps


def run_pyinstaller() -> None:
    print("[build] Lancement PyInstaller...")
    cmd = [sys.executable, "-m", "PyInstaller", str(BUILD_SPEC), "--noconfirm", "--clean"]
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"[build] ERREUR : PyInstaller a échoué (code {result.returncode}).")
        sys.exit(result.returncode)


def collect_output() -> None:
    exe_name = "OBSDynamics.exe" if sys.platform == "win32" else "OBSDynamics"
    exe_path = DIST_DIR / exe_name
    if not exe_path.exists():
        print(f"[build] ERREUR : exécutable attendu introuvable : {exe_path}")
        sys.exit(1)
    RELEASE_DIR.mkdir(exist_ok=True)
    dest = RELEASE_DIR / exe_name
    shutil.copy2(exe_path, dest)
    print(f"[build] Exécutable copié -> {dest}")


def main() -> None:
    print("=" * 70)
    print("OBS Dynamics — Build")
    print("=" * 70)

    print(f"[i18n-check] Vérification des clés t(\"...\") vs i18n.json['{DEFAULT_LANG}']...")
    missing = check_i18n_integrity()
    if missing:
        print(f"[i18n-check] ÉCHEC : {len(missing)} clé(s) manquante(s) dans i18n.json :")
        for key in missing:
            print(f"  - {key}")
        print("[i18n-check] Build bloqué. Ajoute ces clés dans i18n.json avant de continuer.")
        sys.exit(1)
    print(f"[i18n-check] OK — 0 clé manquante ({len(missing)} manquante(s) sur clés utilisées).")

    gaps = check_all_langs_consistent()
    if gaps:
        print("[i18n-check] AVERTISSEMENT : langues secondaires incomplètes (non bloquant) :")
        for lang, keys in gaps.items():
            print(f"  [{lang}] {len(keys)} clé(s) manquante(s): {', '.join(keys[:5])}{'...' if len(keys) > 5 else ''}")
    else:
        print("[i18n-check] Toutes les langues déclarées sont complètes.")

    run_pyinstaller()
    collect_output()

    print("=" * 70)
    print("[build] Terminé avec succès.")
    print("=" * 70)


if __name__ == "__main__":
    main()
