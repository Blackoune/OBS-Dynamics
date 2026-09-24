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

from ui_common import WHEEL_PIXELS_PER_NOTCH   # noqa: E402  (après importorskip)
from ui_dashboard import _rgb    # noqa: E402


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
    # Le recalcul des colonnes est différé de 120 ms. Sur un runner lent, il
    # tombait APRÈS les crans de molette d'un test : la grille grandissait
    # sous le curseur, arrêté à 0,8 au lieu de 1,0 (échec intermittent en CI).
    _pump(root, view)
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

    canvas.yview_scroll(WHEEL_PIXELS_PER_NOTCH, "units")  # positif = vers le bas
    root.update()
    moved_first, _ = canvas.yview()
    assert moved_first > first, "le curseur n'a pas bougé au défilement"


def _settle(root, scroller, timeout=3.0):
    """Laisse l'animation de défilement arriver à destination."""
    deadline = time.monotonic() + timeout
    while scroller.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.005)
    assert not scroller.busy, "le défilement ne s'arrête jamais"


def test_one_wheel_notch_scrolls_the_configured_distance(dashboard):
    """Un cran = WHEEL_PIXELS_PER_NOTCH pixels, ni plus ni moins, même
    parcourus en plusieurs images."""
    view, root = dashboard
    canvas = _canvas(view)
    before = canvas.canvasy(0)

    view._scroller.wheel(-120)  # un cran vers le bas
    _settle(root, view._scroller)

    assert canvas.canvasy(0) - before == pytest.approx(WHEEL_PIXELS_PER_NOTCH, abs=1)


def test_a_notch_glides_in_small_steps_instead_of_jumping(dashboard):
    """Le saut de 60 px d'un coup faisait se recoller les cartes une à une.
    Chaque image n'avance que d'une fraction, en ralentissant."""
    view, root = dashboard
    canvas = _canvas(view)
    positions = [canvas.canvasy(0)]
    real = canvas.yview_scroll
    canvas.yview_scroll = lambda *a: (real(*a), positions.append(canvas.canvasy(0)))[0]

    view._scroller.wheel(-120)
    _settle(root, view._scroller)

    steps = [b - a for a, b in zip(positions, positions[1:])]
    assert len(steps) >= 4, f"mouvement en {len(steps)} déplacement(s) seulement"
    assert max(steps) < WHEEL_PIXELS_PER_NOTCH / 2
    assert steps[0] >= steps[-1], "le mouvement doit ralentir en arrivant"


def test_reversing_the_wheel_turns_around_at_once(dashboard):
    """Un cran dans l'autre sens ne doit pas finir d'abord le mouvement en cours."""
    view, root = dashboard
    canvas = _canvas(view)
    for _ in range(3):
        view._scroller.wheel(-120)
    _settle(root, view._scroller)
    before = canvas.canvasy(0)

    view._scroller.wheel(-120)
    view._scroller.wheel(120)          # demi-tour aussitôt
    _settle(root, view._scroller)
    # Sans demi-tour franc, les deux crans s'annulaient : retour à `before`.
    assert canvas.canvasy(0) < before - WHEEL_PIXELS_PER_NOTCH / 2


def test_wheel_reaches_the_very_bottom_of_the_grid(dashboard):
    """Le défilement doit atteindre la dernière rangée, pas s'arrêter avant."""
    view, root = dashboard
    canvas = _canvas(view)
    for _ in range(200):
        view._scroller.wheel(-120)
    _settle(root, view._scroller)
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


def _pin_pointer(card, on_card=True):
    """Force ce que la carte croit voir sous le curseur.

    L'overlay surveille désormais la position réelle du curseur : sans ce
    leurre, un `_show_overlay()` déclenché par le test se refermerait aussitôt
    puisque la souris de la machine de test est ailleurs.
    """
    card.winfo_containing = (lambda x, y: card) if on_card else (lambda x, y: None)


def test_overlay_is_built_only_on_first_hover(dashboard):
    """L'overlay de survol pesait la moitié du temps de render_games() alors
    qu'une seule carte à la fois l'affiche."""
    view, root = dashboard
    card = view._cards["g0"]
    assert card._overlay is None, "overlay construit d'avance"

    _pin_pointer(card)
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
    _pin_pointer(card)
    card._show_overlay()
    root.update()

    before = canvas.canvasy(0)
    card._overlay._canvas.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    _settle(root, view._scroller)
    assert canvas.canvasy(0) > before, "l'overlay a avalé l'événement molette"


