"""Fabrique assets/intro_obs.png : le logo d'OBS de l'animation de lancement.

    pip install simpleicons svglib rlPyCairo
    python tools/generer_intro.py

Le tracé vient de Simple Icons (CC0), comme les logos des lecteurs. Le PNG
produit est versionné : rien de ceci n'est requis pour lancer l'application.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps
from reportlab.graphics import renderPM
from simpleicons.all import icons
from svglib.svglib import svg2rlg

COTE = 512
SS = 4          # rendu 4 fois plus grand, puis réduit : bords lissés
SORTIE = Path(__file__).resolve().parent.parent / "assets" / "intro_obs.png"


def main() -> int:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'width="{COTE * SS}" height="{COTE * SS}">'
           f'<path d="{icons["obsstudio"].path}" fill="#000000"/></svg>')
    # renderPM ne sait pas rendre un fond transparent : glyphe noir sur blanc,
    # dont la luminance inversée devient l'alpha d'une image toute blanche.
    plate = renderPM.drawToPIL(svg2rlg(io.BytesIO(svg.encode("utf-8"))), bg=0xFFFFFF)
    alpha = ImageOps.invert(plate.convert("L")).resize((COTE, COTE), Image.Resampling.LANCZOS)
    logo = Image.new("RGBA", (COTE, COTE), (255, 255, 255, 0))
    logo.putalpha(alpha)
    logo.save(SORTIE)
    print(SORTIE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
