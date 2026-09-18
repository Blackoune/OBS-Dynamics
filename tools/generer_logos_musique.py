"""Fabrique les logos des lecteurs livrés dans `assets/music/`.

À lancer à la main, pas au démarrage : les PNG produits sont versionnés, et
l'application ne dépend d'aucune des bibliothèques utilisées ici.

    pip install simpleicons svglib rlPyCairo
    python tools/generer_logos_musique.py

Les tracés viennent de simple-icons (CC0) ; les marques, elles, appartiennent
à leurs propriétaires et ne servent ici qu'à désigner le lecteur concerné.

Deux traitements, selon la forme du tracé :

- `pastille` — un disque à la couleur de la marque, le glyphe en blanc. C'est
  le cas des tracés qui ne dessinent QUE le symbole (les barres de Deezer, le
  nuage de SoundCloud).
- `badge` — un disque blanc, le glyphe à la couleur de la marque. C'est le cas
  des tracés qui portent déjà leur propre pastille et y découpent le symbole :
  peindre le glyphe en blanc sur un disque coloré rendrait alors l'inverse du
  vrai logo, un disque blanc à ondes vertes pour Spotify.
"""
from __future__ import annotations

import io
import sys

from pathlib import Path

from PIL import Image, ImageDraw
from reportlab.graphics import renderPM
from simpleicons.all import icons
from svglib.svglib import svg2rlg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from music_catalog import BUILT_IN, LOGOS_DIR          # noqa: E402

#: Résolution des fichiers produits. L'application les réduit ; partir large
#: laisse de quoi rester net sur un écran à forte densité.
COTE = 256

#: Tracé simple-icons, traitement, et part du disque occupée par le glyphe.
#:
#: Un badge occupe tout le disque : son tracé EST la pastille. Une pastille,
#: elle, laisse une marge autour du symbole.
#:
#: Amazon Music n'a pas de tracé dédié dans simple-icons : le sourire d'Amazon
#: sur le cyan d'Amazon Music reste le repère le plus juste disponible.
LOGOS = {
    "spotify": ("spotify", "badge", 1.0),
    "apple_music": ("applemusic", "badge", 1.0),
    "youtube_music": ("youtubemusic", "badge", 1.0),
    "itunes": ("itunes", "pastille", 0.74),
    "deezer": ("deezer", "pastille", 0.56),
    "tidal": ("tidal", "pastille", 0.58),
    "amazon_music": ("amazon", "pastille", 0.62),
    "soundcloud": ("soundcloud", "pastille", 0.66),
}


def _svg(slug: str, couleur: str, fond: str, plein: float) -> str:
    """SVG complet : le disque, puis le glyphe centré dessus."""
    glyphe = icons[slug].path
    marge = (24 - 24 * plein) / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="{COTE}" height="{COTE}">'
        f'<circle cx="12" cy="12" r="12" fill="{fond}"/>'
        f'<g transform="translate({marge:.3f},{marge:.3f}) '
        f'scale({plein:.4f})">'
        f'<path d="{glyphe}" fill="{couleur}"/></g></svg>')


def _rasteriser(svg: str) -> Image.Image:
    """PNG RGBA du SVG, coins du carré rendus transparents.

    renderPM ne sait pas produire de fond transparent : on dessine donc le
    disque sur un fond neutre, puis on rétablit l'alpha avec un masque rond.
    """
    dessin = svg2rlg(io.BytesIO(svg.encode("utf-8")))
    plate = renderPM.drawToPIL(dessin, bg=0x000000).convert("RGBA")

    masque = Image.new("L", plate.size, 0)
    ImageDraw.Draw(masque).ellipse([0, 0, plate.size[0] - 1,
                                    plate.size[1] - 1], fill=255)
    plate.putalpha(masque)
    return plate


def main() -> int:
    LOGOS_DIR.mkdir(parents=True, exist_ok=True)
    for app in BUILT_IN:
        slug, traitement, plein = LOGOS[app.key]
        if traitement == "badge":
            svg = _svg(slug, app.color, "#FFFFFF", plein)
        else:
            svg = _svg(slug, "#FFFFFF", app.color, plein)
        chemin = LOGOS_DIR / f"{app.key}.png"
        _rasteriser(svg).save(chemin)
        print(f"{chemin.name:22} {slug:14} {traitement}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
