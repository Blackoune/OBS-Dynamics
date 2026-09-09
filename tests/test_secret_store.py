"""Chiffrement au repos des identifiants du .env (DPAPI)."""
from __future__ import annotations

import sys

import pytest

import secret_store

MDP = "mot-de-passe-factice-éàü"
CLE = "cle-rawg-factice"

windows_seulement = pytest.mark.skipif(
    not secret_store.available(),
    reason="DPAPI n'existe que sous Windows ; ailleurs les valeurs restent en clair.")


# --- Le module seul ------------------------------------------------------ #

@windows_seulement
def test_round_trip_restores_the_exact_value():
    """Accents compris : le blob transporte de l'UTF-8, pas de l'ASCII."""
    assert secret_store.decrypt(secret_store.encrypt(MDP)) == MDP


@windows_seulement
def test_the_clear_value_never_appears_in_the_cipher():
    """Le point de tout l'exercice : le mot de passe ne doit plus être
    lisible dans ce qui est écrit sur le disque."""
    chiffre = secret_store.encrypt(MDP)
    assert MDP not in chiffre
    assert secret_store.is_encrypted(chiffre)


@windows_seulement
def test_encrypting_twice_gives_two_different_blobs():
    """DPAPI ajoute son propre aléa : deux mots de passe identiques ne
    produisent pas le même blob, donc les comparer ne renseigne sur rien."""
    assert secret_store.encrypt(MDP) != secret_store.encrypt(MDP)


@windows_seulement
def test_encrypting_an_already_encrypted_value_is_a_no_op():
    """Sinon un enregistrement répété empilerait les couches, et le
    déchiffrement ne rendrait qu'un blob."""
    une_fois = secret_store.encrypt(MDP)
    assert secret_store.encrypt(une_fois) == une_fois


def test_an_empty_value_stays_empty():
    """Chiffrer « rien » produirait un blob qui laisse croire qu'un mot de
    passe est configuré."""
    assert secret_store.encrypt("") == ""
    assert secret_store.decrypt("") == ""


def test_a_legacy_clear_value_is_read_as_is():
    """Migration : un .env d'avant le chiffrement doit rester lisible."""
    assert secret_store.decrypt("ancien-mot-de-passe") == "ancien-mot-de-passe"
    assert not secret_store.is_encrypted("ancien-mot-de-passe")


def test_an_unreadable_blob_yields_an_empty_string(caplog):
    """Fichier venu d'un autre compte Windows : rendre le blob l'enverrait
    tel quel à OBS comme mot de passe."""
    assert secret_store.decrypt(secret_store.PREFIX + "pas-du-base64-valide!!") == ""
    assert any("déchiffré" in r.message for r in caplog.records)


@pytest.mark.skipif(sys.platform == "win32", reason="cas non-Windows")
def test_without_dpapi_the_value_is_left_alone():
    """Mieux vaut un fichier manifestement en clair qu'un encodage qui
    ressemble à du chiffrement."""
    assert secret_store.encrypt(MDP) == MDP


# --- Entropie secondaire et migration v1 --------------------------------- #

def _blob_v1(valeur: str) -> str:
    """Valeur scellée à l'ancienne : DPAPI sans entropie secondaire."""
    import base64
    brut = secret_store._dpapi("CryptProtectData", valeur.encode("utf-8"), None)
    return secret_store._PREFIX_V1 + base64.urlsafe_b64encode(brut).decode("ascii")


@windows_seulement
def test_the_entropy_is_really_required():
    """Sans elle, un appel générique à CryptUnprotectData suffirait — c'est
    ce que font les outils qui ratissent un profil Windows."""
    import base64
    blob = base64.urlsafe_b64decode(
        secret_store.encrypt(MDP)[len(secret_store.PREFIX):].encode("ascii"))
    with pytest.raises(OSError):
        secret_store._dpapi("CryptUnprotectData", blob, None)


@windows_seulement
def test_a_v1_blob_is_still_readable():
    """Migration : un .env chiffré avant l'entropie doit rester utilisable,
    sinon la mise à jour efface le mot de passe de l'utilisateur."""
    ancien = _blob_v1(MDP)
    assert secret_store.is_encrypted(ancien)
    assert secret_store.decrypt(ancien) == MDP


