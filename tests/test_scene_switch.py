"""Bascule automatique de scène OBS.

Reproduit le scénario nominal qui ne marchait pas : la surveillance démarre
pendant qu'OBS finit de se connecter (ou alors que le jeu tourne déjà).
"""
from __future__ import annotations

import concurrent.futures

import pytest


class FakeClient:
    def __init__(self, connected=True, fail=False):
        self.is_connected = connected
        self.fail = fail
        self.switched: list[str] = []

    def set_current_scene(self, scene_name):
        self.switched.append(scene_name)
        if self.fail:
            raise RuntimeError("scène introuvable")
        return scene_name


class FakeLoop:
    """run_coro() planifie et rend un Future, comme AsyncLoopThread."""
    def run_coro(self, coro):
        fut: concurrent.futures.Future = concurrent.futures.Future()
        try:
            fut.set_result(coro)
        except Exception as exc:          # pragma: no cover
            fut.set_exception(exc)
        return fut


class FailingLoop:
    """L'erreur survient DANS la coroutine : elle n'apparaît que dans le
    Future. C'est le cas que l'ancien code ne voyait pas."""
    def run_coro(self, coro):
        fut: concurrent.futures.Future = concurrent.futures.Future()
        fut.set_exception(RuntimeError("SetCurrentProgramScene a échoué"))
        return fut


@pytest.fixture
def worker(app_module, tmp_path):
    m = app_module

    def make(client_getter, loop=None):
        return m.ScanWorker(
            store=m.GameStore(tmp_path / "games.json"),
            obs_loop=loop or FakeLoop(),
            obs_client_getter=client_getter,
            config_mgr=m.EnvConfigManager(tmp_path / ".env"),
            post_ui=lambda fn: fn(),
        )
    return make


def _game(app_module, **kw):
    return app_module.Game(id="g1", name="Jeu", source="manual", active_match="j.exe",
                           obs_scene_menu="Jeu - Menu", obs_scene_ingame="Jeu - En jeu", **kw)


def test_switches_on_state_change(app_module, worker):
    client = FakeClient()
    w = worker(lambda: client)
    w._maybe_switch_scene(_game(app_module), "in_game")
    assert client.switched == ["Jeu - En jeu"]


def test_does_not_switch_twice_for_the_same_state(app_module, worker):
    client = FakeClient()
    w = worker(lambda: client)
    g = _game(app_module)
    w._maybe_switch_scene(g, "menu")
    w._maybe_switch_scene(g, "menu")
    assert client.switched == ["Jeu - Menu"]


def test_transition_is_replayed_once_obs_connects(app_module, worker):
    """LE bug : l'état était mémorisé avant le test de connexion, donc la
    transition était consommée dans le vide et plus jamais rejouée."""
    client = FakeClient(connected=False)
    w = worker(lambda: client)
    g = _game(app_module)

    w._maybe_switch_scene(g, "in_game")          # OBS pas encore connecté
    assert client.switched == []

    client.is_connected = True
    w._maybe_switch_scene(g, "in_game")          # même état, OBS joignable
    assert client.switched == ["Jeu - En jeu"], \
        "la bascule n'a jamais été rejouée après la connexion d'OBS"


def test_no_client_at_all_is_replayed_too(app_module, worker):
    client = FakeClient()
    holder = {"c": None}
    w = worker(lambda: holder["c"])
    g = _game(app_module)

    w._maybe_switch_scene(g, "menu")
    holder["c"] = client
    w._maybe_switch_scene(g, "menu")
    assert client.switched == ["Jeu - Menu"]


def test_failure_inside_the_coroutine_is_retried(app_module, worker):
    """run_coro() rend un Future : sans .result(), l'échec était invisible et
    l'état marqué comme traité."""
    client = FakeClient()
    w = worker(lambda: client, loop=FailingLoop())
    g = _game(app_module)

    w._maybe_switch_scene(g, "in_game")
    assert w._last_states.get("g1") is None, "état mémorisé malgré l'échec"

    w._obs_loop = FakeLoop()                      # OBS redevient sain
    w._maybe_switch_scene(g, "in_game")
    # Deux appels : la tentative ratée puis celle qui aboutit. Ce qui compte
    # est que la seconde ait eu lieu et soit cette fois mémorisée.
    assert client.switched == ["Jeu - En jeu", "Jeu - En jeu"]
    assert w._last_states["g1"] == "in_game"


def test_state_without_a_configured_scene_is_recorded(app_module, worker):
    """« inactive » n'a pas de scène : inutile de le réévaluer chaque cycle."""
    client = FakeClient()
    w = worker(lambda: client)
    w._maybe_switch_scene(_game(app_module), "inactive")
    assert client.switched == []
    assert w._last_states["g1"] == "inactive"


def test_repeated_failures_log_only_once(app_module, worker, caplog):
    client = FakeClient()
    w = worker(lambda: client, loop=FailingLoop())
    g = _game(app_module)
    with caplog.at_level("WARNING"):
        for _ in range(5):
            w._maybe_switch_scene(g, "in_game")
    warnings = [r for r in caplog.records if "impossible" in r.getMessage()]
    assert len(warnings) == 1, f"{len(warnings)} avertissements identiques répétés"
