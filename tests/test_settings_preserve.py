"""L'ecran Reglages ne doit pas effacer les cles qu'il n'edite pas.

SettingsView n'expose que host/port/password/interval/threshold/lang, mais
EnvConfigManager.save() reecrit les 8 cles du .env. Si le formulaire repart
d'un OBSConfig() neuf au lieu de la config sur disque, RAWG_API_KEY et
OBS_OVERLAY_PORT retombent a leur valeur par defaut a chaque enregistrement.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

ctk = pytest.importorskip("customtkinter")


def test_saving_settings_keeps_rawg_key_and_overlay_port(app_module, tmp_env):
    m = app_module
    mgr = m.EnvConfigManager(tmp_env)
    mgr.save(replace(m.OBSConfig(), rawg_api_key="cle-rawg-1234", overlay_port=5555))

    try:
        root = ctk.CTk()
    except Exception as exc:                       # pas de serveur graphique
        pytest.skip(f"pas d'affichage disponible : {exc}")

    try:
        view = m.SettingsView(root, config_mgr=mgr, on_saved=lambda: None)
        view.pack(fill="both", expand=True)
        root.update()

        view.host_var.set("10.0.0.2")
        view.pwd_var.set("secret")
        view._save()
        root.update()
    finally:
        root.destroy()

    cfg = mgr.load()
    assert cfg.host == "10.0.0.2"
    assert cfg.password == "secret"
    assert cfg.rawg_api_key == "cle-rawg-1234"     # efface avant le correctif
    assert cfg.overlay_port == 5555                # remis a 4466 avant le correctif
