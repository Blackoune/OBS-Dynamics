"""SettingsView : chargement du formulaire, validation, langue, mot de passe.

L'écran Réglages n'avait qu'un seul test (`test_settings_preserve.py`, une
régression précise) alors qu'il porte toute la validation des valeurs
saisies. Un port hors bornes ou un intervalle trop court y passent par un
`try/except` maison : sans test, une refonte du formulaire peut les laisser
filer jusqu'à la boucle de scan.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

ctk = pytest.importorskip("customtkinter")


@pytest.fixture
def vue(app_module, tmp_env):
    """SettingsView montée sur une racine Tk jetable, .env dans tmp_path."""
    m = app_module
    mgr = m.EnvConfigManager(tmp_env)
    mgr.save(replace(m.OBSConfig(), host="localhost", port=4455,
                     rawg_api_key="cle-rawg-factice", overlay_port=4466))

    langue_initiale = m.i18n.current_lang()
    try:
        root = ctk.CTk()
    except Exception as exc:                       # pas de serveur graphique
        pytest.skip(f"pas d'affichage disponible : {exc}")
    root.withdraw()
    try:
        view = m.SettingsView(root, config_mgr=mgr, on_saved=lambda: None)
        view.pack(fill="both", expand=True)
        root.update()
        yield view, mgr, root
    finally:
        root.destroy()
        m.i18n.set_lang(langue_initiale)


# --- Chargement du formulaire -------------------------------------------- #

def test_the_form_opens_on_the_values_from_disk(vue):
    """Un formulaire vide à l'ouverture ferait croire à une config perdue."""
    view, mgr, _ = vue
    cfg = mgr.load()
    assert view.host_var.get() == cfg.host
    assert view.port_var.get() == str(cfg.port)
    assert view.threshold_var.get() == str(cfg.match_threshold)


def test_the_password_field_is_masked_by_default(vue):
    view, _, _ = vue
    assert view._pwd_entry.cget("show") == "•"


def test_the_eye_button_reveals_and_hides_again(vue):
    view, _, _ = vue
    view._toggle_pwd()
    assert view._pwd_entry.cget("show") == ""
    view._toggle_pwd()
    assert view._pwd_entry.cget("show") == "•"


# --- Validation ----------------------------------------------------------- #

@pytest.mark.parametrize("port", ["pas_un_nombre", "0", "70000", "-1", ""])
def test_an_invalid_port_is_refused_and_nothing_is_written(vue, port):
    """Le port part directement dans la connexion WebSocket : le laisser
    passer donnerait une erreur réseau opaque au lieu d'un message clair."""
    view, mgr, root = vue
    avant = mgr.load()

    view.port_var.set(port)
    view._save()
    root.update()

    assert mgr.load() == avant, "une valeur invalide a été enregistrée"
    assert view.msg_lbl.cget("text"), "aucun message d'erreur affiché"


@pytest.mark.parametrize("saisi,attendu", [("0.01", 0.5), ("0.5", 0.5), ("7", 7.0)])
def test_the_scan_interval_has_a_floor(vue, saisi, attendu):
    """Sous 0,5 s la boucle de scan sature le processeur pour rien."""
    view, mgr, root = vue
    view.interval_var.set(saisi)
    view._save()
    root.update()
    assert mgr.load().scan_interval_seconds == attendu


@pytest.mark.parametrize("saisi,attendu", [("5.0", 1.0), ("-2", 0.0), ("0.65", 0.65)])
def test_the_match_threshold_is_clamped_to_zero_one(vue, saisi, attendu):
    """OpenCV rend un score entre 0 et 1 : au-delà, aucune image ne
    correspond jamais et la détection visuelle est morte sans le dire."""
    view, mgr, root = vue
    view.threshold_var.set(saisi)
    view._save()
    root.update()
    assert mgr.load().match_threshold == attendu


def test_an_empty_host_falls_back_to_localhost(vue):
    """Un hôte vide produirait une URL WebSocket invalide."""
    view, mgr, root = vue
    view.host_var.set("   ")
    view._save()
    root.update()
    assert mgr.load().host == "localhost"


# --- Mot de passe --------------------------------------------------------- #

def test_the_password_survives_the_round_trip(vue):
    """Il est chiffré au repos depuis le 2026-09-09 : le formulaire doit
    toujours le relire tel qu'il a été saisi, accents compris."""
    view, mgr, root = vue
    view.pwd_var.set("Mot2Passe-éàü!")
    view._save()
    root.update()
    assert mgr.load().password == "Mot2Passe-éàü!"


def test_the_password_is_not_readable_in_the_file(vue, tmp_env, app_module):
    view, _, root = vue
    view.pwd_var.set("secret-a-ne-pas-lire")
    view._save()
    root.update()

    if app_module.secret_store.available():
        assert "secret-a-ne-pas-lire" not in tmp_env.read_text(encoding="utf-8")


# --- Langue --------------------------------------------------------------- #

def test_selecting_a_language_switches_and_persists_it(vue, app_module):
    """Changement à chaud ET persistance : appliquer sans écrire ferait
    revenir la langue au redémarrage suivant."""
    view, mgr, root = vue
    cible = "en" if app_module.i18n.current_lang() != "en" else "es"

    view._on_lang_selected(cible)
    root.update()

    assert app_module.i18n.current_lang() == cible
    assert mgr.load().lang == cible


def test_an_unknown_language_changes_nothing(vue, app_module):
    view, mgr, root = vue
    avant_lang = app_module.i18n.current_lang()
    avant_cfg = mgr.load()

    view._on_lang_selected("klingon")
    root.update()

    assert app_module.i18n.current_lang() == avant_lang
    assert mgr.load() == avant_cfg


def test_labels_follow_the_language(vue, app_module):
    """refresh_labels() doit refaire les libellés sans recréer les widgets,
    donc sans vider ce que l'utilisateur vient de taper."""
    view, _, root = vue
    view.host_var.set("10.0.0.9")
    avant = view._field_labels["host"].cget("text")

    cible = "en" if app_module.i18n.current_lang() != "en" else "es"
    app_module.i18n.set_lang(cible)
    view.refresh_labels()
    root.update()

    assert view._field_labels["host"].cget("text") != avant
    assert view.host_var.get() == "10.0.0.9", "la saisie en cours a été perdue"
