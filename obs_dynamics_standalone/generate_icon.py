"""
Génère assets/icon.ico (multi-résolutions 16/32/48/256px) pour la barre de
titre et la barre des tâches Windows. À exécuter une seule fois (ou après
modification du design) avant la compilation PyInstaller.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS_DIR = Path(__file__).parent / "assets"
ASSETS_DIR.mkdir(exist_ok=True)
ICON_PATH = ASSETS_DIR / "icon.ico"

BG_DARK = (13, 17, 23, 255)
ACCENT = (0, 224, 255, 255)
ACCENT_DARK = (0, 160, 190, 255)


def _draw_master(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = size * 0.06
    radius = size * 0.22
    draw.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=BG_DARK)

    # Éclair stylisé (logo "OBS Dynamics"), coordonnées relatives à `size`
    bolt = [
        (size * 0.56, size * 0.16),
        (size * 0.34, size * 0.56),
        (size * 0.47, size * 0.56),
        (size * 0.42, size * 0.86),
        (size * 0.68, size * 0.42),
        (size * 0.53, size * 0.42),
    ]
    draw.polygon(bolt, fill=ACCENT, outline=ACCENT_DARK)

    return img


def generate() -> Path:
    master = _draw_master(256)
    sizes = [(256, 256), (48, 48), (32, 32), (16, 16)]
    master.save(ICON_PATH, format="ICO", sizes=sizes)
    return ICON_PATH


if __name__ == "__main__":
    path = generate()
    print(f"Icône générée : {path}")
