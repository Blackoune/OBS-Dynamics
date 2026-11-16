"""
Vérification de mise à jour au démarrage.

Une seule requête vers l'API GitHub (dernière release publiée, préversions
exclues). Version installée sous Windows : le bouton télécharge l'installeur
et ne le lance que si son SHA-256 est celui publié avec la release. Sinon
(portable, Mac) : il ouvre la page Installation du site.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import requests

from app_paths import get_base_path, logger

LATEST_URL = "https://api.github.com/repos/Blackoune/OBS-Dynamics/releases/latest"
DOWNLOAD_URL = "https://github.com/Blackoune/OBS-Dynamics/releases/download"
SITE_URL = "https://blackoune.github.io"
INSTALLER = "OBS-Dynamics-Setup.exe"
MAX_BYTES = 500_000_000  # l'installeur pèse environ 95 Mo ; au-delà, ce n'en est pas un
VERSION_RE = re.compile(r"\d+(\.\d+){0,2}(-[0-9A-Za-z.]{1,20})?")


class UpdateError(Exception):
    pass


def parse_version(texte: str) -> tuple[int, int, int]:
    """'v1.2.3' ou '1.2.3-test' -> (1, 2, 3). Partie manquante = 0."""
    nums = [int(n) for n in re.findall(r"\d+", texte.split("-")[0])[:3]]
    return tuple((nums + [0, 0, 0])[:3])  # type: ignore[return-value]


def current_version() -> Optional[str]:
    """Version écrite par build.spec dans l'exe. None depuis les sources
    ou pour un build local (0.0.0) : pas de vérification dans ces cas."""
    try:
        texte = (get_base_path() / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return texte if parse_version(texte) > (0, 0, 0) else None


def newer_version(current: str, latest_tag: str) -> Optional[str]:
    """Numéro de la release si elle est plus récente que la version installée."""
    if parse_version(latest_tag) > parse_version(current):
        return latest_tag.lstrip("v")
    return None


def install_page(lang: str, systeme: str = sys.platform) -> str:
    """Section « Mettre à jour » du site, en français ou sinon en anglais.

    Sur Mac, la section de l'onglet Mac : c'est là qu'est le .dmg, pas
    l'installeur Windows."""
    prefixe = "" if lang == "fr" else "/en"
    ancre = "titre-mise-a-jour-mac" if systeme == "darwin" else "mise-a-jour"
    return f"{SITE_URL}{prefixe}/installation/#{ancre}"


def check(timeout: float = 5.0) -> Optional[str]:
    """Bloquant : à appeler hors du thread Tk. Ne lève jamais."""
    current = current_version()
    if current is None:
        return None
    try:
        reponse = requests.get(LATEST_URL, timeout=timeout,
                               headers={"Accept": "application/vnd.github+json"})
        reponse.raise_for_status()
        tag = str(reponse.json().get("tag_name", ""))
    except (requests.RequestException, ValueError) as exc:
        logger.info("Vérification de mise à jour impossible : %s", exc)
        return None
    return newer_version(current, tag)


def can_self_install() -> bool:
    """Vrai pour la version installée sous Windows (désinstalleur Inno Setup à
    côté de l'exe). Version portable, Mac ou sources : page Installation."""
    return sys.platform == "win32" and (Path(sys.executable).parent / "unins000.exe").is_file()


def _fetch(url: str, sortie: Optional[Path] = None) -> tuple[bytes, str]:
    """Télécharge en https, taille bornée. Renvoie (contenu, SHA-256) ; le
    contenu part dans `sortie` si elle est donnée."""
    with requests.get(url, timeout=30, stream=True) as reponse:
        reponse.raise_for_status()
        if not reponse.url.startswith("https://"):  # une redirection ne doit pas quitter https
            raise ValueError("redirection hors https")
        empreinte, morceaux, taille = hashlib.sha256(), [], 0
        fichier = sortie.open("wb") if sortie else None
        try:
            for bloc in reponse.iter_content(1 << 20):
                taille += len(bloc)
                if taille > MAX_BYTES:
                    raise ValueError("réponse trop grosse")
                empreinte.update(bloc)
                fichier.write(bloc) if fichier else morceaux.append(bloc)
        finally:
            if fichier:
                fichier.close()
    return b"".join(morceaux), empreinte.hexdigest()


def expected_sha256(sommes: str, nom: str = INSTALLER) -> Optional[str]:
    """Empreinte de `nom` dans un SHA256SUMS.txt (format sha256sum)."""
    for ligne in sommes.splitlines():
        parts = ligne.split()
        if len(parts) == 2 and parts[1].lstrip("*") == nom:
            return parts[0].lower()
    return None


def install_update(version: str, fetch=_fetch, launch=subprocess.Popen) -> None:
    """Bloquant. Télécharge l'installeur de la release, le lance seulement si
    son SHA-256 est celui du SHA256SUMS.txt de la même release."""
    if not VERSION_RE.fullmatch(version):  # la version entre dans une URL
        raise UpdateError(f"version inattendue : {version!r}")
    base = f"{DOWNLOAD_URL}/v{version}"
    chemin = Path(tempfile.mkdtemp(prefix="obs-dynamics-maj-")) / INSTALLER
    try:
        attendue = expected_sha256(fetch(f"{base}/SHA256SUMS.txt")[0].decode("ascii", "replace"))
        _, obtenue = fetch(f"{base}/{INSTALLER}", chemin)
    except (requests.RequestException, OSError, ValueError) as exc:
        raise UpdateError(f"téléchargement impossible : {exc}") from exc
    if not attendue or obtenue != attendue:
        chemin.unlink(missing_ok=True)
        raise UpdateError("SHA-256 différent : installeur refusé")
    # Sans ça, l'application relancée par l'installeur hérite de
    # l'environnement PyInstaller de celle-ci et cherche ses fichiers au mauvais endroit.
    launch([str(chemin)], env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"})
