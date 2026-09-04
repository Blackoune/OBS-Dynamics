"""Défilement de la grille de jeux — vérifié sur un vrai DashboardView.

Ces tests reproduisent la régression exacte qui rendait l'ascenseur géant et
immobile : un `bind("<Configure>")` nu sur le frame interne écrasait celui que
CTkScrollableFrame y installe pour recalculer la scrollregion du canvas.
Ils nécessitent un affichage ; ils sont sautés proprement s'il n'y en a pas.
"""
from __future__ import annotations

import json
import time

import pytest

ctk = pytest.importorskip("customtkinter")


@pytest.fixture
def dashboard(app_module, tmp_path):
    """Vrai DashboardView, 40 jeux, dans une vraie fenêtre Tk."""
    m = app_module
    try:
        root = ctk.CTk()
    except Exception as exc:  # pas de serveur graphique
        pytest.skip(f"pas d'affichage disponible : {exc}")

    root.geometry("1200x800")
    games_file = tmp_path / "games.json"
    games_file.write_text(json.dumps([
        {"id": f"g{i}", "name": f"Jeu {i}", "source": "manual", "active_match": "x.exe"}
        for i in range(40)
    ]), encoding="utf-8")

    view = m.DashboardView(
        root, store=m.GameStore(games_file), obs_loop=None,
        obs_client_getter=lambda: None, steam_scanner=None,
        post_ui=lambda fn: fn(), cover_service=None,
    )
    view.pack(fill="both", expand=True)
    root.update()
    root.update_idletasks()
    yield view, root
    root.destroy()


def _canvas(view):
    return view.scroll._parent_canvas


def test_scrollregion_grows_with_the_cards(dashboard):
    """La régression : la scrollregion restait figée sur la grille vide, donc
    le curseur d'ascenseur occupait toute la barre et ne bougeait pas."""
    view, root = dashboard
    region = _canvas(view).cget("scrollregion").split()
    assert region, "scrollregion vide : le <Configure> de CTk a été écrasé"
    content_height = int(float(region[3]))
    assert content_height > _canvas(view).winfo_height(), (
        f"contenu ({content_height}px) pas plus haut que la zone visible "
        f"({_canvas(view).winfo_height()}px) — rien ne défilerait"
    )


def test_scrollbar_thumb_is_proportional_and_moves(dashboard):
    view, root = dashboard
    canvas = _canvas(view)

    first, last = canvas.yview()
    assert (first, last) != (0.0, 1.0), "curseur pleine barre : rien à faire défiler"
    assert last - first < 0.9, f"curseur trop grand ({last - first:.2f} de la barre)"

    canvas.yview_scroll(view.WHEEL_PIXELS_PER_NOTCH, "units")  # positif = vers le bas
    root.update()
    moved_first, _ = canvas.yview()
    assert moved_first > first, "le curseur n'a pas bougé au défilement"


def test_one_wheel_notch_scrolls_the_configured_distance(dashboard):
    """Un cran = WHEEL_PIXELS_PER_NOTCH pixels, en UN seul déplacement.
    L'ancienne boucle faisait 3 px en 3 repaints : on n'avançait pas et les
    cartes se déchiraient."""
    view, root = dashboard
    canvas = _canvas(view)
    before = canvas.canvasy(0)

    view._wheel_handler(type("Evt", (), {"delta": -120})())  # un cran vers le bas
    root.update()

    assert canvas.canvasy(0) - before == pytest.approx(view.WHEEL_PIXELS_PER_NOTCH, abs=1)


def test_wheel_reaches_the_very_bottom_of_the_grid(dashboard):
    """Le défilement doit atteindre la dernière rangée, pas s'arrêter avant."""
    view, root = dashboard
    canvas = _canvas(view)
    for _ in range(200):
        view._wheel_handler(type("Evt", (), {"delta": -120})())
    root.update()
    assert canvas.yview()[1] == pytest.approx(1.0, abs=1e-3)


