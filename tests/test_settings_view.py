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


def test_the_menu_holds_every_other_catalog_language_by_native_name(vue, app_module):
    """FR/EN/ES restent des boutons ; toute autre langue du catalogue doit
    être atteignable par le menu, sinon sa traduction ne sert à rien."""
    view, _, _ = vue
    i18n = app_module.i18n
    attendu = set(i18n.available_langs()) - {"fr", "en", "es"}
    assert attendu, "aucune langue en plus de FR/EN/ES dans i18n.json"
    assert set(view._more_langs.values()) == attendu
    for nom, code in view._more_langs.items():
        assert nom == i18n.lang_name(code) != code


def test_picking_from_the_menu_switches_persists_and_lights_only_the_menu(vue, app_module):
    view, mgr, root = vue
    seg = app_module.LanguageSegmentedControl
    nom, code = next(iter(view._more_langs.items()))

    view._lang_menu._dropdown_callback(nom)          # ce que fait un clic
    root.update()

    assert app_module.i18n.current_lang() == code
    assert mgr.load().lang == code
    assert view._lang_menu.get() == nom
    assert view._lang_menu.cget("fg_color") == seg.COL_ACTIVE
    assert all(b.cget("fg_color") != seg.COL_ACTIVE for b in view._lang_seg._buttons.values())

    view._on_lang_selected("fr")                     # retour sur un bouton
    root.update()
    assert view._lang_menu.get() == app_module.t("SETTINGS_LANG_MORE")
    assert view._lang_menu.cget("fg_color") != seg.COL_ACTIVE
    assert view._lang_seg._buttons["fr"].cget("fg_color") == seg.COL_ACTIVE


def test_the_menu_is_in_alphabetical_order_ignoring_accents(vue):
    view, _, _ = vue
    noms = list(view._lang_menu._values)
    assert noms == list(view._more_langs)
    for avant, apres in [("Bahasa Melayu", "Čeština"), ("Čeština", "Dansk"),
                         ("Deutsch", "Italiano"), ("Polski", "Português"), ("Svenska", "Türkçe")]:
        assert noms.index(avant) < noms.index(apres), f"{avant} devrait précéder {apres}"


def test_the_list_opens_on_ten_rows_that_scroll(vue, app_module):
    """Le menu natif de Windows étalait toutes les langues sur la hauteur de
    l'écran : la liste n'en montre plus que dix, le reste défile."""
    view, _, root = vue
    menu = view._lang_menu
    menu._open_dropdown_menu()
    root.update()
    try:
        lo, hi = menu._list._parent_canvas.yview()
        assert round((hi - lo) * len(menu._values)) == menu.VISIBLE_ROWS
        assert len(menu._list.winfo_children()) == len(menu._values)
    finally:
        menu.close_dropdown()


def test_clicking_a_row_picks_the_language_and_closes_the_list(vue, app_module):
    view, mgr, root = vue
    menu = view._lang_menu
    menu._open_dropdown_menu()
    root.update()
    ligne = menu._list.winfo_children()[0]
    nom = ligne.cget("text")

    ligne.invoke()
    root.update()

    assert menu._popup is None
    assert app_module.i18n.current_lang() == view._more_langs[nom]
    assert mgr.load().lang == view._more_langs[nom]


def test_the_list_closes_only_when_focus_leaves_it(vue, monkeypatch):
    """Clic ailleurs : la liste se ferme. Clic sur une de ses lignes ou son
    ascenseur : elle doit rester ouverte, sinon la molette ne sert à rien.
    (La racine de test est masquée et ne peut pas prendre le focus pour de
    vrai : on fixe ce que Tk répondrait.)"""
    view, _, root = vue
    menu = view._lang_menu
    menu._open_dropdown_menu()
    root.update()
    popup = menu._popup

    monkeypatch.setattr(popup, "focus_get", lambda: menu._list.winfo_children()[3])
    menu._close_if_focus_left()
    assert menu._popup is popup, "fermée alors que le focus est resté dans la liste"

    monkeypatch.setattr(popup, "focus_get", lambda: view._pwd_entry)
    menu._close_if_focus_left()
    assert menu._popup is None


def test_the_list_closes_when_the_window_moves(vue):
    """La liste est une fenêtre à part : si la fenêtre principale bouge sans
    elle, elle resterait à flotter à l'ancienne place. Fenêtre affichée : Tk
    n'émet aucun <Configure> quand on déplace une fenêtre masquée."""
    view, _, root = vue
    root.deiconify()
    root.update()
    menu = view._lang_menu
    menu._open_dropdown_menu()
    root.update()

    view._title_lbl.configure(text="Un titre bien plus long qu'avant")   # un widget interne
    root.update()
    assert menu._popup is not None, "fermée par le redimensionnement d'un simple widget"

    root.geometry(f"+{root.winfo_x() + 80}+{root.winfo_y() + 60}")
    root.update()
    assert menu._popup is None


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