@windows_seulement
def test_a_v1_blob_is_flagged_for_rewrite():
    assert secret_store.needs_rewrite(_blob_v1(MDP))
    assert secret_store.needs_rewrite("mot-de-passe-en-clair")
    assert not secret_store.needs_rewrite(secret_store.encrypt(MDP))
    assert not secret_store.needs_rewrite("")


@windows_seulement
def test_a_v1_env_is_upgraded_at_startup(app_module, tmp_env):
    """Le fichier passe au format courant sans attendre un enregistrement
    manuel, et le mot de passe reste lisible après conversion."""
    tmp_env.write_text("OBS_WS_PASSWORD=" + _blob_v1(MDP) + chr(10),
                       encoding="utf-8")
    mgr = app_module.EnvConfigManager(tmp_env)

    assert mgr.encrypt_secrets_at_rest() is True
    brut = tmp_env.read_text(encoding="utf-8")
    assert secret_store._PREFIX_V1 not in brut
    assert secret_store.PREFIX in brut
    assert mgr.load().password == MDP


# --- Intégration avec EnvConfigManager ----------------------------------- #

@windows_seulement
def test_saving_writes_the_password_encrypted(app_module, tmp_env):
    mgr = app_module.EnvConfigManager(tmp_env)
    cfg = mgr.load()
    cfg.password, cfg.rawg_api_key = MDP, CLE
    assert mgr.save(cfg)

    brut = tmp_env.read_text(encoding="utf-8")
    assert MDP not in brut, "le mot de passe est encore lisible dans .env"
    assert CLE not in brut, "la clé RAWG est encore lisible dans .env"
    assert brut.count(secret_store.PREFIX) == 2


@windows_seulement
def test_reloading_gives_the_password_back(app_module, tmp_env):
    mgr = app_module.EnvConfigManager(tmp_env)
    cfg = mgr.load()
    cfg.password, cfg.rawg_api_key = MDP, CLE
    mgr.save(cfg)

    relu = app_module.EnvConfigManager(tmp_env).load()
    assert (relu.password, relu.rawg_api_key) == (MDP, CLE)


@windows_seulement
def test_the_other_keys_stay_readable(app_module, tmp_env):
    """Hôte, port, seuils et langue sont des réglages, pas des secrets :
    les chiffrer empêcherait de dépanner le fichier à la main."""
    mgr = app_module.EnvConfigManager(tmp_env)
    cfg = mgr.load()
    cfg.host, cfg.port, cfg.lang, cfg.password = "192.168.1.10", 4460, "es", MDP
    mgr.save(cfg)

    brut = tmp_env.read_text(encoding="utf-8")
    assert "OBS_WS_HOST=192.168.1.10" in brut
    assert "OBS_WS_PORT=4460" in brut
    assert "OBS_APP_LANG=es" in brut


@windows_seulement
def test_a_legacy_clear_env_is_encrypted_at_startup(app_module, tmp_env):
    """Un .env hérité doit être chiffré sans attendre que l'utilisateur
    rouvre l'onglet Paramètres."""
    tmp_env.write_text(f"OBS_WS_HOST=localhost\nOBS_WS_PASSWORD={MDP}\n",
                       encoding="utf-8")
    mgr = app_module.EnvConfigManager(tmp_env)

    assert mgr.encrypt_secrets_at_rest() is True
    brut = tmp_env.read_text(encoding="utf-8")
    assert MDP not in brut
    assert mgr.load().password == MDP        # toujours utilisable


@windows_seulement
def test_startup_encryption_does_not_rewrite_an_already_encrypted_file(app_module, tmp_env):
    """Appelée à chaque démarrage : elle ne doit ni remuer le disque ni
    produire un nouveau blob à chaque lancement."""
    mgr = app_module.EnvConfigManager(tmp_env)
    cfg = mgr.load()
    cfg.password = MDP
    mgr.save(cfg)
    avant = tmp_env.read_text(encoding="utf-8")

    assert mgr.encrypt_secrets_at_rest() is False
    assert tmp_env.read_text(encoding="utf-8") == avant


def test_startup_encryption_on_a_missing_file_does_nothing(app_module, tmp_env):
    assert app_module.EnvConfigManager(tmp_env).encrypt_secrets_at_rest() is False
    assert not tmp_env.exists()
