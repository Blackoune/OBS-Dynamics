"""Enregistrement du modal de jeu : identité préservée, scènes choisies.

Le modal est un vrai CTkToplevel ; seuls OBS et le scan Steam sont simulés.
"""
from __future__ import annotations

import concurrent.futures

import pytest

ctk = pytest.importorskip("customtkinter")


class FakeClient:
    is_connected = True

    def __init__(self, scenes):
        self.scenes = scenes
        self.created: list[str] = []

    async def get_scene_list(self):
        return self.scenes

    def setup_game_scenes(self, game_name, executable):
        self.created += [f"{game_name} - Menu", f"{game_name} - En jeu"]
        return (f"{game_name} - Menu", f"{game_name} - En jeu")


class FakeLoop:
    def run_coro(self, coro):
        fut: concurrent.futures.Future = concurrent.futures.Future()
        if hasattr(coro, "send"):                     # vraie coroutine
            try:
                coro.send(None)
            except StopIteration as stop:
                fut.set_result(stop.value)
            else:                                     # pragma: no cover
                fut.set_result(None)
        else:
            fut.set_result(coro)
        return fut


@pytest.fixture
def modal(app_module, tmp_path):
    m = app_module
    try:
        root = ctk.CTk()
    except Exception as exc:
        pytest.skip(f"pas d'affichage disponible : {exc}")
    root.withdraw()
    store = m.GameStore(tmp_path / "games.json")
    client = FakeClient(["Scene A", "Scene B", "Ma scene menu", "Ma scene jeu"])

    def open_modal(game=None, candidates=()):
        mod = m.GameModal(root, store=store, obs_loop=FakeLoop(),
                          obs_client_getter=lambda: client,
                          on_saved=lambda: None, steam_candidates=list(candidates), game=game)
        mod.withdraw()
        return mod

    yield m, store, client, open_modal
    root.destroy()


def test_editing_a_steam_game_keeps_its_identity(modal):
    """Régression du doublon : le menu Steam ne liste que les jeux détectés et
    pas encore ajoutés, donc jamais celui qu'on édite. L'ancien code prenait
    le premier candidat de la liste et écrasait nom, appid et dossier."""
    m, store, _client, open_modal = modal
    original = m.Game(id="g1", name="Overwatch", source="steam",
                      active_match="C:/Steam/Overwatch", appid="2357570")
    store.upsert(original)

    autre = [{"name": "Un Autre Jeu", "appid": "999", "install_dir": "C:/Steam/Autre"}]
    mod = open_modal(game=original, candidates=autre)
    mod._menu_images = ["nouvelle_image.png"]        # l'utilisateur change une image
    mod._save()

    games = store.load()
    assert len(games) == 1, f"doublon créé : {[g.name for g in games]}"
    saved = games[0]
    assert saved.id == "g1"
    assert (saved.name, saved.appid, saved.active_match) == \
           ("Overwatch", "2357570", "C:/Steam/Overwatch")
    assert saved.menu_images == ["nouvelle_image.png"]


def test_editing_twice_never_duplicates(modal):
    m, store, _client, open_modal = modal
    g = m.Game(id="g1", name="Jeu", source="manual", active_match="j.exe")
    store.upsert(g)
    for image in ("a.png", "b.png"):
        mod = open_modal(game=store.load()[0])
        mod._ingame_images = [image]
        mod._save()
    assert len(store.load()) == 1
    assert store.load()[0].ingame_images == ["b.png"]


def test_selected_scenes_win_over_auto_created_ones(modal):
    """« C'est celles sélectionnées dans les menus déroulants qui changent »."""
    m, store, client, open_modal = modal
    mod = open_modal()
    mod.source_var.set("manual")
    mod.name_var.set("Overwatch")
    mod.exe_var.set("Overwatch.exe")
    mod.scene_menu_var.set("Ma scene menu")
    mod.scene_ingame_var.set("Ma scene jeu")
    mod.create_scenes_var.set(True)
    mod._save()

    saved = store.load()[0]
    assert client.created, "les scènes auraient dû être créées quand même"
    assert saved.obs_scene_menu == "Ma scene menu"
    assert saved.obs_scene_ingame == "Ma scene jeu"


def test_auto_created_scenes_fill_only_the_empty_fields(modal):
    m, store, _client, open_modal = modal
    mod = open_modal()
    mod.source_var.set("manual")
    mod.name_var.set("Overwatch")
    mod.exe_var.set("Overwatch.exe")
    mod.scene_menu_var.set("Ma scene menu")
    mod.scene_ingame_var.set("")                     # laissé vide
    mod.create_scenes_var.set(True)
    mod._save()

    saved = store.load()[0]
    assert saved.obs_scene_menu == "Ma scene menu"
    assert saved.obs_scene_ingame == "Overwatch - En jeu"


def test_placeholder_is_never_saved_as_a_scene_name(app_module, modal):
    """Sans OBS, le menu n'affiche qu'un libellé d'information : il ne doit
    pas finir enregistré comme nom de scène."""
    m, store, _client, open_modal = modal
    mod = open_modal()
    mod.source_var.set("manual")
    mod.name_var.set("Jeu")
    mod.exe_var.set("j.exe")
    mod.scene_menu_var.set(m.t("GAME_MODAL_SCENE_NOT_CONNECTED"))
    mod._save()
    assert store.load()[0].obs_scene_menu == ""


# -- Validation du cadrage ---------------------------------------------- #

