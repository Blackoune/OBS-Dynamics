"""Contrôle du cadrage : aperçu numéroté, exclusions, persistance."""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

import screen_match as sm


def _screen(h=400, w=600, seed=0, busy=False):
    """Écran synthétique dont le contenu suit la taille : dessiner à des
    coordonnées fixes le ferait sortir du cadre aux petites résolutions."""
    img = np.full((h, w, 3), (40, 30, 60), dtype=np.uint8)
    if busy:                                   # décor texturé = pièges à fragments
        rng = np.random.default_rng(seed)
        y0, y1, x0, x1 = int(h * .15), int(h * .65), int(w * .10), int(w * .90)
        img[y0:y1, x0:x1] = cv2.GaussianBlur(
            rng.integers(0, 255, (y1 - y0, x1 - x0, 3), dtype=np.uint8), (0, 0), 3)
    scale = w / 600
    cv2.rectangle(img, (int(w * .05), int(h * .82)), (int(w * .42), int(h * .95)),
                  (220, 220, 230), -1)
    cv2.putText(img, "HP 100", (int(w * .075), int(h * .92)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9 * scale, (10, 10, 10), max(1, int(2 * scale)))
    cv2.putText(img, "12:34", (int(w * .78), int(h * .15)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1 * scale, (255, 255, 255), max(1, int(3 * scale)))
    return img


# -- Aperçu ------------------------------------------------------------- #

def test_preview_draws_one_box_per_patch():
    img = _screen()
    patches = sm.extract_patches(img)
    preview = sm.draw_preview(img, patches)
    assert preview.shape == img.shape
    assert not np.array_equal(preview, img), "aucun cadre dessiné"


def test_preview_does_not_touch_the_original():
    img = _screen()
    before = img.copy()
    sm.draw_preview(img, sm.extract_patches(img))
    assert np.array_equal(img, before)


def test_excluded_boxes_are_drawn_in_another_colour():
    img = _screen()
    patches = sm.extract_patches(img)
    assert len(patches) >= 2
    a = sm.draw_preview(img, patches)
    b = sm.draw_preview(img, patches, excluded=[1])
    assert not np.array_equal(a, b), "le cadre exclu est rendu à l'identique"


def test_relative_size_is_resolution_independent():
    small = sm.relative_size(sm.extract_patches(_screen(200, 300))[0])
    big = sm.relative_size(sm.extract_patches(_screen(400, 600))[0])
    assert small[0] == pytest.approx(big[0], abs=0.06)
    assert small[1] == pytest.approx(big[1], abs=0.06)


def test_patches_follow_the_shape_of_what_they_cover():
    """Les cases voisines sont fusionnées : une barre de vie large doit donner
    un rectangle large, pas quatre carrés qui la découpent."""
    img = _screen(600, 900)
    wide = [p for p in sm.extract_patches(img) if p.fy > 0.7]
    assert wide, "aucun fragment sur la barre du bas"
    rw, rh = sm.relative_size(wide[0])
    assert rw > rh, f"fragment {rw:.2f}x{rh:.2f} : ne suit pas la forme de la barre"


def test_touching_cells_become_a_single_patch():
    """La réponse à « deux cadres côte à côte, c'est un ou deux ? » : un."""
    img = np.full((400, 600, 3), (30, 25, 45), dtype=np.uint8)
    cv2.rectangle(img, (60, 300), (540, 370), (220, 220, 230), -1)
    cv2.putText(img, "UNE TRES LONGUE BARRE", (75, 350),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, (10, 10, 10), 2)
    bottom = [p for p in sm.extract_patches(img) if p.fy > 0.6]
    assert len(bottom) == 1, f"la barre a été découpée en {len(bottom)} fragments"
    rw, rh = sm.relative_size(bottom[0])
    # Les extrémités unies de la barre sont écartées (une zone plate
    # correspondrait à n'importe quelle autre zone plate) et la grille
    # quantifie la hauteur : le fragment couvre la partie texturée et reste
    # plus large que haut. Le point vérifié ici est la FUSION, pas le ratio.
    assert rw > rh, f"fragment {rw:.2f}x{rh:.2f} : pas la forme d'une barre"


# -- Exclusions --------------------------------------------------------- #

def test_excluding_a_patch_drops_the_whole_zone():
    """Sans exclusion, relancer le calcul redonnerait les mêmes fragments.
    Et c'est la BOÎTE qui est écartée, pas son centre : sinon refuser un
    fragment fusionné n'en retirerait qu'une case."""
    img = _screen(600, 900, busy=True)
    first = sm.extract_patches(img)
    assert first
    box = sm.patch_box(first[0])

    second = sm.extract_patches(img, exclude=[box])
    for patch in second:
        assert abs(patch.fx - box[0]) > box[2] / 2 or abs(patch.fy - box[1]) > box[3] / 2,             "un fragment est ressorti dans la zone refusée"


def test_exclusions_accumulate():
    img = _screen(600, 900, busy=True)
    excluded = []
    for _ in range(2):
        remaining = sm.extract_patches(img, exclude=excluded)
        if not remaining:
            break
        excluded.append(sm.patch_box(remaining[0]))
    final = sm.extract_patches(img, exclude=excluded)
    for patch in final:
        for bx, by, bw, bh in excluded:
            assert abs(patch.fx - bx) > bw / 2 or abs(patch.fy - by) > bh / 2


def test_excluding_everything_falls_back_instead_of_returning_nothing():
    """Un jeu sans zone stable ne doit pas se retrouver sans aucun fragment."""
    img = _screen()
    everywhere = [(x / 10, y / 10, 0.2, 0.2) for x in range(11) for y in range(11)]
    assert sm.extract_patches(img, exclude=everywhere)


# -- Verdict de séparation ---------------------------------------------- #

def test_two_distinct_captures_are_reported_as_separable():
    menu = np.full((400, 600, 3), (28, 20, 46), dtype=np.uint8)
    cv2.putText(menu, "MENU PRINCIPAL", (60, 150), cv2.FONT_HERSHEY_DUPLEX, 1.4, (240, 200, 90), 3)
    cv2.rectangle(menu, (120, 220), (480, 290), (70, 55, 110), -1)
    cv2.putText(menu, "JOUER", (200, 268), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    game = _screen(busy=True)

    sep = sm.cross_check(menu, sm.extract_patches(menu), game, sm.extract_patches(game))
    assert sep.ok, f"séparation jugée insuffisante : {sep}"
    assert sep.margin > 0.2


def test_two_identical_captures_are_reported_as_inseparable():
    """Le cas où aucun réglage de cadrage ne sauvera la détection."""
    img = _screen(busy=True)
    sep = sm.cross_check(img, sm.extract_patches(img), img, sm.extract_patches(img))
    assert not sep.ok
    assert sep.margin <= 0.0


# -- Persistance -------------------------------------------------------- #

def test_review_survives_a_save_and_reload(app_module, tmp_path):
    m = app_module
    store = m.GameStore(tmp_path / "games.json")
    reviews = {"C:/caps/menu.png": {"stamp": "123.0:456", "excluded": [[0.25, 0.75]]}}
    store.upsert(m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                        patch_reviews=reviews))
    assert m.GameStore(tmp_path / "games.json").load()[0].patch_reviews == reviews


def test_games_json_without_the_field_still_loads(app_module, tmp_path):
    """Compatibilité : les fichiers antérieurs n'ont pas cette clé."""
    m = app_module
    path = tmp_path / "games.json"
    path.write_text(json.dumps([{"id": "g1", "name": "J", "source": "manual",
                                 "active_match": "j.exe"}]), encoding="utf-8")
    assert m.GameStore(path).load()[0].patch_reviews == {}


def test_image_stamp_changes_when_the_file_changes(app_module, tmp_path):
    """C'est ce qui déclenche une nouvelle validation, et seulement ça."""
    m = app_module
    path = tmp_path / "ref.png"
    cv2.imwrite(str(path), _screen())
    first = m.image_stamp(str(path))
    assert first and m.image_stamp(str(path)) == first

    cv2.imwrite(str(path), _screen(busy=True))
    import os, time
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert m.image_stamp(str(path)) != first


def test_missing_file_has_an_empty_stamp(app_module, tmp_path):
    assert app_module.image_stamp(str(tmp_path / "absent.png")) == ""


def test_registry_feeds_the_exclusions_into_detection(app_module, tmp_path):
    """Le registre est global : c'est lui qui transmet les refus de
    l'utilisateur au cache de fragments utilisé pendant le scan."""
    m = app_module
    path = tmp_path / "ref.png"
    cv2.imwrite(str(path), _screen(busy=True))
    m._TEMPLATES.clear()
    m._REVIEWS.clear()

    template = m._TEMPLATES.get(str(path))
    before = m._PATCHES.get(str(path), template)
    box = sm.patch_box(before[0])

    m._REVIEWS.set(str(path), [box])
    after = m._PATCHES.get(str(path), template)
    for patch in after:
        assert abs(patch.fx - box[0]) > box[2] / 2 or abs(patch.fy - box[1]) > box[3] / 2
    m._REVIEWS.clear()
    m._TEMPLATES.clear()


def test_loading_games_populates_the_registry(app_module, tmp_path):
    m = app_module
    m._REVIEWS.clear()
    game = m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                  patch_reviews={"img.png": {"stamp": "1:2", "excluded": [[0.5, 0.5]]}})
    m._REVIEWS.load_from_games([game])
    assert m._REVIEWS.excluded_for("img.png") == [(0.5, 0.5)]
    m._REVIEWS.clear()


# -- Recadrage manuel aux curseurs -------------------------------------- #

def test_a_manual_zone_lands_exactly_where_asked():
    img = _screen(600, 900)
    patch = sm.patch_from_box(img, (0.80, 0.20, 0.12, 0.16))
    assert patch is not None
    assert patch.fx == pytest.approx(0.80, abs=0.02)
    assert patch.fy == pytest.approx(0.20, abs=0.02)
    rw, rh = sm.relative_size(patch)
    assert (rw, rh) == (pytest.approx(0.12, abs=0.02), pytest.approx(0.16, abs=0.02))


def test_a_zone_too_small_to_compare_is_refused():
    """Un fragment de quelques pixels correspondrait n'importe où."""
    assert sm.patch_from_box(_screen(600, 900), (0.5, 0.5, 0.001, 0.001)) is None


def test_a_zone_is_clipped_to_the_image():
    patch = sm.patch_from_box(_screen(600, 900), (0.98, 0.98, 0.40, 0.40))
    assert patch is not None
    assert patch.fx < 1.0 and patch.fy < 1.0


def test_manual_zones_come_first_and_are_never_replaced():
    img = _screen(600, 900, busy=True)
    box = (0.80, 0.20, 0.12, 0.16)
    patches = sm.build_patches(img, manual=[box])
    assert patches[0].fx == pytest.approx(box[0], abs=0.02)
    assert patches[0].fy == pytest.approx(box[1], abs=0.02)


def test_automatic_zones_fill_the_remaining_slots():
    img = _screen(600, 900, busy=True)
    auto_only = sm.build_patches(img)
    with_manual = sm.build_patches(img, manual=[(0.80, 0.20, 0.12, 0.16)])
    assert len(with_manual) >= len(auto_only), "les zones auto ont disparu"
    assert len(with_manual) <= sm.PATCH_COUNT


def test_the_automatic_pass_avoids_the_manual_zones():
    """Sinon la même région ressortirait deux fois."""
    img = _screen(600, 900, busy=True)
    box = (0.15, 0.88, 0.30, 0.20)
    patches = sm.build_patches(img, manual=[box])
    for patch in patches[1:]:
        assert abs(patch.fx - box[0]) > box[2] / 2 or abs(patch.fy - box[1]) > box[3] / 2


def test_no_manual_zone_leaves_the_automatic_behaviour_untouched():
    img = _screen(600, 900, busy=True)
    assert [(p.fx, p.fy) for p in sm.build_patches(img)] == \
           [(p.fx, p.fy) for p in sm.extract_patches(img)]


def test_manual_zones_are_drawn_differently():
    img = _screen(600, 900)
    patches = sm.build_patches(img, manual=[(0.80, 0.20, 0.12, 0.16)])
    plain = sm.draw_preview(img, patches)
    tinted = sm.draw_preview(img, patches, manual_count=1)
    assert not np.array_equal(plain, tinted), "zone manuelle rendue comme une auto"


def test_manual_zones_survive_save_and_reload(app_module, tmp_path):
    m = app_module
    store = m.GameStore(tmp_path / "games.json")
    reviews = {"ref.png": {"stamp": "1:2", "excluded": [], "manual": [[0.8, 0.2, 0.12, 0.16]]}}
    store.upsert(m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                        patch_reviews=reviews))
    reloaded = m.GameStore(tmp_path / "games.json").load()[0]
    assert reloaded.patch_reviews["ref.png"]["manual"] == [[0.8, 0.2, 0.12, 0.16]]


