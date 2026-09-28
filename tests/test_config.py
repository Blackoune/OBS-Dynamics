"""EnvConfigManager : lecture, écriture, préservation et cache."""
from __future__ import annotations

import pytest


def test_defaults_when_file_absent(app_module, tmp_env):
    cfg = app_module.EnvConfigManager(tmp_env).load()
    assert cfg.host == "localhost"
    assert cfg.port == 4455
    assert cfg.password == ""


def test_reads_all_live_keys(app_module, tmp_env):
    tmp_env.write_text(
        "OBS_WS_HOST=192.168.1.10\n"
        "OBS_WS_PORT=4460\n"
        "OBS_WS_PASSWORD=secret\n"
        "OBS_SCAN_INTERVAL_SECONDS=3.5\n"
        "OBS_MATCH_THRESHOLD=0.65\n"
        "OBS_APP_LANG=es\n"
        "RAWG_API_KEY=abc123\n",
        encoding="utf-8",
    )
    cfg = app_module.EnvConfigManager(tmp_env).load()
    assert (cfg.host, cfg.port, cfg.password) == ("192.168.1.10", 4460, "secret")
    assert cfg.scan_interval_seconds == 3.5
    assert cfg.match_threshold == 0.65
    assert cfg.lang == "es"
    assert cfg.rawg_api_key == "abc123"


@pytest.mark.parametrize("raw,expected", [
    ("OBS_WS_PORT=pas_un_nombre\n", 4455),      # valeur invalide -> défaut
    ("OBS_WS_PORT=  4470  \n", 4470),           # espaces tolérés
    ('OBS_WS_PORT="4480"\n', 4480),             # guillemets retirés
])
def test_port_parsing_is_forgiving(app_module, tmp_env, raw, expected):
    tmp_env.write_text(raw, encoding="utf-8")
    assert app_module.EnvConfigManager(tmp_env).load().port == expected


def test_threshold_and_interval_are_clamped(app_module, tmp_env):
    tmp_env.write_text("OBS_MATCH_THRESHOLD=5.0\nOBS_SCAN_INTERVAL_SECONDS=0.01\n", encoding="utf-8")
    cfg = app_module.EnvConfigManager(tmp_env).load()
    assert cfg.match_threshold == 1.0      # plafonné
    assert cfg.scan_interval_seconds == 0.5  # plancher


def test_every_catalog_lang_survives_a_restart(app_module, tmp_env):
    """Une langue du menu refusée à la relecture reviendrait au français au
    redémarrage suivant, sans rien dire."""
    for lang in app_module.i18n.available_langs():
        tmp_env.write_text(f"OBS_APP_LANG={lang}\n", encoding="utf-8")
        assert app_module.EnvConfigManager(tmp_env).load().lang == lang


def test_unknown_lang_falls_back(app_module, tmp_env):
    tmp_env.write_text("OBS_APP_LANG=klingon\n", encoding="utf-8")
    assert app_module.EnvConfigManager(tmp_env).load().lang == app_module.i18n.DEFAULT_LANG


def test_save_preserves_unrelated_lines(app_module, tmp_env):
    """Régression : .env peut contenir des clés d'autres outils, save() ne
    doit jamais les écraser."""
    tmp_env.write_text("# commentaire\nCLE_TIERCE=valeur\nOBS_WS_HOST=old\n", encoding="utf-8")
    mgr = app_module.EnvConfigManager(tmp_env)
    cfg = mgr.load()
    cfg.host = "nouveau"
    assert mgr.save(cfg)

    content = tmp_env.read_text(encoding="utf-8")
    assert "# commentaire" in content
    assert "CLE_TIERCE=valeur" in content
    assert mgr.load().host == "nouveau"


def test_cache_is_invalidated_by_save(app_module, tmp_env):
    tmp_env.write_text("OBS_WS_HOST=a\n", encoding="utf-8")
    mgr = app_module.EnvConfigManager(tmp_env)
    assert mgr.load().host == "a"
    cfg = mgr.load()
    cfg.host = "b"
    mgr.save(cfg)
    assert mgr.load().host == "b"


def test_cached_copy_is_isolated(app_module, tmp_env):
    """Muter l'objet retourné ne doit pas polluer le cache interne."""
    tmp_env.write_text("OBS_WS_HOST=stable\n", encoding="utf-8")
    mgr = app_module.EnvConfigManager(tmp_env)
    first = mgr.load()
    first.host = "MUTE"
    assert mgr.load().host == "stable"
