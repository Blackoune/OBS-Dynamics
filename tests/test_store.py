"""GameStore : persistance, compatibilité rétro, fusion Steam, cache."""
from __future__ import annotations

import json


def _game(app_module, **kw):
    base = dict(id="id1", name="Jeu", source="manual", active_match="jeu.exe")
    base.update(kw)
    return app_module.Game(**base)


def test_load_missing_file_returns_empty(app_module, tmp_games):
    assert app_module.GameStore(tmp_games).load() == []


def test_roundtrip(app_module, tmp_games):
    store = app_module.GameStore(tmp_games)
    assert store.save([_game(app_module, name="Doom")])
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "Doom"


def test_legacy_bare_list_format_is_accepted(app_module, tmp_games):
    """Les anciennes versions écrivaient un tableau brut au lieu de
    {"games": [...]}. Les deux doivent être lisibles."""
    tmp_games.write_text(json.dumps([
        {"id": "x", "name": "Ancien", "source": "manual", "active_match": "a.exe"}
    ]), encoding="utf-8")
    loaded = app_module.GameStore(tmp_games).load()
    assert [g.name for g in loaded] == ["Ancien"]


def test_corrupted_json_returns_empty_not_crash(app_module, tmp_games):
    tmp_games.write_text("{ ceci n'est pas du json", encoding="utf-8")
    assert app_module.GameStore(tmp_games).load() == []


def test_missing_fields_get_defaults(app_module, tmp_games):
    tmp_games.write_text(json.dumps({"games": [{"name": "Minimal"}]}), encoding="utf-8")
    game = app_module.GameStore(tmp_games).load()[0]
    assert game.name == "Minimal"
    assert game.source == "manual"
    assert game.id           # un id est généré
    assert game.menu_images == []


def test_upsert_replaces_same_id(app_module, tmp_games):
    store = app_module.GameStore(tmp_games)
    store.upsert(_game(app_module, id="same", name="Avant"))
    store.upsert(_game(app_module, id="same", name="Apres"))
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].name == "Apres"


def test_delete(app_module, tmp_games):
    store = app_module.GameStore(tmp_games)
    store.upsert(_game(app_module, id="a"))
    store.upsert(_game(app_module, id="b"))
    store.delete("a")
    assert [g.id for g in store.load()] == ["b"]


def test_import_steam_never_overwrites_user_config(app_module, tmp_games):
    """Régression clé : réimporter la bibliothèque Steam ne doit pas écraser
    les images et scènes configurées à la main pour un jeu déjà présent."""
    store = app_module.GameStore(tmp_games)
    store.upsert(app_module.Game(
        id="keep", name="Portal", source="steam", active_match="C:/Portal",
        appid="400", menu_images=["menu.png"], obs_scene_menu="Ma Scene",
    ))
    added = store.import_steam_games([
        {"appid": "400", "name": "Portal", "install_dir": "C:/Autre"},
        {"appid": "620", "name": "Portal 2", "install_dir": "C:/Portal2"},
    ])
    assert added == 1  # seul Portal 2 est nouveau
    portal = next(g for g in store.load() if g.appid == "400")
    assert portal.menu_images == ["menu.png"]
    assert portal.obs_scene_menu == "Ma Scene"
    assert portal.active_match == "C:/Portal"


def test_cached_list_is_isolated(app_module, tmp_games):
    store = app_module.GameStore(tmp_games)
    store.upsert(_game(app_module, id="a"))
    first = store.load()
    first.append("POLLUTION")
    assert len(store.load()) == 1