def _pump(root, view, timeout=3.0):
    """Fait tourner la boucle Tk jusqu'à ce que le recalcul débounce (120 ms)
    des colonnes soit passé."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.update()
        if view._resize_job is None:
            return
        time.sleep(0.02)
    raise AssertionError("le recalcul de colonnes ne s'est jamais terminé")


def test_resize_regrids_the_cards_without_rebuilding_them(dashboard):
    """Redimensionner la fenêtre doit DÉPLACER les cartes, pas les recréer.

    C'est le chemin qui gelait l'UI : chaque palier de largeur détruisait puis
    reconstruisait les 40 cartes (~80 ms/carte, ~3 s de blocage total), et ce
    gel au milieu d'un défilement était perçu comme du saccadement.
    """
    view, root = dashboard
    _pump(root, view)
    before_ids = {gid: id(card) for gid, card in view._cards.items()}
    before_cols = max(c.grid_info()["column"] for c in view._cards.values())

    root.geometry("700x800")          # nettement plus étroit -> moins de colonnes
    _pump(root, view)

    after_ids = {gid: id(card) for gid, card in view._cards.items()}
    after_cols = max(c.grid_info()["column"] for c in view._cards.values())

    assert after_ids == before_ids, "les cartes ont été reconstruites au lieu d'être déplacées"
    assert after_cols < before_cols, f"la grille n'a pas été resserrée ({before_cols} -> {after_cols})"


def test_regridding_keeps_every_game_exactly_once(dashboard):
    """Un re-grid ne doit ni perdre ni dupliquer une carte."""
    view, root = dashboard
    _pump(root, view)
    view._columns = 3
    view._layout_cards()
    # Pas de root.update() ici : il laisserait passer le recalcul débounce qui
    # rétablirait les colonnes déduites de la largeur réelle. grid_info() est
    # déjà à jour, le gestionnaire de géométrie est renseigné dès l'appel.
    slots = [(c.grid_info()["row"], c.grid_info()["column"]) for c in view._cards.values()]
    assert len(slots) == 40
    assert len(set(slots)) == 40, "deux cartes occupent la même case"
    assert {col for _, col in slots} == {0, 1, 2}


def test_overlay_is_built_only_on_first_hover(dashboard):
    """L'overlay de survol pesait la moitié du temps de render_games() alors
    qu'une seule carte à la fois l'affiche."""
    view, root = dashboard
    card = view._cards["g0"]
    assert card._overlay is None, "overlay construit d'avance"

    card._show_overlay()
    root.update()
    assert card._overlay is not None
    assert card._overlay_visible
    assert card._edit_btn.cget("text")

    built = id(card._overlay)
    card._hide_overlay()
    card._show_overlay()
    assert id(card._overlay) == built, "overlay reconstruit à chaque survol"


def test_overlay_does_not_swallow_the_wheel(dashboard):
    """Overlay ouvert, la molette doit continuer de faire défiler la grille.
    CTk pose les bindings sur le canvas interne du widget, pas sur le widget :
    on vérifie donc l'effet, pas la présence du binding."""
    view, root = dashboard
    canvas = _canvas(view)
    card = view._cards["g0"]
    card._show_overlay()
    root.update()

    before = canvas.canvasy(0)
    card._overlay._canvas.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    root.update()
    assert canvas.canvasy(0) > before, "l'overlay a avalé l'événement molette"


def test_hover_overlay_is_suppressed_while_scrolling(dashboard):
    """Les cartes glissent sous un curseur immobile : sans ce garde-fou les
    overlays clignotaient en rafale au milieu du défilement."""
    view, root = dashboard
    card = view._cards["g0"]

    view._wheel_handler(type("Evt", (), {"delta": -120})())
    card._show_overlay()
    assert not card._overlay_visible, "overlay ouvert pendant le défilement"

    type(card)._hover_blocked_until = 0.0      # défilement terminé
    card._show_overlay()
    root.update()
    assert card._overlay_visible, "overlay bloqué après la fin du défilement"


def test_adding_one_game_reuses_the_other_cards(dashboard, tmp_path):
    """Ajouter un jeu ne doit pas reconstruire les 40 autres cartes."""
    view, root = dashboard
    _pump(root, view)
    before = {gid: id(card) for gid, card in view._cards.items()}

    m = __import__("obs_dynamics")
    view._store.upsert(m.Game(id="nouveau", name="Nouveau jeu",
                              source="manual", active_match="n.exe"))
    t0 = time.perf_counter()
    view.render_games()
    root.update()
    elapsed = time.perf_counter() - t0

    assert set(view._cards) == set(before) | {"nouveau"}
    assert all(id(view._cards[gid]) == oid for gid, oid in before.items()), \
        "des cartes intactes ont été reconstruites"
    assert elapsed < 1.0, f"ajout d'un jeu trop lent : {elapsed*1000:.0f} ms"


