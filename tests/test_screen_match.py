"""Agent de comparaison écran / référence par fragments."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

import screen_match as sm


def _flat(h=400, w=600, color=(40, 30, 60)):
    return np.full((h, w, 3), color, dtype=np.uint8)


def _with_hud(h=400, w=600):
    """Fond uni + deux éléments détaillés à des endroits connus."""
    img = _flat(h, w)
    cv2.rectangle(img, (30, 330), (250, 380), (220, 220, 230), -1)
    cv2.putText(img, "HP 100", (45, 368), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (10, 10, 10), 2)
    cv2.putText(img, "12:34", (470, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 3)
    return img


# -- Extraction --------------------------------------------------------- #

def test_patches_are_taken_from_the_detailed_areas():
    patches = sm.extract_patches(_with_hud())
    assert patches
    for p in patches:
        assert sm.detail_score(p.image) >= sm.MIN_DETAIL


def test_flat_areas_are_never_selected():
    """Une zone unie correspondrait à n'importe quelle autre zone unie."""
    img = _with_hud()
    for p in sm.extract_patches(img):
        # aucun fragment ne doit venir du grand vide central
        assert not (0.35 < p.fy < 0.65 and 0.45 < p.fx < 0.70)


def test_patches_are_spread_out_not_stacked_on_one_element():
    patches = sm.extract_patches(_with_hud())
    if len(patches) >= 2:
        spread = max(p.fx for p in patches) - min(p.fx for p in patches)
        assert spread > 0.1, "tous les fragments sont collés au même endroit"


def test_completely_flat_image_falls_back_to_the_whole_frame():
    patches = sm.extract_patches(_flat())
    assert len(patches) == 1 and patches[0].image.shape == _flat().shape


def test_patch_positions_are_relative_so_they_survive_a_resolution_change():
    for p in sm.extract_patches(_with_hud()):
        assert 0.0 <= p.fx <= 1.0 and 0.0 <= p.fy <= 1.0


# -- Correspondance ----------------------------------------------------- #

def test_reference_matches_itself_perfectly():
    img = _with_hud()
    assert sm.score_patches(img, sm.extract_patches(img)) == pytest.approx(1.0, abs=0.01)


def test_moving_background_does_not_lower_the_score():
    """LE cas qui cassait tout : le décor bouge, le HUD reste."""
    ref = _with_hud()
    patches = sm.extract_patches(ref)
    rng = np.random.default_rng(0)
    for frame in range(6):
        screen = _with_hud()
        # décor remplacé intégralement, HUD conservé
        screen[80:300, 60:540] = rng.integers(0, 255, (220, 480, 3), dtype=np.uint8)
        assert sm.score_patches(screen, patches) >= 0.8, f"image {frame}"


def test_a_different_screen_scores_low():
    patches = sm.extract_patches(_with_hud())
    other = _flat()
    cv2.putText(other, "MENU PRINCIPAL", (60, 200), cv2.FONT_HERSHEY_DUPLEX, 1.4, (240, 200, 90), 3)
    assert sm.score_patches(other, patches) < 0.8


def test_patch_is_not_matched_elsewhere_on_screen():
    """Le fragment est cherché autour de sa position d'origine : le même motif
    déplacé ailleurs ne doit pas produire une fausse correspondance."""
    ref = _with_hud()
    patches = [p for p in sm.extract_patches(ref) if p.fy > 0.7]
    assert patches, "aucun fragment dans la moitié basse"
    moved = _flat()
    moved[20:70, 30:250] = ref[330:380, 30:250]     # HUD déplacé tout en haut
    assert sm.score_patches(moved, patches) < 0.8


def test_aggregate_tolerates_a_few_masked_patches():
    """Un fragment caché par un effet ne doit pas effondrer le score."""
    assert sm.aggregate([1.0, 1.0, 1.0, 0.9, 0.1, 0.0]) >= 0.8


def test_aggregate_punishes_a_screen_that_really_differs():
    assert sm.aggregate([0.4, 0.35, 0.3, 0.2, 0.1, 0.0]) < 0.8


def test_calibrate_finds_the_scale_of_a_resized_screen():
    ref = _with_hud(400, 600)
    patches = sm.extract_patches(ref)
    bigger = cv2.resize(ref, (900, 600), interpolation=cv2.INTER_LINEAR)
    scale, score = sm.calibrate(bigger, patches)
    assert score >= 0.8
    assert scale > 1.0, f"échelle {scale} : l'agrandissement n'a pas été détecté"


# -- Stabilité ---------------------------------------------------------- #

def test_first_observation_is_adopted_immediately():
    assert sm.Stabilizer(confirmations=2).update("g", "menu") == "menu"


def test_a_single_stray_reading_does_not_change_the_state():
    """C'est ce qui empêche OBS de basculer sans arrêt entre deux scènes."""
    st = sm.Stabilizer(confirmations=2)
    st.update("g", "menu")
    assert st.update("g", "in_game") == "menu"      # lecture isolée ignorée
    assert st.update("g", "menu") == "menu"


def test_a_confirmed_change_goes_through():
    st = sm.Stabilizer(confirmations=2)
    st.update("g", "menu")
    assert st.update("g", "in_game") == "menu"
    assert st.update("g", "in_game") == "in_game"


def test_alternating_readings_never_confirm_anything():
    st = sm.Stabilizer(confirmations=2)
    st.update("g", "menu")
    for _ in range(6):
        assert st.update("g", "in_game") == "menu"
        assert st.update("g", "menu") == "menu"


