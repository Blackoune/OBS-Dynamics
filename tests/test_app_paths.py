"""Chemins : parité entre `python obs_dynamics.py` et le .exe figé.

Ces trois règles sont ce qui a fait diverger le binaire de la version lancée
en Python : ressources empaquetées cherchées à côté de l'exe au lieu de
sys._MEIPASS, et données utilisateur écrites à côté du binaire au lieu d'un
emplacement unique.
"""
from __future__ import annotations

import sys

import app_paths


def test_ressources_empaquetees_suivent_meipass(monkeypatch, tmp_path):
    """En mode figé, i18n.json et assets/ vivent dans sys._MEIPASS."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert app_paths.get_base_path() == tmp_path


def test_ressources_suivent_le_module_hors_gel(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert (app_paths.get_base_path() / "i18n.json").exists()


def test_donnees_utilisateur_hors_dossier_de_code():
    """DATA_DIR ne doit dépendre ni du dossier des sources ni de celui de
    l'exe : c'est la condition pour que les deux modes voient la même
    bibliothèque."""
    assert app_paths.DATA_DIR.parent == app_paths.USER_CONFIG_DIR
    assert app_paths.GAMES_PATH.parent == app_paths.DATA_DIR


def test_migration_reprend_la_bibliotheque_sans_les_logs(tmp_path):
    legacy = tmp_path / "data"
    (legacy / "covers").mkdir(parents=True)
    (legacy / "games.json").write_text("[]", encoding="utf-8")
    (legacy / "covers" / "a.jpg").write_bytes(b"x")
    (legacy / "obs_dynamics.log").write_text("bruit", encoding="utf-8")
    target = tmp_path / "cible"
    target.mkdir()

    assert app_paths.migrate_legacy_data(legacy, target) is True
    assert (target / "games.json").read_text(encoding="utf-8") == "[]"
    assert (target / "covers" / "a.jpg").exists()
    assert not (target / "obs_dynamics.log").exists()
    assert (tmp_path / "data.old").is_dir()
    assert not legacy.exists()


def test_migration_n_ecrase_pas_une_cible_deja_peuplee(tmp_path):
    """Deuxième lancement : la bibliothèque en place ne doit jamais être
    écrasée par un ancien dossier resté sur le disque."""
    legacy = tmp_path / "data"
    legacy.mkdir()
    (legacy / "games.json").write_text('["ancien"]', encoding="utf-8")
    target = tmp_path / "cible"
    target.mkdir()
    (target / "games.json").write_text('["courant"]', encoding="utf-8")

    assert app_paths.migrate_legacy_data(legacy, target) is False
    assert (target / "games.json").read_text(encoding="utf-8") == '["courant"]'


def test_migration_ignore_un_dossier_sans_bibliotheque(tmp_path):
    """Le `data/` créé à vide par un ancien .exe ne doit rien déclencher."""
    legacy = tmp_path / "data"
    (legacy / "covers").mkdir(parents=True)
    target = tmp_path / "cible"
    target.mkdir()

    assert app_paths.migrate_legacy_data(legacy, target) is False
    assert legacy.exists()


# --- Masquage des secrets dans le journal -------------------------------- #

import logging  # noqa: E402

import pytest  # noqa: E402


@pytest.mark.parametrize("texte, reste", [
    ("GET https://api.rawg.io/api/games?key=0123456789abcdef&search=x",
     "https://api.rawg.io/api/games?key=***&search=x"),
    ("url=https://x.test/cb?code=1&access_token=AbC.123-xyz done",
     "access_token=*** done"),
    ("connexion rtmp://ingest.example.com/live2/abcd-efgh-ijkl-mnop-qrst refusée",
     "rtmp://ingest.example.com/live2/*** refusée"),
    ("OBS_WS_PASSWORD=hunter2hunter2", "OBS_WS_PASSWORD=***"),
    ('{"api_key": "cle-json-factice", "port": 4455}', '"api_key": "***", "port"'),
])
def test_les_secrets_dune_url_sont_masques(texte, reste):
    masque = app_paths.mask_secrets(texte)
    assert reste in masque
    for secret in ("0123456789abcdef", "AbC.123-xyz", "abcd-efgh-ijkl-mnop-qrst",
                   "hunter2hunter2", "cle-json-factice"):
        assert secret not in masque


def test_une_trace_dexception_est_masquee_aussi():
    # C'est par la trace d'une exception `requests` que la clé de stream est
    # partie dans un journal versionné.
    formateur = app_paths._FormatMasque("%(message)s")
    try:
        raise ConnectionError("HTTPSConnectionPool: /api/games?key=CLEFACTICE123")
    except ConnectionError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1,
                                   "échec", None, sys.exc_info())
    sortie = formateur.format(record)
    assert "CLEFACTICE123" not in sortie
    assert "key=***" in sortie


def test_les_handlers_de_lapplication_masquent():
    racine = logging.getLogger()
    assert racine.handlers
    assert all(isinstance(h.formatter, app_paths._FormatMasque)
               for h in racine.handlers
               if h in app_paths._log_handlers)


def test_un_texte_ordinaire_nest_pas_touche():
    texte = "Jaquette récupérée pour Portal 2 (appid 620), 3 tokens restants"
    assert app_paths.mask_secrets(texte) == texte
