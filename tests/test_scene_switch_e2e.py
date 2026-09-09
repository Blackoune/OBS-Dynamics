"""Chaîne complète : ce que voit l'écran -> l'ordre envoyé à OBS.

Aucun OBS ni jeu réel : l'écran est fabriqué, le processus est simulé, mais la
détection OpenCV et la logique de bascule sont les vraies.
"""
from __future__ import annotations

import concurrent.futures

import cv2
import numpy as np
import pytest


class FakeClient:
    is_connected = True

    def __init__(self):
        self.switched: list[str] = []

    def set_current_scene(self, scene_name):
        self.switched.append(scene_name)
        return scene_name


class FakeLoop:
    def run_coro(self, coro):
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_result(coro)
        return fut


def _menu_screen():
    """Écran de menu : titre et boutons, fond uni."""
    img = np.full((300, 400, 3), (40, 20, 90), dtype=np.uint8)
    cv2.putText(img, "OVERWATCH", (40, 70), cv2.FONT_HERSHEY_DUPLEX, 1.1, (240, 200, 90), 3)
    for i, label in enumerate(("JOUER", "OPTIONS")):
        y = 130 + i * 70
        cv2.rectangle(img, (110, y), (300, y + 50), (70, 55, 110), -1)
        cv2.putText(img, label, (140, y + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return img


def _ingame_screen(seed=0):
    """Écran de jeu : HUD stable sur un décor qui change à chaque image."""
    rng = np.random.default_rng(seed)
    img = rng.integers(20, 200, (300, 400, 3), dtype=np.uint8)
    cv2.rectangle(img, (20, 250), (170, 288), (220, 220, 230), -1)
    cv2.putText(img, "HP 100", (30, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (10, 10, 10), 2)
    cv2.putText(img, "12:34", (295, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return img


@pytest.fixture
def rig(app_module, tmp_path, monkeypatch):
    m = app_module
    monkeypatch.setattr(m.detection, "is_game_active", lambda game: True)
    m._TEMPLATES = type(m._TEMPLATES)()          # cache vierge entre les tests

    menu_png = tmp_path / "menu.png"
    ingame_png = tmp_path / "ingame.png"
    # Références = captures PLEIN ÉCRAN, comme celles qu'un utilisateur fait
    # avec la touche Impr. écran. Les deux écrans doivent être franchement
    # différents, sinon le test ne mesure rien de réel.
    menu_screen = _menu_screen()
    ingame_screen = _ingame_screen(0)
    cv2.imwrite(str(menu_png), menu_screen)
    cv2.imwrite(str(ingame_png), ingame_screen)

    game = m.Game(id="g1", name="Jeu", source="manual", active_match="j.exe",
                  menu_images=[str(menu_png)], ingame_images=[str(ingame_png)],
                  obs_scene_menu="Scene Menu", obs_scene_ingame="Scene En Jeu")

    client = FakeClient()
    worker = m.ScanWorker(store=m.GameStore(tmp_path / "games.json"), obs_loop=FakeLoop(),
                          obs_client_getter=lambda: client,
                          config_mgr=m.EnvConfigManager(tmp_path / ".env"),
                          post_ui=lambda fn: fn())
    return m, game, worker, client, menu_screen, ingame_screen


def _cycle(m, worker, game, screen, threshold=0.8):
    """Un tour de boucle de surveillance, avec l'écran donné."""
    state = m.detect_game_state(game, threshold, m._downscale(screen))
    worker._maybe_switch_scene(game, state)
    return state


def test_menu_screen_switches_obs_to_the_menu_scene(rig):
    m, game, worker, client, menu_screen, _ = rig
    assert _cycle(m, worker, game, menu_screen) == "menu"
    assert client.switched == ["Scene Menu"]


def test_ingame_screen_switches_obs_to_the_ingame_scene(rig):
    m, game, worker, client, _, ingame_screen = rig
    assert _cycle(m, worker, game, ingame_screen) == "in_game"
    assert client.switched == ["Scene En Jeu"]


def test_going_from_menu_to_game_and_back(rig):
    """Le scénario réel : lancement -> menu -> partie -> retour menu.
    Le décor du jeu change entre les deux images en jeu, comme en vrai."""
    m, game, worker, client, menu_screen, _ = rig
    _cycle(m, worker, game, menu_screen)
    _cycle(m, worker, game, menu_screen)      # rien de neuf, pas de re-bascule
    _cycle(m, worker, game, _ingame_screen(1))
    _cycle(m, worker, game, _ingame_screen(2))
    _cycle(m, worker, game, menu_screen)
    assert client.switched == ["Scene Menu", "Scene En Jeu", "Scene Menu"]


def test_without_reference_images_falls_back_to_the_menu_scene(rig):
    """Sans images de référence, l'état reste « active » pour toujours : la
    détection visuelle ne peut rien distinguer. On bascule alors sur la scène
    de menu au lancement, plutôt que de n'envoyer jamais rien à OBS."""
    m, game, worker, client, menu_screen, _ = rig
    bare = m.Game(id="g2", name="Sans images", source="manual", active_match="j.exe",
                  obs_scene_menu="Scene Menu", obs_scene_ingame="Scene En Jeu")
    assert _cycle(m, worker, bare, menu_screen) == "active"
    assert client.switched == ["Scene Menu"]


def test_unrecognised_frame_mid_game_does_not_bounce_back_to_the_menu(rig):
    """Garde-fou du repli : avec des images configurées, « active » signifie
    « écran non reconnu » (cinématique, chargement). Basculer ferait clignoter
    la scène en pleine partie."""
    m, game, worker, client, _, ingame_screen = rig
    _cycle(m, worker, game, ingame_screen)
    assert client.switched == ["Scene En Jeu"]

    noise = np.random.default_rng(1).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    assert _cycle(m, worker, game, noise) == "active"
    assert client.switched == ["Scene En Jeu"], "la scène a clignoté vers le menu"


def test_unrecognised_screen_stays_active(rig):
    """Un écran qui ne ressemble à aucune référence ne doit pas basculer au
    hasard : mieux vaut ne rien faire que changer de scène à tort."""
    m, game, worker, client, _, _ = rig
    noise = np.random.default_rng(0).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    assert _cycle(m, worker, game, noise) == "active"
    assert client.switched == []


def test_moving_scenery_does_not_flip_the_scene(app_module, tmp_path, monkeypatch):
    """Le scénario qui cassait tout : en jeu, le décor change à chaque cycle.
    Avec la comparaison plein cadre le score s'effondrait sous le seuil au bout
    de quelques images et OBS repartait sur « active » — donc plus de bascule.
    """
    m = app_module
    monkeypatch.setattr(m.detection, "is_game_active", lambda game: True)
    m._TEMPLATES.clear()

    def frame(seed):
        rng = np.random.default_rng(seed)
        img = rng.integers(20, 200, (300, 400, 3), dtype=np.uint8)   # décor mouvant
        cv2.rectangle(img, (20, 250), (170, 288), (220, 220, 230), -1)   # HUD fixe
        cv2.putText(img, "HP 100", (30, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (10, 10, 10), 2)
        cv2.putText(img, "12:34", (300, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        return img

    ref = tmp_path / "ingame.png"
    cv2.imwrite(str(ref), frame(0))
    game = m.Game(id="g1", name="Jeu", source="manual", active_match="j.exe",
                  ingame_images=[str(ref)],
                  obs_scene_menu="Scene Menu", obs_scene_ingame="Scene En Jeu")

    client = FakeClient()
    worker = m.ScanWorker(store=m.GameStore(tmp_path / "g.json"), obs_loop=FakeLoop(),
                          obs_client_getter=lambda: client,
                          config_mgr=m.EnvConfigManager(tmp_path / ".env"),
                          post_ui=lambda fn: fn())

    states = []
    for seed in range(1, 13):
        state = m.detect_game_state(game, 0.8, m._downscale(frame(seed)))
        states.append(worker._stabilizer.update(game.id, state))
        worker._maybe_switch_scene(game, states[-1])

    assert states.count("in_game") == 12, f"états instables : {states}"
    assert client.switched == ["Scene En Jeu"], \
        f"OBS a reçu {len(client.switched)} bascules au lieu d'une"


def test_a_near_tie_never_switches_anything(app_module, tmp_path, monkeypatch):
    """Sans marge de décision, menu=0,82 contre jeu=0,83 suffisait à basculer.
    Une décision prise sur du bruit ressemble à un va-et-vient régulier entre
    les deux scènes. En cas d'égalité, aucune scène n'est imposée."""
    m = app_module
    monkeypatch.setattr(m.detection, "is_game_active", lambda g: True)
    game = m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                  menu_images=["m.png"], ingame_images=["g.png"],
                  obs_scene_menu="Scene Menu", obs_scene_ingame="Scene En Jeu")

    scores = {"m.png": 0.82, "g.png": 0.83}
    monkeypatch.setattr(m.detection, "_best_match_score",
                        lambda screen, paths: max(scores[p] for p in paths))
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    assert m.detect_game_state(game, 0.8, screen) == "active"

    client, worker = FakeClient(), m.ScanWorker(
        store=m.GameStore(tmp_path / "g.json"), obs_loop=FakeLoop(),
        obs_client_getter=lambda: None, config_mgr=m.EnvConfigManager(tmp_path / ".env"),
        post_ui=lambda fn: fn())
    worker._get_obs_client = lambda: client
    worker._maybe_switch_scene(game, m.detect_game_state(game, 0.8, screen))
    assert client.switched == [], "une quasi-égalité a fait basculer une scène"


def test_a_clear_winner_still_switches(app_module, tmp_path, monkeypatch):
    """La marge ne doit pas rendre la détection sourde."""
    m = app_module
    monkeypatch.setattr(m.detection, "is_game_active", lambda g: True)
    game = m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                  menu_images=["m.png"], ingame_images=["g.png"],
                  obs_scene_menu="Scene Menu", obs_scene_ingame="Scene En Jeu")
    scores = {"m.png": 0.10, "g.png": 0.95}
    monkeypatch.setattr(m.detection, "_best_match_score",
                        lambda screen, paths: max(scores[p] for p in paths))
    screen = np.zeros((100, 100, 3), dtype=np.uint8)
    assert m.detect_game_state(game, 0.8, screen) == "in_game"

    scores["m.png"], scores["g.png"] = 0.97, 0.05
    assert m.detect_game_state(game, 0.8, screen) == "menu"


def test_the_state_follows_the_screen_not_a_rhythm(app_module, tmp_path, monkeypatch):
    """Aucune alternance prédéfinie : des durées irrégulières à l'écran
    produisent exactement autant de bascules que de changements réels."""
    m = app_module
    monkeypatch.setattr(m.detection, "is_game_active", lambda g: True)
    game = m.Game(id="g1", name="J", source="manual", active_match="j.exe",
                  menu_images=["m.png"], ingame_images=["g.png"],
                  obs_scene_menu="Scene Menu", obs_scene_ingame="Scene En Jeu")

    showing = {"what": "menu"}
    monkeypatch.setattr(m.detection, "_best_match_score", lambda screen, paths: (
        0.98 if (showing["what"] == "menu") == ("m.png" in paths) else 0.02))

    client = FakeClient()
    worker = m.ScanWorker(store=m.GameStore(tmp_path / "g.json"), obs_loop=FakeLoop(),
                          obs_client_getter=lambda: client,
                          config_mgr=m.EnvConfigManager(tmp_path / ".env"),
                          post_ui=lambda fn: fn())
    screen = np.zeros((100, 100, 3), dtype=np.uint8)

    plan = ["menu"] * 5 + ["game"] * 9 + ["menu"] * 2 + ["game"] * 7 + ["menu"] * 4
    for what in plan:
        showing["what"] = what
        state = worker._stabilizer.update("g1", m.detect_game_state(game, 0.8, screen))
        worker._maybe_switch_scene(game, state)

    assert client.switched == ["Scene Menu", "Scene En Jeu", "Scene Menu",
                               "Scene En Jeu", "Scene Menu"], client.switched
