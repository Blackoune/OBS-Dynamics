"""Images de l'assistant d'installation (Inno Setup), à relancer si le logo change.

    python tools/images_installateur.py

Écrit installer/wizard-*.bmp (panneau de gauche, pages Bienvenue et Fin) et
installer/petit-*.bmp (coin haut droit des autres pages), en 100 % et 200 %.
Couleurs du site : fond #0B0F17, surface #111724, ligne #222C3D, accent #22D3EE.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RACINE = Path(__file__).resolve().parent.parent
SORTIE = RACINE / "installer"
LOGO = Image.open(RACINE / "assets" / "logo.png").convert("RGBA")
POLICE = "C:/Windows/Fonts/segoeuib.ttf"

FOND = (11, 15, 23)
SURFACE = (17, 23, 36)
LIGNE = (34, 44, 61)
TEXTE = (230, 235, 242)


def panneau(echelle: int) -> Image.Image:
    """164 x 314 : carte arrondie logo + nom, puis une séparation blanche."""
    l, h = 164 * echelle, 314 * echelle
    s = lambda v: round(v * echelle)  # noqa: E731
    img = Image.new("RGB", (l, h), FOND)
    d = ImageDraw.Draw(img)

    carte = (s(16), s(64), l - s(16), s(204))
    d.rounded_rectangle(carte, radius=s(14), fill=SURFACE, outline=LIGNE, width=max(1, s(1)))
    taille = s(64)
    logo = LOGO.resize((taille, taille), Image.LANCZOS)
    img.paste(logo, ((l - taille) // 2, s(84)), logo)
    nom = ImageFont.truetype(POLICE, s(17))
    d.text((l // 2, s(170)), "OBS Dynamics", font=nom, fill=TEXTE, anchor="mm")

    # Aucun texte traduisible ici : l'image sert aux deux langues.
    d.line((s(28), s(226), l - s(28), s(226)), fill=(255, 255, 255), width=max(1, s(1)))
    return img


def petit(echelle: int) -> Image.Image:
    """55 x 55 : logo seul sur fond clair (bandeau blanc des pages internes)."""
    c = 55 * echelle
    img = Image.new("RGB", (c, c), (255, 255, 255))
    logo = LOGO.resize((c - 6 * echelle, c - 6 * echelle), Image.LANCZOS)
    img.paste(logo, (3 * echelle, 3 * echelle), logo)
    return img


if __name__ == "__main__":
    SORTIE.mkdir(exist_ok=True)
    for e, suffixe in ((1, "100"), (2, "200")):
        panneau(e).save(SORTIE / f"wizard-{suffixe}.bmp")
        petit(e).save(SORTIE / f"petit-{suffixe}.bmp")
    print("Images écrites dans", SORTIE)
