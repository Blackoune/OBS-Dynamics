"""Apparence du widget de sous-titres : champs, bornes, préréglages.

Le style voyage en JSON jusqu'à la page OBS, qui le pose en variables CSS.
Toute valeur passe par `from_dict` avant d'être enregistrée ou envoyée :
couleur `#RRGGBB`, nombre borné, choix dans une liste fermée, nom de police
réduit à des lettres, chiffres, espaces et tirets. Rien de ce que contient le
fichier ne peut donc s'injecter dans la page.

Les polices sont celles INSTALLÉES sous Windows : la page n'a pas le droit de
charger quoi que ce soit d'extérieur (CSP `default-src 'none'`), et la source
navigateur d'OBS voit les mêmes polices que le système.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

ALIGNS = ("left", "center", "right")
BACKGROUNDS = ("none", "box", "band")
ANIMATIONS = ("none", "fade", "word")
WEIGHTS = (400, 600, 700, 800, 900)

_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FONT = re.compile(r"^[\w][\w \-]{0,63}$")

#: Bornes des champs numériques : (minimum, maximum).
RANGES: dict[str, tuple[float, float]] = {
    "font_size": (16, 120),
    "letter_spacing": (0, 10),
    "outline_width": (0, 10),
    "shadow_blur": (0, 30),
    "shadow_offset": (0, 10),
    "bg_opacity": (0, 100),
    "bg_radius": (0, 40),
    "bg_padding": (0, 40),
    "lines": (1, 4),
    "max_width": (30, 100),
    "hold_s": (1, 30),
}


@dataclass(frozen=True)
class Style:
    preset: str = "tv"
    # Texte
    font_family: str = "Segoe UI"
    font_size: int = 42
    font_weight: int = 700
    italic: bool = False
    uppercase: bool = False
    letter_spacing: int = 0
    align: str = "center"
    # Couleurs
    text_color: str = "#FFFFFF"
    partial_color: str = "#FFFFFF"
    highlight_on: bool = False
    highlight_color: str = "#FFD60A"
    # Lisibilité
    outline_width: int = 0
    outline_color: str = "#000000"
    shadow_blur: int = 6
    shadow_offset: int = 2
    shadow_color: str = "#000000"
    # Fond
    bg_mode: str = "box"
    bg_color: str = "#000000"
    bg_opacity: int = 75
    bg_radius: int = 6
    bg_padding: int = 10
    # Mise en page
    lines: int = 2
    max_width: int = 80
    hold_s: int = 5
    animation: str = "fade"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Préréglages : un point de départ, que l'utilisateur ajuste ensuite.
PRESETS: dict[str, Style] = {
    "tv": Style(),
    "outline": Style(preset="outline", font_size=48, font_weight=900,
                     outline_width=5, shadow_blur=0, shadow_offset=3,
                     bg_mode="none"),
    "band": Style(preset="band", font_size=38, font_weight=600, bg_mode="band",
                  bg_opacity=85, bg_radius=0, bg_padding=14, max_width=100),
    "karaoke": Style(preset="karaoke", font_size=46, font_weight=800,
                     partial_color="#D6D6D6", highlight_on=True,
                     outline_width=3, bg_mode="none", animation="word"),
    "minimal": Style(preset="minimal", font_size=36, font_weight=600,
                     shadow_blur=10, shadow_offset=0, bg_mode="none", lines=1),
}
CUSTOM = "custom"


def from_dict(data: Any, base: Style = Style()) -> Style:
    """Style valide à partir de n'importe quoi : chaque champ absent ou
    invalide reprend sa valeur dans `base`, sans faire échouer les autres."""
    if not isinstance(data, dict):
        return base
    clean: dict[str, Any] = {}
    for f in fields(Style):
        value = data.get(f.name)
        default = getattr(base, f.name)
        if f.name == "preset":
            # isinstance d'abord : une liste lèverait TypeError sur `in PRESETS`.
            ok = isinstance(value, str) and (value in PRESETS or value == CUSTOM)
        elif isinstance(default, bool):
            ok = isinstance(value, bool)
        elif f.name == "font_weight":
            ok = value in WEIGHTS and not isinstance(value, bool)
        elif isinstance(default, int):
            lo, hi = RANGES[f.name]
            ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and lo <= value <= hi)
            if ok:
                value = int(round(value))
        elif f.name.endswith("_color"):
            ok = isinstance(value, str) and bool(_COLOR.fullmatch(value))
        elif f.name == "font_family":
            ok = isinstance(value, str) and bool(_FONT.fullmatch(value))
        elif f.name == "align":
            ok = value in ALIGNS
        elif f.name == "bg_mode":
            ok = value in BACKGROUNDS
        elif f.name == "animation":
            ok = value in ANIMATIONS
        else:
            ok = False
        clean[f.name] = value if ok else default
    return Style(**clean)


def edit(style: Style, **changes: Any) -> Style:
    """Applique des changements venus de l'interface, validés, et passe le
    style en « personnalisé » dès qu'on s'écarte du préréglage."""
    merged = from_dict(style.to_dict() | changes, base=style)
    if "preset" not in changes and merged != style:
        merged = replace(merged, preset=CUSTOM)
    return merged