def _png(path):
    import cv2, numpy as np
    img = np.full((300, 400, 3), (40, 30, 60), dtype=np.uint8)
    cv2.rectangle(img, (20, 250), (170, 288), (220, 220, 230), -1)
    cv2.putText(img, "HP 100", (30, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (10, 10, 10), 2)
    cv2.imwrite(str(path), img)
    return str(path)


def test_a_freshly_picked_image_needs_a_review(modal, tmp_path):
    m, _store, _client, open_modal = modal
    mod = open_modal()
    path = _png(tmp_path / "menu.png")
    mod._menu_images = [path]
    assert mod.review_is_stale(path)


def test_validating_records_the_file_fingerprint(modal, tmp_path):
    """C'est l'empreinte qui évite de redemander une validation à chaque
    lancement, tout en la redemandant si l'image change dans le dossier."""
    m, _store, _client, open_modal = modal
    path = _png(tmp_path / "menu.png")
    mod = open_modal()
    mod._menu_images = [path]
    mod._on_review_validated(path, [(0.25, 0.75, 0.1, 0.1)], [], 3)

    assert not mod.review_is_stale(path)
    assert mod._patch_reviews[path]["stamp"] == m.image_stamp(path)
    assert mod._patch_reviews[path]["excluded"] == [[0.25, 0.75, 0.1, 0.1]]
    assert m._REVIEWS.excluded_for(path) == [(0.25, 0.75, 0.1, 0.1)]
    m._REVIEWS.clear()


def test_changing_the_file_makes_the_review_stale_again(modal, tmp_path):
    import os, time
    m, _store, _client, open_modal = modal
    path = _png(tmp_path / "menu.png")
    mod = open_modal()
    mod._menu_images = [path]
    mod._on_review_validated(path, [], [], 2)
    assert not mod.review_is_stale(path)

    _png(tmp_path / "menu.png")                       # image remplacée
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert mod.review_is_stale(path), "changer l'image ne redemande pas de validation"
    m._REVIEWS.clear()


def test_saving_keeps_reviews_and_drops_the_orphans(modal, tmp_path):
    m, store, _client, open_modal = modal
    kept = _png(tmp_path / "menu.png")
    orphan = _png(tmp_path / "vieux.png")
    mod = open_modal()
    mod.source_var.set("manual")
    mod.name_var.set("Jeu")
    mod.exe_var.set("j.exe")
    mod._menu_images = [kept]
    mod._on_review_validated(kept, [(0.1, 0.2, 0.1, 0.1)], [(0.6, 0.7, 0.2, 0.2)], 4)
    mod._patch_reviews[orphan] = {"stamp": "1:2", "excluded": []}
    mod._save()

    saved = store.load()[0]
    assert kept in saved.patch_reviews
    assert saved.patch_reviews[kept]["manual"] == [[0.6, 0.7, 0.2, 0.2]]
    assert orphan not in saved.patch_reviews, "chemin mort conservé dans games.json"
    m._REVIEWS.clear()


# -- États supplémentaires ------------------------------------------------ #

def test_an_extra_state_survives_the_round_trip(modal, tmp_path):
    """Le « + » du bas ajoute un état avec ses images et sa scène : il doit
    revenir tel quel après enregistrement puis réouverture."""
    m, store, _client, open_modal = modal
    mod = open_modal()
    mod.source_var.set("manual")
    mod.name_var.set("Jeu")
    mod.exe_var.set("j.exe")
    section = mod._add_extra_state()
    section.name_var.set("Carte")
    section.images = [_png(tmp_path / "carte.png")]
    section.scene_var.set("Scene B")
    mod._save()

    saved = store.load()[0]
    assert [extra["name"] for extra in saved.extra_states] == ["Carte"]
    assert saved.extra_states[0]["scene"] == "Scene B"
    assert saved.extra_states[0]["images"] == [str(tmp_path / "carte.png")]

    again = open_modal(game=saved)
    assert len(again._sections) == 3
    assert again._sections[2].name_now() == "Carte"


def test_menu_and_ingame_sections_cannot_be_removed(modal):
    """Supprimer « Menu » ou « En jeu » laisserait un jeu sans état de base."""
    m, _store, _client, open_modal = modal
    mod = open_modal()
    mod.remove_state_section(mod._sections[0])
    assert len(mod._sections) == 2

    extra = mod._add_extra_state()
    mod.remove_state_section(extra)
    assert len(mod._sections) == 2


def test_an_extra_state_gets_its_own_id(modal):
    """L'id relie l'état à sa scène et à ses cadrages : deux états ajoutés
    coup sur coup ne peuvent pas le partager."""
    m, _store, _client, open_modal = modal
    mod = open_modal()
    first, second = mod._add_extra_state(), mod._add_extra_state()
    assert first.state_id and first.state_id != second.state_id
    assert first.key.startswith(m.Game.EXTRA_PREFIX)


def test_picking_images_adds_instead_of_replacing(modal, tmp_path, monkeypatch):
    """Le sélecteur remplaçait la liste entière : impossible d'ajouter une
    seconde image sans rechoisir la première."""
    m, _store, _client, open_modal = modal
    mod = open_modal()
    section = mod._sections[0]
    section.images = [_png(tmp_path / "a.png")]
    import ui_game_dialogs
    monkeypatch.setattr(ui_game_dialogs.filedialog, "askopenfilenames",
                        lambda **kwargs: (str(tmp_path / "a.png"), _png(tmp_path / "b.png")))
    monkeypatch.setattr(type(mod), "open_patch_review", lambda self, path, section: None)
    section.add_images()

    assert section.images == [str(tmp_path / "a.png"), str(tmp_path / "b.png")], \
        "l'ajout a écrasé la liste, ou a laissé passer un doublon"


def test_removing_one_image_keeps_the_others(modal, tmp_path):
    m, _store, _client, open_modal = modal
    mod = open_modal()
    section = mod._sections[1]
    first, second = _png(tmp_path / "a.png"), _png(tmp_path / "b.png")
    section.images = [first, second]
    section.remove_image(first)
    assert section.images == [second]