def test_editing_a_game_rebuilds_only_that_card(dashboard):
    """Une carte dont les données changent doit être refaite (libellés et
    jaquette périmés), les autres non."""
    view, root = dashboard
    _pump(root, view)
    before = {gid: id(card) for gid, card in view._cards.items()}

    m = __import__("obs_dynamics")
    edited = m.Game(id="g0", name="Jeu renommé", source="steam",
                    active_match="x.exe", appid="620")
    view._store.upsert(edited)
    view.render_games()
    root.update()

    assert id(view._cards["g0"]) != before["g0"], "carte éditée non reconstruite"
    assert view._cards["g0"].game.name == "Jeu renommé"
    others = {gid: oid for gid, oid in before.items() if gid != "g0"}
    assert all(id(view._cards[gid]) == oid for gid, oid in others.items()), \
        "les autres cartes ont été reconstruites inutilement"


def test_empty_state_appears_and_disappears(dashboard):
    """Le message « aucun jeu » ne doit ni manquer ni rester coincé."""
    view, root = dashboard
    _pump(root, view)
    assert view._empty_lbl is None

    for gid in list(view._cards):
        view._store.delete(gid)
    view.render_games(); root.update()
    assert view._empty_lbl is not None and not view._cards

    m = __import__("obs_dynamics")
    view._store.upsert(m.Game(id="revenu", name="Revenu", source="manual", active_match="r.exe"))
    view.render_games(); root.update()
    assert view._empty_lbl is None and set(view._cards) == {"revenu"}


def test_cover_fills_the_card_edge_to_edge(dashboard):
    """Régression « deux bandes sur les côtés ».

    CTkLabel applique `padx=min(corner_radius, hauteur/2)` autour de son
    contenu. Le corner_radius=12 du label de jaquette laissait 12 px morts à
    gauche et à droite ET amputait l'image d'autant.
    """
    view, root = dashboard
    label = view._cards["g0"]._cover_lbl
    assert label.cget("corner_radius") == 0
    assert label._label.grid_info().get("padx") == 0, \
        "le label de jaquette réserve une marge : bandes visibles sur les côtés"


def test_status_badge_uses_the_two_requested_colours(dashboard):
    view, root = dashboard
    card = view._cards["g0"]

    card.set_state("inactive")
    assert card._badge.cget("fg_color") == "#D93025"
    for state in ("active", "menu", "in_game"):
        card.set_state(state)
        assert card._badge.cget("fg_color") == "#508267", state


def test_badge_text_and_border_stay_white_in_every_state(dashboard):
    view, root = dashboard
    card = view._cards["g0"]
    for state in ("inactive", "active", "menu", "in_game"):
        card.set_state(state)
        assert card._badge.cget("border_color") == "#FFFFFF"
        assert card._badge_lbl.cget("text_color") == "#FFFFFF"


def test_badge_has_no_rounded_corners(dashboard):
    """CTk peint le reste du canvas d'un coin arrondi avec la couleur du
    PARENT, pas celle de la jaquette posée dessous : quatre encoches sombres
    apparaissaient aux angles, par-dessus l'artwork."""
    view, root = dashboard
    card = view._cards["g0"]
    assert card._badge.cget("corner_radius") == 0
    assert card._badge_lbl.cget("corner_radius") == 0


def test_the_badge_only_says_active_or_inactive(dashboard, app_module):
    """Les états fins (Menu, En jeu) pilotent la bascule de scène mais
    n'apportent rien sur la carte : on veut y lire si le jeu tourne, point."""
    view, root = dashboard
    card = view._cards["g0"]

    card.set_state("inactive")
    assert card._badge_lbl.cget("text") == f"● {app_module.state_label('inactive')}"
    for state in ("active", "menu", "in_game"):
        card.set_state(state)
        assert card._badge_lbl.cget("text") == f"● {app_module.state_label('active')}", state


