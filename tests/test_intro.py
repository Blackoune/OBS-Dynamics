"""Animation de lancement : calcul des images, sans fenêtre."""
from __future__ import annotations

from PIL import Image

import ui_intro
from app_paths import ASSETS_DIR


def _intro(scale: float = 1.0) -> ui_intro.Intro:
    with Image.open(ASSETS_DIR / "intro_obs.png") as obs, Image.open(ASSETS_DIR / "logo.png") as bolt:
        return ui_intro.Intro(obs.copy(), bolt.copy(), (1180, 720), (40, 44), scale)


def test_the_logo_stays_centred_until_it_flies_to_its_place():
    intro = _intro()
    for t in (0.0, 0.5, ui_intro.SPIN_END, 1.1, ui_intro.MORPH_END, 1.5):
        image, centre = intro.frame(t)
        assert centre == (590, 360)
        assert image.mode == "RGBA" and image.width == image.height

    image, centre = intro.frame(ui_intro.DURATION)
    assert centre == (40, 44)                       # rangé à côté du nom
    assert image.size == (ui_intro.SMALL, ui_intro.SMALL)


def test_it_starts_invisible_and_follows_the_window_scaling():
    image, _ = _intro(scale=1.5).frame(0.0)
    assert image.size == (round(ui_intro.BIG * 1.5),) * 2
    assert image.getchannel("A").getextrema() == (0, 0)   # fondu depuis le noir