def test_hover_overlay_is_suppressed_while_scrolling(dashboard):
    """Les cartes glissent sous un curseur immobile : sans ce garde-fou les
    overlays clignotaient en rafale au milieu du défilement."""
    view, root = dashboard
    card = view._cards["g0"]

    _pin_pointer(card)
    view._scroller.wheel(-120)
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


def test_cover_has_no_dead_margin_inside_its_label(dashboard):
    """Régression « deux bandes sur les côtés ».

    CTkLabel applique `padx=min(corner_radius, hauteur/2)` autour de son
    contenu. Le corner_radius=12 du label de jaquette laissait 12 px morts à
    gauche et à droite ET amputait l'image d'autant. Les coins arrondis sont
    désormais dessinés DANS l'image (PIL), le label reste carré.
    """
    view, root = dashboard
    label = view._cards["g0"]._cover_lbl
    assert label.cget("corner_radius") == 0
    assert label._label.grid_info().get("padx") == 0, \
        "le label de jaquette réserve une marge : bandes visibles sur les côtés"


def test_the_state_ring_is_drawn_all_around_including_the_corners(dashboard):
    """Le bug qu'on corrige : le rectangle du label de jaquette mordait dans
    l'arc du liseré et en peignait un bout, d'où un angle amputé. Liseré et
    jaquette sont désormais peints dans la même image."""
    from ui_common import COL_RING_IDLE
    view, root = dashboard
    card = view._cards["g0"]
    card.set_state("inactive")
    image = card._ctk_image._light_image
    w, h = image.size
    ring = _rgb(COL_RING_IDLE)

    # Milieu de chaque bord : le liseré, sur toute la périphérie.
    for x, y in ((1, h // 2), (w - 2, h // 2), (w // 2, 1), (w // 2, h - 2)):
        assert image.getpixel((x, y)) == ring, f"pas de liseré en {(x, y)}"
    # Angle : hors de l'arrondi, donc le fond de la grille, jamais le liseré
    # ni une couleur de carte — c'est ce coin qui était « dégueulasse ».
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        assert image.getpixel((x, y)) != ring, f"angle carré en {(x, y)}"
    # Diagonale de l'angle : le liseré doit y passer, arrondi mais présent.
    # Tolérance : l'arc est anticrénelé, le pixel est un mélange très proche.
    offset = round(card.CARD_RADIUS * 0.3)
    corner = image.getpixel((offset, offset))
    assert max(abs(a - b) for a, b in zip(corner, ring)) <= 6,         f"l'arc du liseré est troué dans l'angle : {corner} au lieu de {ring}"


def test_the_two_roundings_stay_concentric(dashboard):
    """Rayon intérieur = rayon extérieur - épaisseur : sinon l'angle de la
    jaquette et celui du liseré ne suivent pas la même courbe."""
    card = dashboard[0]._cards["g0"]
    assert card.COVER_RADIUS == card.CARD_RADIUS - card.BORDER_W
    assert 3 <= card.BORDER_W <= 4, "contour demandé : 3 à 4 px"
    assert card._cover_lbl.place_info()["x"] == "0",         "le label doit couvrir la carte entière, liseré compris"


def test_the_badge_is_painted_into_the_image_not_a_widget(dashboard):
    """CTk peint le reste du canvas d'un coin arrondi avec la couleur du
    PARENT : une pastille CTkFrame arrondie posée sur la jaquette montrait
    quatre encoches sombres aux angles. Elle est donc composée dans l'image."""
    view, root = dashboard
    card = view._cards["g0"]
    assert not hasattr(card, "_badge")
    assert card._ctk_image is not None, \
        "une carte sans jaquette doit déjà montrer sa pastille"


def test_the_badge_only_says_active_or_inactive(app_module):
    """Les états fins (Menu, En jeu) pilotent la bascule de scène mais
    n'apportent rien sur la carte : on veut y lire si le jeu tourne, point."""
    assert app_module.badge_text("inactive") == app_module.state_label("inactive")
    for state in ("active", "menu", "in_game"):
        assert app_module.badge_text(state) == app_module.state_label("active"), state


def test_the_badge_image_differs_between_running_and_stopped(dashboard):
    """Casse si la pastille cesse d'être recomposée : les deux états doivent
    produire des pixels différents, pas seulement un libellé différent."""
    from PIL import Image
    view, root = dashboard
    card = view._cards["g0"]
    card.set_state("inactive")
    card._on_cover_received(Image.new("RGB", (300, 450), (120, 40, 90)))
    stopped = card._ctk_image._light_image.copy()
    card.set_state("active")
    running = card._ctk_image._light_image
    assert stopped.tobytes() != running.tobytes()


def test_cards_without_a_cover_share_one_placeholder_image(dashboard):
    """Quarante jeux sans jaquette = quarante images composées, sans ce cache."""
    view, root = dashboard
    assert view._cards["g0"]._ctk_image is view._cards["g1"]._ctk_image


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
    _pin_pointer(card)
    card._show_overlay()
    root.update()
    assert card._overlay_visible

    _pin_pointer(card, on_card=False)     # la souris a quitté la carte
    card._on_cover_received(Image.new("RGB", (300, 450), (100, 40, 90)))
    root.update()
    assert not card._overlay_visible, "l'overlay est resté collé après l'actualisation"


def test_the_overlay_closes_as_soon_as_the_pointer_leaves(dashboard):
    """Le symptôme : « Éditer / Supprimer » restait affiché et il fallait
    repasser sur la carte pour s'en débarrasser."""
    view, root = dashboard
    card = view._cards["g0"]
    type(card)._hover_blocked_until = 0.0
    _pin_pointer(card)
    card._show_overlay()
    root.update()
    assert card._overlay_visible

    _pin_pointer(card, on_card=False)
    card._on_poster_leave()
    assert not card._overlay_visible, "l'overlay ne s'est pas fermé au <Leave>"


def test_leaving_from_a_button_still_closes_the_overlay(dashboard):
    """Tk n'envoie PAS de <Leave> au cadre quand le curseur passe d'un de ses
    boutons directement dehors : c'est ce trajet qui laissait l'overlay collé.
    La surveillance périodique doit le rattraper."""
    view, root = dashboard
    card = view._cards["g0"]
    type(card)._hover_blocked_until = 0.0
    _pin_pointer(card)
    card._show_overlay()
    root.update()
    assert card._overlay_visible

    _pin_pointer(card, on_card=False)     # sorti sans le moindre événement
    deadline = time.monotonic() + 1.0
    while card._overlay_visible and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert not card._overlay_visible, "aucun filet : l'overlay reste ouvert"
    assert card._hover_job is None, "la surveillance tourne encore dans le vide"


def test_the_overlay_takes_the_exact_shape_of_the_cover(dashboard):
    """Même emprise et même arrondi que la jaquette. Les coins du rectangle de
    l'overlay tombent sur l'arc du liseré : `bg_color` doit donc porter la
    couleur du liseré, sinon l'angle est troué au survol."""
    from ui_common import COL_RING_ACTIVE, COL_RING_IDLE
    card = dashboard[0]._cards["g0"]
    _pin_pointer(card)
    card.set_state("inactive")
    card._show_overlay()
    assert card.OVERLAY_INSET == card.BORDER_W
    assert card._overlay.cget("corner_radius") == card.COVER_RADIUS
    assert card._overlay.cget("bg_color") == COL_RING_IDLE

    card.set_state("active")
    assert card._overlay.cget("bg_color") == COL_RING_ACTIVE,         "le fond des coins n'a pas suivi le changement d'état"


def test_the_overlay_stays_and_comes_back_on_top_if_the_mouse_is_still_there(dashboard):
    from PIL import Image
    view, root = dashboard
    card = view._cards["g0"]
    type(card)._hover_blocked_until = 0.0
    _pin_pointer(card)
    card._show_overlay()
    root.update()

    card._is_descendant = lambda widget: True      # curseur toujours sur la carte
    card._on_cover_received(Image.new("RGB", (300, 450), (40, 90, 100)))
    root.update()

    assert card._overlay_visible
    assert card._edit_btn.winfo_ismapped(), "l'overlay est passé sous la jaquette"
