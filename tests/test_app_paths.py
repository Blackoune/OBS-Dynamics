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