def test_the_registry_hands_manual_zones_to_the_scan(app_module, tmp_path):
    """Ce qui est réglé aux curseurs doit vraiment servir pendant la partie."""
    m = app_module
    path = tmp_path / "ref.png"
    cv2.imwrite(str(path), _screen(600, 900, busy=True))
    m._TEMPLATES.clear(); m._REVIEWS.clear()

    box = (0.80, 0.20, 0.12, 0.16)
    m._REVIEWS.set(str(path), [], [box])
    patches = m._PATCHES.get(str(path), m._TEMPLATES.get(str(path)))
    assert patches[0].fx == pytest.approx(box[0], abs=0.05)
    m._REVIEWS.clear(); m._TEMPLATES.clear()


def test_legacy_reviews_without_manual_key_still_load(app_module):
    """Les enregistrements écrits avant les curseurs n'ont pas cette clé."""
    m = app_module
    m._REVIEWS.clear()
    game = m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                  patch_reviews={"img.png": {"stamp": "1:2", "excluded": [[0.5, 0.5]]}})
    m._REVIEWS.load_from_games([game])
    assert m._REVIEWS.excluded_for("img.png") == [(0.5, 0.5)]
    assert m._REVIEWS.manual_for("img.png") == []
    m._REVIEWS.clear()