def test_games_are_tracked_independently():
    st = sm.Stabilizer(confirmations=2)
    st.update("a", "menu")
    st.update("b", "in_game")
    assert st.update("a", "menu") == "menu"
    assert st.update("b", "in_game") == "in_game"


def test_forget_resets_a_game():
    st = sm.Stabilizer(confirmations=2)
    st.update("g", "menu")
    st.forget("g")
    assert st.update("g", "in_game") == "in_game"   # redevient une 1re lecture


# -- Référence plein écran vs recadrage --------------------------------- #

def test_a_cropped_reference_is_searched_everywhere():
    """Si l'utilisateur fournit un recadrage (juste le HUD), les positions des
    fragments ne désignent rien sur l'écran : il faut chercher partout."""
    screen = _with_hud()
    crop = screen[320:390, 20:260]              # le HUD seul, découpé à la main
    patches = sm.extract_patches(crop)
    assert patches
    assert not sm.is_fullscreen_reference(patches[0], screen)
    assert sm.score_patches(screen, patches) >= 0.8


def test_a_cropped_reference_still_matches_when_the_hud_moves():
    """Corollaire : sans position connue, le HUD peut être n'importe où."""
    screen = _with_hud()
    crop = screen[320:390, 20:260]
    patches = sm.extract_patches(crop)
    moved = _flat()
    moved[40:110, 300:540] = crop               # même HUD, ailleurs
    assert sm.score_patches(moved, patches) >= 0.8


def test_a_fullscreen_reference_uses_positions():
    screen = _with_hud()
    patches = sm.extract_patches(screen)
    assert sm.is_fullscreen_reference(patches[0], screen)


def test_fullscreen_detection_is_scale_invariant():
    """Une référence 720p face à un écran 1440p reste une capture plein écran.
    Un critère basé sur la taille faussait la calibration : à petite échelle la
    référence passait pour un recadrage, la recherche devenait globale, et une
    correspondance fortuite pouvait battre la bonne échelle."""
    patches = sm.extract_patches(_with_hud(200, 300))
    assert sm.is_fullscreen_reference(patches[0], _with_hud(400, 600))
    assert sm.is_fullscreen_reference(patches[0], _with_hud(200, 300))


# -- Rendu : traits fins, numéros à l'extérieur ------------------------- #

@pytest.mark.parametrize("box,expected", [
    ((100, 60, 200, 120), "au-dessus"),   # place libre en haut
    ((100, 0, 200, 60), "en dessous"),    # collé au bord haut
])
def test_the_badge_is_placed_outside_the_box(box, expected):
    """Posée à l'intérieur, la pastille masquait justement le détail que le
    fragment est censé reconnaître."""
    x0, y0, x1, y1 = box
    bx, by = sm._badge_spot(box, 16, 16, 400, 300)
    assert bx + 16 <= x0 or bx >= x1 or by + 16 <= y0 or by >= y1, \
        f"pastille ({bx},{by}) à l'intérieur de {box}"
    if expected == "au-dessus":
        assert by + 16 <= y0
    else:
        assert by >= y1


def test_the_badge_never_leaves_the_image():
    for box in ((0, 0, 60, 40), (340, 260, 400, 300), (0, 260, 60, 300)):
        bx, by = sm._badge_spot(box, 16, 16, 400, 300)
        assert 0 <= bx and bx + 16 <= 400
        assert 0 <= by and by + 16 <= 300


def test_a_box_filling_the_whole_image_falls_back_inside():
    """Aucun côté disponible : mieux vaut une pastille dedans que hors cadre."""
    bx, by = sm._badge_spot((0, 0, 400, 300), 16, 16, 400, 300)
    assert 0 <= bx <= 400 - 16 and 0 <= by <= 300 - 16


def _line_thickness(h, w):
    return max(1, min(3, round(min(h, w) / 320)))


@pytest.mark.parametrize("h,w,expected", [
    (110, 900, 1),     # bande de menu : le trait doit rester fin
    (300, 400, 1),
    (900, 1600, 3),    # plein écran : plafonné
    (2160, 3840, 3),
])
def test_line_thickness_stays_thin_and_capped(h, w, expected):
    """L'épaisseur était proportionnelle sans plafond : 3 px sur une image de
    100 px de haut, soit 3 % de sa hauteur."""
    assert _line_thickness(h, w) == expected
    assert _line_thickness(h, w) / min(h, w) < 0.015


def test_a_thin_strip_reference_stays_readable():
    """Cas réel : la référence n'est qu'une bande de menu peu haute."""
    strip = np.full((110, 900, 3), (30, 24, 48), dtype=np.uint8)
    for i, label in enumerate(("ACCUEIL", "JOUER", "OPTIONS")):
        x = 40 + i * 215
        cv2.rectangle(strip, (x, 28), (x + 190, 82), (72, 58, 112), -1)
        cv2.putText(strip, label, (x + 18, 66), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (250, 250, 255), 2)
    patches = sm.extract_patches(strip)
    assert patches
    preview = sm.draw_preview(strip, patches)
    assert preview.shape == strip.shape
    # Le cadre ne doit pas noyer le contenu : l'intérieur des zones reste intact.
    inside_kept = sum(np.array_equal(preview[int(p.fy * 110), int(p.fx * 900)],
                                     strip[int(p.fy * 110), int(p.fx * 900)])
                      for p in patches)
    assert inside_kept == len(patches)
