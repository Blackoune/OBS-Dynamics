"""
music_catalog.py — Les lecteurs de musique connus d'avance.

Sans ce catalogue, une source n'existerait que pendant qu'elle joue : fermer
Spotify ferait disparaître sa carte, donc son lien d'overlay, et la source
navigateur déjà configurée dans OBS pointerait vers une URL qui ne répond
plus. Les services listés ici sont donc TOUJOURS présents dans l'onglet, avec
une URL stable, qu'ils tournent ou non.

C'est aussi ce qui permet de préparer un overlay à l'avance : on colle le lien
dans OBS, on le place et on le dimensionne, et il s'allume tout seul le jour où
ce lecteur joue quelque chose.

Logos : les huit lecteurs livrés ont le leur dans `assets/music/<clé>.png`,
fabriqués par `tools/generer_logos_musique.py` à partir des tracés simple-icons.
Les marques appartiennent à leurs propriétaires et ne servent ici qu'à désigner
le lecteur concerné. Le monogramme reste le recours des sources HORS catalogue
— un navigateur, un lecteur non listé — qui n'ont aucun fichier à afficher.
"""
from __future__ import annotations

import re

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app_paths import ASSETS_DIR

#: Dossier où chercher un logo fourni par l'utilisateur, un PNG par clé.
LOGOS_DIR = ASSETS_DIR / "music"

#: Forme de TOUTE clé de lecteur : celles du catalogue (`apple_music`) comme
#: celles fabriquées par `_slug` pour une source inconnue (`chrome-exe`).
#:
#: C'est la seule barrière entre le paramètre `?source=` d'une URL d'overlay
#: et un chemin de fichier : la clé finit en `<dossier>/<clé>.png`. Sans
#: elle, `?source=C:/Users/.../photo` servait n'importe quel PNG du disque —
#: pathlib remplace le dossier de base dès qu'on lui joint un chemin absolu.
_KEY = re.compile(r"[a-z0-9_-]{1,40}")


def is_valid_key(key: object) -> bool:
    """La chaîne a-t-elle la forme d'une clé de lecteur, et rien de plus ?"""
    return isinstance(key, str) and _KEY.fullmatch(key) is not None


@dataclass(frozen=True)
class MusicApp:
    """Un lecteur : son identité d'affichage et ce qui le reconnaît."""

    key: str
    label: str
    color: str
    #: Fragments cherchés dans l'AppUserModelId une fois normalisé (minuscules,
    #: sans séparateurs). Un seul suffit à reconnaître le lecteur.
    patterns: tuple[str, ...] = ()
    #: Faux pour une source découverte à l'exécution. Elle n'a pas à survivre à
    #: la fermeture du lecteur : personne n'a préparé d'overlay pour elle.
    built_in: bool = True

    @property
    def monogram(self) -> str:
        """Initiales, deux lettres au plus : « Apple Music » donne « AM »."""
        mots = [mot for mot in re.split(r"[\s_-]+", self.label) if mot]
        if len(mots) >= 2:
            return (mots[0][:1] + mots[1][:1]).upper()
        return self.label[:2].upper() if self.label else "?"


#: Les lecteurs livrés par défaut, dans l'ordre d'affichage.
#:
#: Les couleurs sont celles de chaque marque, retenues pour rester lisibles sur
#: le fond sombre de l'application. Tidal fait exception : sa charte est noire,
#: invisible ici, donc son cyan d'accentuation est utilisé à la place.
BUILT_IN: tuple[MusicApp, ...] = (
    MusicApp("spotify", "Spotify", "#1DB954", ("spotify",)),
    MusicApp("apple_music", "Apple Music", "#FA243C",
             ("applemusic", "applemusicwin")),
    MusicApp("itunes", "iTunes", "#FB5BC5", ("itunes",)),
    MusicApp("deezer", "Deezer", "#A238FF", ("deezer",)),
    MusicApp("tidal", "Tidal", "#33E5FF", ("tidal",)),
    MusicApp("amazon_music", "Amazon Music", "#25D1DA",
             ("amazonmusic", "amznmusic")),
    MusicApp("soundcloud", "SoundCloud", "#FF5500", ("soundcloud",)),
    MusicApp("youtube_music", "YouTube Music", "#FF0033",
             ("youtubemusic", "ytmusic", "musicyoutube")),
)

#: Couleur d'une source inconnue : le violet de l'application, faute de marque.
UNKNOWN_COLOR = "#A855F7"

_BY_KEY = {app.key: app for app in BUILT_IN}


def _normalise(app_id: str) -> str:
    """Réduit un AppUserModelId à ses lettres et chiffres, en minuscules.

    SMTC rend `SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify` pour une
    application du Store et `chrome.exe` pour un exécutable : comparer les
    formes brutes obligerait à écrire un motif par variante.
    """
    return re.sub(r"[^a-z0-9]+", "", app_id.lower())


def _slug(text: str) -> str:
    """Clé d'URL lisible pour une source hors catalogue.

    La coupe à 40 caractères vient AVANT le retrait des tirets de bord :
    tronquer en dernier laisserait un tiret orphelin en fin de clé, donc dans
    l'URL de l'overlay.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower())[:40].strip("-")
    return slug or "source"


def friendly_label(app_id: str) -> str:
    """Nom lisible d'une source hors catalogue.

    SMTC rend deux formes : celle d'une application du Store, dont la partie
    utile suit le `!`, et un nom d'exécutable. Ni l'une ni l'autre n'est
    présentable telle quelle à côté d'un nom d'artiste.
    """
    nom = app_id.rsplit("!", 1)[-1] if "!" in app_id else app_id
    if nom.lower().endswith(".exe"):
        nom = nom[:-4]
    return nom.strip() or app_id


def identify(app_id: str) -> MusicApp:
    """Le lecteur correspondant à cet identifiant SMTC.

    Une source inconnue — un navigateur, un lecteur non listé — reçoit une
    entrée fabriquée à la volée, avec une clé stable tirée de son identifiant :
    son lien d'overlay ne change pas d'une exécution à l'autre, même si elle
    n'est pas livrée par défaut.
    """
    normalise = _normalise(app_id)
    for app in BUILT_IN:
        if any(motif in normalise for motif in app.patterns):
            return app
    return MusicApp(key=_slug(app_id), label=friendly_label(app_id),
                    color=UNKNOWN_COLOR, built_in=False)


def by_key(key: str) -> Optional[MusicApp]:
    """L'entrée livrée par défaut portant cette clé, ou None."""
    return _BY_KEY.get(key)


def logo_path(key: str) -> Optional[Path]:
    """Logo de ce lecteur, s'il en existe un.

    Les huit lecteurs du catalogue ont le leur dans `assets/music/<clé>.png`.
    Une source hors catalogue n'en a pas : l'appelant retombe alors sur le
    monogramme. Remplacer un fichier suffit à changer le logo affiché.
    """
    if not is_valid_key(key):
        return None
    chemin = LOGOS_DIR / f"{key}.png"
    try:
        return chemin if chemin.is_file() else None
    except OSError:
        return None