def test_the_preview_changes_visibly_when_a_zone_moves():
    """Régression : un liseré de 2 px ne modifiait que 1,3 % des pixels et le
    réglage aux curseurs semblait ne rien faire. Tout ce qui n'est pas retenu
    est désormais assombri, comme dans un outil de recadrage."""
    img = _screen(600, 900, busy=True)
    left = sm.draw_preview(img, sm.build_patches(img, manual=[(0.25, 0.5, 0.2, 0.2)]),
                           manual_count=1, highlight=1)
    right = sm.draw_preview(img, sm.build_patches(img, manual=[(0.75, 0.5, 0.2, 0.2)]),
                            manual_count=1, highlight=1)
    changed = (left != right).any(axis=2).mean()
    assert changed > 0.05, f"seulement {changed:.1%} de pixels changés : invisible"


def test_everything_outside_the_fragments_is_dimmed():
    img = _screen(600, 900, busy=True)
    patches = sm.build_patches(img)
    preview = sm.draw_preview(img, patches)
    # Un coin sans fragment doit être assombri, l'intérieur d'un fragment non.
    assert preview[5, 5].mean() < img[5, 5].mean(), "l'extérieur n'est pas assombri"
    p = patches[0]
    cy, cx = int(p.fy * img.shape[0]), int(p.fx * img.shape[1])
    assert np.array_equal(preview[cy, cx], img[cy, cx]), "l'intérieur a été assombri"


