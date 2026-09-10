"""
make_shortcut.py — Crée le raccourci « Dynamics » sur le Bureau.

Le raccourci pointe vers dist_release/Dynamics.exe. Ce fichier est remplacé sur
place à chaque rebuild automatique (hook post-commit), donc le raccourci n'est à
créer qu'une seule fois : il suivra toutes les versions suivantes.

Usage : python make_shortcut.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
TARGET = ROOT / "dist_release" / "Dynamics.exe"
SHORTCUT_NAME = "Dynamics.lnk"


def desktop_dir() -> Path:
    """Bureau de l'utilisateur, y compris quand OneDrive l'a redirigé."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
        capture_output=True, text=True,
    )
    path = out.stdout.strip()
    if not path:
        # Repli : le chemin classique reste correct hors redirection OneDrive.
        return Path.home() / "Desktop"
    return Path(path)


def main() -> None:
    if sys.platform != "win32":
        print("[shortcut] Windows uniquement.")
        sys.exit(1)
    if not TARGET.exists():
        print(f"[shortcut] ERREUR : {TARGET} introuvable. Lance d'abord : python build.py")
        sys.exit(1)

    link = desktop_dir() / SHORTCUT_NAME
    # L'icône est lue depuis le .exe lui-même (build.spec l'y intègre) plutôt
    # que depuis assets/ : déplacer le dépôt ne transforme pas le raccourci du
    # Bureau en icône blanche.
    icon = TARGET

    def ps_quote(value: Path) -> str:
        """Chaîne PowerShell littérale : seule l'apostrophe doit être doublée,
        et un chemin Windows ne subit alors aucune interprétation."""
        return "'" + str(value).replace("'", "''") + "'"

    # powershell.exe -Command ne transmet pas d'arguments à $args : les chemins
    # sont inlinés dans le script, littéralement et échappés.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + ps_quote(link) + "); "
        "$s.TargetPath = " + ps_quote(TARGET) + "; "
        "$s.WorkingDirectory = " + ps_quote(TARGET.parent) + "; "
        "$s.IconLocation = " + ps_quote(icon) + "; "
        "$s.Description = 'OBS Dynamics'; "
        "$s.Save()"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[shortcut] ERREUR : {result.stderr.strip()}")
        sys.exit(1)
    print(f"[shortcut] Raccourci créé -> {link}")


if __name__ == "__main__":
    main()
