"""
Vérification de mise à jour au démarrage.

Une seule requête vers l'API GitHub (dernière release publiée, préversions
exclues). Rien n'est téléchargé : l'application affiche un bouton qui ouvre
la page Installation du site, où l'utilisateur récupère l'installeur et peut
vérifier son empreinte.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

import requests

from app_paths import get_base_path, logger

LATEST_URL = "https://api.github.com/repos/Blackoune/OBS-Dynamics/releases/latest"
SITE_URL = "https://blackoune.github.io"


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