def test_dimming_can_be_turned_off():
    img = _screen(600, 900)
    plain = sm.draw_preview(img, sm.build_patches(img), dim_outside=False)
    assert plain[5, 5].mean() == pytest.approx(img[5, 5].mean(), abs=1)


def test_manual_colour_is_orange_in_bgr():
    """OpenCV attend du BGR : une couleur écrite en RGB sortirait cyan."""
    b, g, r = sm._MANUAL_COLOR
    assert r > g > b, f"({b},{g},{r}) n'est pas de l'orange en BGR"


# -- Menu déroulant des zones ------------------------------------------- #

ctk = pytest.importorskip("customtkinter")


@pytest.fixture
def dialog(app_module, tmp_path):
    """Vraie fenêtre de contrôle sur une capture de jeu et une de menu."""
    m = app_module
    try:
        root = ctk.CTk()
    except Exception as exc:
        pytest.skip(f"pas d'affichage disponible : {exc}")
    root.withdraw()

    game_png, menu_png = tmp_path / "game.png", tmp_path / "menu.png"
    cv2.imwrite(str(game_png), _screen(600, 900, busy=True))
    flat = np.full((600, 900, 3), (28, 20, 46), dtype=np.uint8)
    cv2.putText(flat, "MENU PRINCIPAL", (90, 250), cv2.FONT_HERSHEY_DUPLEX, 2.0, (240, 200, 90), 4)
    cv2.imwrite(str(menu_png), flat)

    saved = {}
    dlg = m.PatchReviewDialog(root, image_path=str(game_png), kind="ingame",
                              menu_images=[str(menu_png)], ingame_images=[str(game_png)],
                              excluded=[], manual=[], count=None,
                              on_validated=lambda e, mn, n: saved.update(
                                  excluded=e, manual=mn, count=n))
    dlg.withdraw()
    yield m, dlg, saved
    root.destroy()