def test_state_label_keeps_the_four_states_for_the_rest_of_the_app(app_module):
    """La pastille n'en montre que deux, mais les journaux et la logique de
    bascule ont toujours besoin des quatre."""
    labels = {app_module.state_label(s)
              for s in ("inactive", "active", "menu", "in_game")}
    assert len(labels) == 4


def test_the_cover_is_pre_resized_so_ctk_never_softens_it(dashboard, app_module):
    """CTkImage redimensionne avec le rééchantillonnage par défaut de Pillow,
    bicubique, qui adoucit en réduction : 25 % de netteté perdue, mesuré. On
    fournit donc l'image déjà à la taille finale, en LANCZOS."""
    from PIL import Image
    view, root = dashboard
    card = view._cards["g0"]
    source = Image.new("RGB", (300, 450), (120, 40, 90))
    card._on_cover_received(source)
    root.update()

    assert card._ctk_image is not None
    scaling = ctk.ScalingTracker.get_widget_scaling(card)
    expected = (round(card.CARD_WIDTH * scaling), round(card.CARD_HEIGHT * scaling))
    assert card._ctk_image._light_image.size == expected, \
        "l'image n'est pas à la taille d'affichage : CTk la redimensionnera"


def test_lanczos_keeps_more_detail_than_the_default_resize():
    """Le chiffre qui justifie la correction."""
    import cv2
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(0)
    src = Image.fromarray(rng.integers(0, 255, (450, 300, 3), dtype=np.uint8))

    def sharpness(pil):
        grey = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2GRAY)
        return float(cv2.Laplacian(grey, cv2.CV_64F).var())

    assert sharpness(src.resize((190, 285), Image.Resampling.LANCZOS)) > \
           sharpness(src.resize((190, 285))) * 1.05


def test_a_card_stays_lean_in_tk_windows(dashboard):
    """Chaque fenêtre Tk est déplacée et repeinte à chaque cran de défilement :
    moins il y en a, moins on voit de repeints partiels."""
    view, root = dashboard

    def count(widget):
        return 1 + sum(count(child) for child in widget.winfo_children())

    assert count(view._cards["g0"]) <= 12


def test_dragging_the_scrollbar_redraws_once_per_frame(dashboard):
    """Faire glisser le curseur envoyait une commande à chaque pixel de souris
    — des dizaines de repeints complets par seconde, d'où des cartes qui se
    déchirent pendant le glissement."""
    view, root = dashboard
    canvas = _canvas(view)
    moves = []
    real = canvas.yview
    canvas.yview = lambda *a: (moves.append(a), real(*a))[1]

    drag = view.scroll._scrollbar.cget("command")
    for i in range(30):
        drag("moveto", str(i / 60))
    assert moves == [], "chaque événement de glissement déplaçait la grille"

    deadline = time.monotonic() + 1.0
    while not moves and time.monotonic() < deadline:
        root.update()
        time.sleep(0.005)
    assert len(moves) == 1, f"{len(moves)} déplacements pour 30 événements"
    assert moves[0] == ("moveto", str(29 / 60)), "la dernière position n'a pas été appliquée"


def test_the_overlay_closes_when_the_cover_arrives_and_the_mouse_left(dashboard):
    """Poser la jaquette repasse le label devant l'overlay et peut avaler le
    <Leave> : « Modifier / Supprimer » restait affiché après l'actualisation."""
    from PIL import Image
    view, root = dashboard
    card = view._cards["g0"]
    type(card)._hover_blocked_until = 0.0
    card._show_overlay()
    root.update()
    assert card._overlay_visible

    card._on_cover_received(Image.new("RGB", (300, 450), (100, 40, 90)))
    root.update()
    assert not card._overlay_visible, "l'overlay est resté collé après l'actualisation"


def test_the_overlay_stays_and_comes_back_on_top_if_the_mouse_is_still_there(dashboard):
    from PIL import Image
    view, root = dashboard
    card = view._cards["g0"]
    type(card)._hover_blocked_until = 0.0
    card._show_overlay()
    root.update()

    card._is_descendant = lambda widget: True      # curseur toujours sur la carte
    card.winfo_containing = lambda x, y: card
    card._on_cover_received(Image.new("RGB", (300, 450), (40, 90, 100)))
    root.update()

    assert card._overlay_visible
    assert card._edit_btn.winfo_ismapped(), "l'overlay est passé sous la jaquette"
