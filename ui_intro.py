"""Animation de lancement : le logo d'OBS tourne, devient l'éclair d'OBS
Dynamics, puis file se ranger en haut à gauche, à côté du nom.

Un voile opaque couvre la fenêtre le temps de l'animation (~2 s), puis se
retire. Tout est calculé d'après l'horloge : une machine lente saute des
images au lieu de ralentir. Un clic ou une touche passe l'animation.

Elle n'est jamais bloquante : image absente ou erreur de dessin, et
l'application s'ouvre directement.
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import Any, Optional

from PIL import Image, ImageTk

from app_paths import ASSETS_DIR, logger
from ui_common import COL_BG, ctk

#: Bornes des phases, en secondes depuis le début.
SPIN_END = 0.95       # le logo d'OBS tourne, de plus en plus vite
MORPH_END = 1.30      # il s'efface pendant que l'éclair apparaît
HOLD_END = 1.55       # l'éclair marque un temps, avec un léger rebond
DURATION = 2.05       # il rejoint sa place en haut à gauche

#: Côté du logo au centre, et une fois rangé (unités CustomTkinter).
BIG = 168
SMALL = 26

_FRAME_MS = 16
_TURNS = 2.25         # tours faits avant la transformation


def _ease_in(p: float) -> float:
    return p * p * p


def _ease_in_out(p: float) -> float:
    return 3 * p * p - 2 * p * p * p


def _faded(image: Image.Image, opacity: float) -> Image.Image:
    """La même image, alpha multiplié par `opacity`."""
    out = image.copy()
    out.putalpha(image.getchannel("A").point(lambda v: int(v * opacity)))
    return out


class Intro:
    """Le voile et son animation. `frame(t)` est pur : testable sans fenêtre."""

    def __init__(self, obs: Image.Image, bolt: Image.Image, size: tuple[int, int],
                 target: tuple[int, int], scale: float = 1.0) -> None:
        self._big = max(8, round(BIG * scale))
        self._small = max(4, round(SMALL * scale))
        # Sources au double de la taille affichée : la rotation puis la
        # réduction en Lanczos lissent les bords, là où tourner l'image à sa
        # taille finale les laissait en escalier.
        self._obs = obs.convert("RGBA").resize((self._big * 2,) * 2, Image.Resampling.LANCZOS)
        self._bolt = bolt.convert("RGBA")
        self._centre = (size[0] // 2, size[1] // 2)
        self._target = target

    def _spin(self, t: float) -> Image.Image:
        angle = -360 * _TURNS * _ease_in(min(1.0, t / MORPH_END))
        tourne = self._obs.rotate(angle, resample=Image.Resampling.BICUBIC)
        return tourne.resize((self._big,) * 2, Image.Resampling.LANCZOS)

    def _bolt_at(self, side: int) -> Image.Image:
        return self._bolt.resize((side, side), Image.Resampling.LANCZOS)

    def frame(self, t: float) -> tuple[Image.Image, tuple[int, int]]:
        """(image du logo, centre où la poser) à l'instant `t`."""
        if t < SPIN_END:
            apparition = min(1.0, t / 0.25)
            return _faded(self._spin(t), apparition), self._centre
        if t < MORPH_END:
            k = (t - SPIN_END) / (MORPH_END - SPIN_END)
            image = _faded(self._spin(t), 1 - k)
            side = round(self._big * (0.6 + 0.4 * k))
            eclair = _faded(self._bolt_at(side), k)
            image.alpha_composite(eclair, ((self._big - side) // 2,) * 2)
            return image, self._centre
        if t < HOLD_END:
            k = (t - MORPH_END) / (HOLD_END - MORPH_END)
            rebond = 1 + 0.08 * (1 - abs(2 * k - 1))
            return self._bolt_at(round(self._big * rebond)), self._centre
        k = _ease_in_out(min(1.0, (t - HOLD_END) / (DURATION - HOLD_END)))
        side = round(self._big + (self._small - self._big) * k)
        centre = tuple(round(a + (b - a) * k) for a, b in zip(self._centre, self._target))
        return self._bolt_at(side), centre       # type: ignore[return-value]


def play(app: Any, target_widget: Any) -> None:
    """Joue l'animation par-dessus `app`, puis découvre l'interface."""
    voile = tk.Canvas(app, bg=COL_BG, highlightthickness=0, bd=0)
    voile.place(x=0, y=0, relwidth=1, relheight=1)
    try:
        with Image.open(ASSETS_DIR / "intro_obs.png") as brut:
            obs = brut.convert("RGBA")
        with Image.open(ASSETS_DIR / "logo.png") as brut:
            bolt = brut.convert("RGBA")
        # Le voile d'abord, la mise en page ensuite : les tailles ne sont
        # connues qu'une fois la fenêtre affichée, et l'afficher sans voile
        # montrerait l'interface une fraction de seconde avant l'animation.
        app.update()
        taille = (app.winfo_width(), app.winfo_height())
        cible = (target_widget.winfo_rootx() - app.winfo_rootx() + target_widget.winfo_width() // 2,
                 target_widget.winfo_rooty() - app.winfo_rooty() + target_widget.winfo_height() // 2)
        intro = Intro(obs, bolt, taille, cible,
                      scale=ctk.ScalingTracker.get_window_scaling(app) or 1.0)
    except Exception:
        logger.debug("Animation de lancement ignorée.", exc_info=True)
        voile.destroy()
        return

    item = voile.create_image(*intro._centre)
    debut = time.monotonic()
    photo: list[Optional[ImageTk.PhotoImage]] = [None]   # garde l'image en vie

    def fin(_event: Any = None) -> None:
        if voile.winfo_exists():
            voile.destroy()

    def image_suivante() -> None:
        if not voile.winfo_exists():
            return
        t = time.monotonic() - debut
        if t >= DURATION:
            fin()
            return
        try:
            image, centre = intro.frame(t)
            # Posée sur le fond du voile : Tk compose mal l'alpha partiel.
            fond = Image.new("RGBA", image.size, COL_BG)
            fond.alpha_composite(image)
            photo[0] = ImageTk.PhotoImage(fond.convert("RGB"))
            voile.itemconfigure(item, image=photo[0])
            voile.coords(item, *centre)
        except Exception:
            logger.debug("Animation de lancement interrompue.", exc_info=True)
            fin()
            return
        voile.after(_FRAME_MS, image_suivante)

    voile.bind("<Button-1>", fin)
    app.bind("<Key>", lambda _e: fin(), add="+")
    image_suivante()