def test_the_dropdown_lists_every_displayed_zone(dialog):
    """Automatiques comprises : on doit pouvoir en sélectionner une pour la
    repositionner, sans avoir à l'écarter puis en retracer une par-dessus."""
    _m, dlg, _ = dialog
    assert len(dlg._zone_labels()) == len(dlg._patches)
    assert all("1" in dlg._zone_labels()[0] for _ in [0])


def test_selecting_a_zone_loads_its_position_into_the_sliders(dialog):
    _m, dlg, _ = dialog
    dlg._on_zone_selected(dlg._zone_labels()[0])
    box = sm.patch_box(dlg._patches[0])
    for key, value in zip(("x", "y", "w", "h"), box):
        assert dlg._sliders[key].get() == pytest.approx(value, abs=0.02)


def test_repositioning_an_automatic_zone_pins_it_and_frees_its_old_spot(dialog):
    """Sans écarter l'emplacement d'origine, la recherche automatique
    remettrait la zone au même endroit et le déplacement serait annulé."""
    _m, dlg, _ = dialog
    dlg._on_zone_selected(dlg._zone_labels()[0])
    original = sm.patch_box(dlg._patches[0])

    dlg._sliders["x"].set(0.60)
    dlg._sliders["y"].set(0.35)
    dlg._apply_zone()

    assert len(dlg._manual) == 1
    assert dlg._patches[0].fx == pytest.approx(0.60, abs=0.05)
    assert dlg._patches[0].fy == pytest.approx(0.35, abs=0.05)
    assert original in dlg._excluded, "l'emplacement automatique d'origine n'a pas été écarté"


def test_the_plus_button_adds_a_zone_and_selects_it(dialog):
    _m, dlg, _ = dialog
    before = len(dlg._manual)
    dlg._add_zone()
    assert len(dlg._manual) == before + 1
    assert dlg._selected == before
    # Les curseurs pointent la zone qui vient d'être créée, où qu'elle soit
    # tombée : sa position est choisie libre, pas fixée au centre.
    created = dlg._manual[-1]
    for key, value in zip(("x", "y", "w", "h"), created):
        assert dlg._sliders[key].get() == pytest.approx(value, abs=0.02)


