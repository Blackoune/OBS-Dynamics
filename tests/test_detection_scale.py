"""Détection visuelle robuste au changement de résolution.

Une image de référence capturée en 1080p doit continuer à être reconnue si le
jeu tourne ensuite en 1440p, en 4K ou en 720p. Avant, le score tombait entre
0,28 et 0,60 — très en dessous du seuil de 0,8 — et la bascule de scène ne se
déclenchait jamais.
"""
from __future__ import annotations

import cv2
import numpy as np
import pytest


def _scene(w: int, h: int) -> np.ndarray:
    """Écran de jeu synthétique : fond bruité + HUD proportionnel."""
    rng = np.random.default_rng(3)
    img = rng.integers(30, 70, (h, w, 3), dtype=np.uint8)
    cv2.rectangle(img, (int(w * .03), int(h * .88)), (int(w * .28), int(h * .96)), (200, 200, 210), -1)
    cv2.rectangle(img, (int(w * .04), int(h * .90)), (int(w * .20), int(h * .94)), (40, 160, 220), -1)
    cv2.putText(img, "HP 100", (int(w * .05), int(h * .935)), cv2.FONT_HERSHEY_SIMPLEX,
                w / 1600, (10, 10, 10), max(1, int(w / 900)))
    cv2.circle(img, (int(w * .92), int(h * .12)), int(h * .05), (230, 180, 40), -1)
    return img


@pytest.fixture
def hud_reference(app_module, tmp_path):
    """Découpe du HUD dans un écran 1080p, comme une capture faite par l'user."""
    m = app_module
    ref = _scene(1920, 1080)
    path = tmp_path / "hud.png"
    cv2.imwrite(str(path), ref[int(1080 * .86):int(1080 * .98), int(1920 * .02):int(1920 * .30)])
    m._TEMPLATES.clear()
    yield m, [str(path)], path
    m._TEMPLATES.clear()


@pytest.mark.parametrize("width,height", [
    (1920, 1080),   # même résolution que la référence
    (2560, 1440),
    (3840, 2160),
    (1600, 900),
    (1280, 720),
])
def test_reference_is_recognised_at_any_resolution(hud_reference, width, height):
    m, paths, _ = hud_reference
    m._TEMPLATES.clear()
    score = m._best_match_score(m._downscale(_scene(width, height)), paths)
    assert score >= 0.8, f"{width}x{height} : score {score:.3f}, sous le seuil"


def test_calibration_is_computed_once_then_reused(hud_reference):
    """Balayer les 11 échelles à chaque cycle mangerait l'intervalle de scan."""
    m, paths, path = hud_reference
    screen = m._downscale(_scene(2560, 1440))

    assert m._SCALES.scale_to_use(str(path), screen.shape[:2]) is None
    m._best_match_score(screen, paths)
    calibrated = m._SCALES.scale_to_use(str(path), screen.shape[:2])
    assert calibrated is not None and calibrated[1] is False   # confirmée
    assert calibrated[0] != 1.0, "l'échelle 1 ne peut pas convenir en 1440p"

    m._best_match_score(screen, paths)
    assert m._SCALES.scale_to_use(str(path), screen.shape[:2]) == calibrated


def test_changing_resolution_forces_a_recalibration(hud_reference):
    m, paths, path = hud_reference
    small = m._downscale(_scene(1280, 720))
    big = m._downscale(_scene(2560, 1440))

    m._best_match_score(small, paths)
    scale_small = m._SCALES.scale_to_use(str(path), small.shape[:2])[0]

    m._best_match_score(big, paths)
    scale_big = m._SCALES.scale_to_use(str(path), big.shape[:2])[0]

    assert scale_small != scale_big, "l'échelle n'a pas été recalculée"
    assert m._best_match_score(big, paths) >= 0.8


def test_replacing_the_image_file_drops_its_calibration(hud_reference, tmp_path):
    """Une échelle calibrée sur l'ancienne image ferait échouer la détection
    en silence après remplacement."""
    m, paths, path = hud_reference
    screen = m._downscale(_scene(2560, 1440))
    m._best_match_score(screen, paths)
    assert m._SCALES.scale_to_use(str(path), screen.shape[:2]) is not None

    other = _scene(1280, 720)
    cv2.imwrite(str(path), other[600:700, 40:400])
    import os, time
    os.utime(path, (time.time() + 2, time.time() + 2))   # force l'invalidation

    m._TEMPLATES.get(str(path))
    assert m._SCALES.scale_to_use(str(path), screen.shape[:2]) is None


def test_menu_and_ingame_scores_are_never_truncated(hud_reference):
    """detect_game_state arbitre menu vs en-jeu en comparant les deux scores :
    un score tronqué au franchissement du seuil fausserait l'arbitrage."""
    m, paths, _ = hud_reference
    screen = m._downscale(_scene(1920, 1080))
    assert m._best_match_score(screen, paths) == pytest.approx(1.0, abs=0.02)


def test_a_calibration_that_found_nothing_is_not_locked_in(hud_reference):
    """Régression majeure : la première calibration se faisait contre l'écran
    affiché à cet instant — typiquement le menu quand on calibre la référence
    « en jeu ». Une échelle absurde était mémorisée définitivement et la
    référence marquait ~0,09 même face à une copie conforme d'elle-même."""
    m, paths, path = hud_reference
    ailleurs = np.full((540, 960, 3), (30, 20, 50), dtype=np.uint8)   # rien à voir

    m._best_match_score(ailleurs, paths)
    assert not m._SCALES.is_confirmed(str(path)), \
        "une calibration ratée a été retenue comme définitive"

    bon_ecran = m._downscale(_scene(1920, 1080))
    assert m._best_match_score(bon_ecran, paths) >= 0.8, \
        "la référence reste aveugle après une calibration ratée"
    assert m._SCALES.is_confirmed(str(path))
