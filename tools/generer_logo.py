"""Fabrique le logo de l'application et son icône, bords lissés.

    python tools/generer_logo.py

Écrit `assets/logo.png` (256 px) et `assets/icon.ico` (16, 32, 48, 256 px).
Relancer ensuite `tools/images_installateur.py`, qui reprend le logo.

L'ancien logo était un bitmap à deux niveaux de transparence : coins arrondis
et éclair en escalier, visibles dès qu'on le réduisait. Il est ici retracé
d'après ses mesures (carré arrondi, éclair à six sommets relevés sur l'ancien
fichier), à SS fois la taille, puis réduit en Lanczos.
"""
from pathlib import Path

from PIL import Image, ImageDraw

RACINE = Path(__file__).resolve().parent.parent
COTE = 256
SS = 8

FOND = (13, 17, 23, 255)
ECLAIR = (0, 224, 255, 255)
CARRE = (15, 15, 242, 242)          # bord haut-gauche inclus, bas-droit exclu
RAYON = 49
#: Sommets de l'éclair, en pixels du logo de 256 px (centres de pixels).
SOMMETS = [(143, 40), (87, 143), (120, 144), (107, 220), (174, 107), (135, 106)]


def logo() -> Image.Image:
    grand = Image.new("RGBA", (COTE * SS, COTE * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(grand)
    x0, y0, x1, y1 = (v * SS for v in CARRE)
    d.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=RAYON * SS, fill=FOND)
    d.polygon([((x + 0.5) * SS, (y + 0.5) * SS) for x, y in SOMMETS], fill=ECLAIR)
    return grand.resize((COTE, COTE), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    image = logo()
    image.save(RACINE / "assets" / "logo.png")
    image.save(RACINE / "assets" / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
    print("Logo et icône écrits dans", RACINE / "assets")