def test_deleting_a_manual_zone_removes_it(dialog):
    _m, dlg, _ = dialog
    dlg._add_zone()
    dlg._delete_zone()
    assert dlg._manual == []
    assert dlg._selected is None


def test_deleting_an_automatic_zone_excludes_it(dialog):
    _m, dlg, _ = dialog
    dlg._on_zone_selected(dlg._zone_labels()[0])
    box = sm.patch_box(dlg._patches[0])
    dlg._delete_zone()
    assert box in dlg._excluded


def test_applying_without_a_selection_says_so_instead_of_guessing(dialog):
    _m, dlg, _ = dialog
    dlg._selected = None
    before = list(dlg._manual)
    dlg._apply_zone()
    assert dlg._manual == before
    assert dlg._status_lbl.cget("text")


def test_sliders_do_nothing_when_no_zone_is_selected(dialog):
    """Les curseurs ne visent rien tant qu'aucune zone n'est choisie."""
    _m, dlg, _ = dialog
    dlg._selected = None
    before = list(dlg._manual)
    dlg._sliders["x"].set(0.9)
    dlg._on_slider("x")
    assert dlg._manual == before


def test_validating_hands_back_both_lists(dialog):
    _m, dlg, saved = dialog
    dlg._add_zone()
    dlg._validate()
    assert len(saved["manual"]) == 1
    assert "excluded" in saved


# -- Stabilité du nombre de zones --------------------------------------- #

def test_editing_a_zone_never_creates_a_new_one(dialog):
    """Régression : transformer une zone automatique en zone manuelle libérait
    un créneau que la recherche automatique remplissait aussitôt — une zone
    surgissait alors qu'on venait simplement d'en déplacer une."""
    _m, dlg, _ = dialog
    before = len(dlg._patches)
    for x in (0.30, 0.45, 0.60):
        dlg._on_zone_selected(dlg._zone_labels()[0])
        dlg._sliders["x"].set(x)
        dlg._apply_zone()
        assert len(dlg._patches) == before, \
            f"le nombre de zones est passé de {before} à {len(dlg._patches)}"
    assert len(dlg._manual) == 1, "chaque modification a créé une zone de plus"


def test_the_dropdown_only_lists_active_zones(dialog):
    _m, dlg, _ = dialog
    assert len(dlg._zone_menu.cget("values")) == len(dlg._patches)
    dlg._add_zone()
    assert len(dlg._zone_menu.cget("values")) == len(dlg._patches)


def test_adding_and_deleting_move_the_count_by_exactly_one(dialog):
    _m, dlg, _ = dialog
    start = len(dlg._patches)
    dlg._add_zone()
    assert len(dlg._patches) == start + 1
    dlg._delete_zone()
    assert len(dlg._patches) == start


def test_excluding_the_last_zone_does_not_summon_the_whole_frame():
    """Le repli plein cadre ne vaut que pour une image vierge : sinon écarter
    la dernière zone faisait ressurgir l'image entière en 1.0 x 1.0."""
    img = _screen(600, 900)
    patches = sm.extract_patches(img)
    assert patches
    leftover = sm.build_patches(img, exclude=[sm.patch_box(patches[0])])
    for patch in leftover:
        rw, rh = sm.relative_size(patch)
        assert not (rw > 0.95 and rh > 0.95), "l'image entière est revenue en fragment"


def test_a_blank_reference_still_falls_back_to_the_whole_frame():
    """Le repli reste utile là où il a un sens."""
    flat = np.full((400, 600, 3), (40, 30, 60), dtype=np.uint8)
    patches = sm.build_patches(flat)
    assert len(patches) == 1
    rw, rh = sm.relative_size(patches[0])
    assert rw > 0.95 and rh > 0.95


def test_the_validated_count_is_what_the_scan_uses(app_module, tmp_path):
    """L'aperçu ne vaut que s'il montre EXACTEMENT ce qui servira en jeu."""
    m = app_module
    path = tmp_path / "ref.png"
    cv2.imwrite(str(path), _screen(600, 900, busy=True))
    m._TEMPLATES.clear(); m._REVIEWS.clear()

    m._REVIEWS.set(str(path), [], [(0.5, 0.5, 0.2, 0.2)], 2)
    patches = m._PATCHES.get(str(path), m._TEMPLATES.get(str(path)))
    assert len(patches) == 2
    m._REVIEWS.clear(); m._TEMPLATES.clear()


def test_the_count_survives_save_and_reload(app_module, tmp_path):
    m = app_module
    store = m.GameStore(tmp_path / "games.json")
    store.upsert(m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                        patch_reviews={"r.png": {"stamp": "1:2", "excluded": [],
                                                 "manual": [], "count": 3}}))
    m._REVIEWS.clear()
    m._REVIEWS.load_from_games(m.GameStore(tmp_path / "games.json").load())
    assert m._REVIEWS.count_for("r.png") == 3
    m._REVIEWS.clear()


# -- Indépendance des zones --------------------------------------------- #

def test_new_zones_never_land_on_top_of_each_other(dialog):
    """Régression : chaque nouvelle zone naissait au centre, à la même taille.
    Deux rectangles empilés au pixel près n'en font qu'un à l'œil, et régler
    les curseurs donnait l'impression que les zones étaient liées."""
    _m, dlg, _ = dialog
    for _ in range(5):
        dlg._add_zone()
    boxes = dlg._manual
    assert len(boxes) == 5
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            assert not type(dlg)._overlaps(boxes[i], boxes[j]), \
                f"les zones {i + 1} et {j + 1} se superposent"


def test_adding_a_zone_leaves_the_others_untouched(dialog):
    _m, dlg, _ = dialog
    dlg._add_zone()
    dlg._sliders["x"].set(0.20)
    dlg._sliders["y"].set(0.20)
    dlg._sliders["w"].set(0.10)
    dlg._sliders["h"].set(0.10)
    dlg._apply_zone()
    first = dlg._manual[0]

    dlg._add_zone()
    assert dlg._manual[0] == first, "ajouter une zone a modifié la précédente"
    assert dlg._manual[1] != first, "la nouvelle zone a repris la position de l'autre"


def test_each_zone_keeps_its_own_size(dialog):
    _m, dlg, _ = dialog
    dlg._add_zone()
    dlg._sliders["w"].set(0.10); dlg._sliders["h"].set(0.10)
    dlg._apply_zone()
    dlg._add_zone()
    dlg._sliders["w"].set(0.45); dlg._sliders["h"].set(0.40)
    dlg._apply_zone()

    assert dlg._manual[0][2:] == pytest.approx((0.10, 0.10), abs=0.02)
    assert dlg._manual[1][2:] == pytest.approx((0.45, 0.40), abs=0.02)


def test_moving_one_slider_does_not_move_the_others(dialog):
    _m, dlg, _ = dialog
    dlg._add_zone()
    before = {k: dlg._sliders[k].get() for k in ("x", "y", "w", "h")}
    dlg._sliders["w"].set(0.40)
    dlg._on_slider("w")
    after = {k: dlg._sliders[k].get() for k in ("x", "y", "w", "h")}
    assert [k for k in before if before[k] != after[k]] == ["w"]


def test_editing_one_zone_does_not_touch_the_other(dialog):
    _m, dlg, _ = dialog
    dlg._add_zone()
    dlg._add_zone()
    untouched = dlg._manual[0]

    dlg._on_zone_selected(dlg._zone_labels()[1])
    dlg._sliders["x"].set(0.85)
    dlg._apply_zone()

    assert dlg._manual[0] == untouched
    assert dlg._manual[1][0] == pytest.approx(0.85, abs=0.02)


def test_the_free_spot_search_falls_back_without_stacking(dialog):
    """Quand toutes les places sont prises, on décale au lieu d'empiler."""
    _m, dlg, _ = dialog
    for _ in range(12):
        dlg._add_zone()
    assert len({tuple(round(v, 3) for v in b) for b in dlg._manual}) == 12
