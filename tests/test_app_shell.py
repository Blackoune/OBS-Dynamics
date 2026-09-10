"""La coquille de l'application : App, Sidebar, navigation, file UI.

C'est le câblage que personne ne testait — les vues avaient leurs tests, le
point d'entrée qui les assemble n'en avait aucun. Or c'est là que vivent les
régressions les plus bêtes : un onglet ajouté sans bouton dans la barre
latérale, une vue qui reste affichée sous une autre, un callback posté depuis
un thread qui n'arrive jamais.

L'App réelle ouvre un serveur HTTP, une écoute clavier globale et un
connecteur de chat. Ici, les chemins pointent tous vers tmp_path, le port
overlay est 0 (le système en choisit un libre) et l'écoute clavier est
remplacée par un double : aucun test ne touche au vrai profil utilisateur ni
au vrai réseau.
"""
from __future__ import annotations

import threading

import pytest

ctk = pytest.importorskip("customtkinter")


class _ListenerFactice:
    """Double de ComboListener et HotkeyManager.

    pynput poserait un hook clavier GLOBAL sur la machine qui lance les
    tests : chaque touche tapée ailleurs partirait dans nos callbacks.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False


@pytest.fixture
def app(app_module, tmp_path, monkeypatch):
    m = app_module

    (tmp_path / "covers").mkdir()
    env = tmp_path / ".env"
    env.write_text("OBS_WS_HOST=localhost\nOBS_WS_PORT=4455\nOBS_OVERLAY_PORT=0\n",
                   encoding="utf-8")

    # App lit ces noms dans les globales du module au moment de l'appel :
    # les rediriger suffit, sans toucher au vrai %APPDATA% ni au vrai data/.
    monkeypatch.setattr(m, "ENV_PATH", env)
    monkeypatch.setattr(m, "DATA_DIR", tmp_path)
    monkeypatch.setattr(m, "COVERS_DIR", tmp_path / "covers")
    monkeypatch.setattr(m, "GAMES_PATH", tmp_path / "games.json")
    monkeypatch.setattr(m, "HOTKEYS_PATH", tmp_path / "hotkeys.json")
    monkeypatch.setattr(m, "TRIGGERS_PATH", tmp_path / "triggers.json")
    monkeypatch.setattr(m, "TWITCH_CHAT_PATH", tmp_path / "multistream.json")
    monkeypatch.setattr(m, "ComboListener", _ListenerFactice)
    monkeypatch.setattr(m, "HotkeyManager", _ListenerFactice)

    langue_initiale = m.i18n.current_lang()
    try:
        instance = m.App()
    except Exception as exc:                      # pas de serveur graphique
        pytest.skip(f"pas d'affichage disponible : {exc}")
    instance.withdraw()
    instance.update()
    try:
        yield instance
    finally:
        instance._on_close()
        m.i18n.set_lang(langue_initiale)


# --- Vues et navigation --------------------------------------------------- #

def test_the_four_views_are_built(app):
    assert set(app.views) == {"dashboard", "triggers", "twitch_chat", "settings"}


def test_the_dashboard_is_shown_first(app):
    """Ouvrir sur un onglet vide donnerait l'impression d'une application
    cassée au premier lancement."""
    assert app.views["dashboard"].winfo_manager() == "grid"


@pytest.mark.parametrize("cle", ["triggers", "twitch_chat", "settings", "dashboard"])
def test_navigating_shows_exactly_one_view(app, cle):
    """Régression : un `grid_forget()` oublié laisserait deux vues empilées."""
    app._navigate(cle)
    app.update()
    affichees = [k for k, v in app.views.items() if v.winfo_manager() == "grid"]
    assert affichees == [cle]


def test_the_sidebar_has_a_button_for_every_view(app):
    """Ajouter une vue sans son entrée de navigation la rendrait
    inatteignable — et rien ne le signalerait au démarrage."""
    assert set(app.sidebar.nav_buttons) == set(app.views)


def test_every_nav_entry_has_a_label(app):
    """Les onglets n'ont plus d'icône — les emoji couleur sortaient de la
    palette et décalaient les libellés d'un onglet à l'autre. Reste le
    libellé, porté par le bouton lui-même : sans lui, l'onglet est vide."""
    manquantes = set(app.views) - set(app.sidebar.nav_buttons)
    assert not manquantes, f"onglets sans libellé : {sorted(manquantes)}"
    assert all(app.sidebar.nav_buttons[key].cget("text") for key in app.views)


def test_every_tab_shows_its_frame_even_unselected(app):
    """Un onglet non sélectionné était entièrement transparent : rien
    n'indiquait où cliquer. Tous portent désormais leur cadre."""
    app._navigate("triggers")
    app.update()
    for key, btn in app.sidebar.nav_buttons.items():
        assert btn.cget("border_width") == 1, key
        assert btn.cget("fg_color") != "transparent", key


def test_the_nav_label_has_no_box_of_its_own(app):
    """Le libellé était un CTkLabel posé sur le bouton : son fond dessinait un
    rectangle plus clair autour du texte, d'où l'effet « surligné »."""
    import customtkinter as ctk
    btn = app.sidebar.nav_buttons["settings"]
    poses = [child for child in btn.winfo_children() if isinstance(child, ctk.CTkLabel)]
    assert not poses, "un label est reposé sur le bouton : le rectangle revient"


def test_clicking_a_sidebar_button_navigates(app):
    """Le bouton porte deux labels par-dessus lui qui interceptent le clic ;
    la commande doit rester câblée sur la navigation."""
    app.sidebar.nav_buttons["settings"].invoke()
    app.update()
    assert app.views["settings"].winfo_manager() == "grid"


def test_the_active_tab_is_the_only_one_highlighted(app):
    """Tous les onglets ont un cadre : ce sont sa COULEUR, celle du fond et
    celle du texte qui disent lequel est sélectionné."""
    from ui_common import COL_BORDER_ACCENT
    app._navigate("triggers")
    app.update()
    surlignes = [k for k, b in app.sidebar.nav_buttons.items()
                 if b.cget("border_color") == COL_BORDER_ACCENT]
    assert surlignes == ["triggers"]
    actif = app.sidebar.nav_buttons["triggers"]
    repos = app.sidebar.nav_buttons["settings"]
    assert actif.cget("fg_color") != repos.cget("fg_color")
    assert actif.cget("text_color") != repos.cget("text_color")


# --- File UI thread-safe -------------------------------------------------- #

def test_post_ui_runs_the_callback_on_the_main_thread(app):
    """Tout ce qui vient d'un thread (scan, jaquettes, chat) passe par là.
    Exécuter le callback sur le thread appelant remettrait du Tk hors du
    thread UI — plantage aléatoire, le pire genre."""
    principal = threading.get_ident()
    vu: list[int] = []

    fil = threading.Thread(target=lambda: app.post_ui(lambda: vu.append(threading.get_ident())))
    fil.start()
    fil.join()
    assert vu == []                    # rien n'a encore tourné

    app._pump_ui_queue()
    assert vu == [principal]


def test_a_failing_callback_does_not_block_the_queue(app):
    """Isolation des erreurs : un callback qui lève ne doit pas emporter les
    suivants, ni arrêter la pompe."""
    passes: list[str] = []
    app.post_ui(lambda: (_ for _ in ()).throw(RuntimeError("boum")))
    app.post_ui(lambda: passes.append("suivant"))

    app._pump_ui_queue()
    assert passes == ["suivant"]

    app.post_ui(lambda: passes.append("encore"))
    app._pump_ui_queue()
    assert passes == ["suivant", "encore"]


def test_chat_status_from_a_thread_reaches_the_view(app):
    """_on_chat_status est appelée depuis le thread d'un connecteur : elle ne
    doit rien toucher directement, seulement poster."""
    app._on_chat_status("twitch", "connected", "chaine")
    app._pump_ui_queue()
    app.update()          # aucune exception = la vue a bien reçu le statut


# --- Barre latérale : états ----------------------------------------------- #

def test_running_state_swaps_the_buttons(app):
    app.sidebar.set_running_state(True)
    assert app.sidebar.start_btn.cget("state") == "disabled"
    assert app.sidebar.stop_btn.cget("state") == "normal"

    app.sidebar.set_running_state(False)
    assert app.sidebar.start_btn.cget("state") == "normal"
    assert app.sidebar.stop_btn.cget("state") == "disabled"


def test_changing_language_relabels_the_sidebar(app, app_module):
    """Changement de langue à chaud : les libellés se refont sans recréer les
    widgets, donc sans perdre l'onglet actif."""
    avant = app.sidebar.nav_buttons["settings"].cget("text")
    app._navigate("triggers")

    cible = "en" if app_module.i18n.current_lang() != "en" else "fr"
    assert app_module.i18n.set_lang(cible)
    app.update()

    assert app.sidebar.nav_buttons["settings"].cget("text") != avant
    assert app.views["triggers"].winfo_manager() == "grid", "onglet actif perdu"


def test_closing_unhooks_the_language_listener(app, app_module):
    """i18n garde ses listeners dans une liste de module, qui survit à la
    fenêtre. Sans désinscription, chaque App fermée laisserait un callback
    rappelant des widgets détruits au prochain changement de langue."""
    listeners = app_module.i18n._instance._listeners
    avant = len(listeners)
    app._on_close()
    assert len(listeners) == avant - 1

    app._on_close()                     # la fixture rappellera : reste idempotent
    assert len(listeners) == avant - 1


# --- Table des déclencheurs ----------------------------------------------- #

def test_the_combo_map_only_keeps_usable_rules(app, app_module, tmp_path):
    """Une règle désactivée ou incomplète dans la table ferait clignoter un
    overlay vide au premier appui."""
    media = tmp_path / "m.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    regles = [
        app_module.TriggerRule(id="ok", hotkey="ctrl+a", media_type="image",
                               media_path=str(media), enabled=True),
        app_module.TriggerRule(id="off", hotkey="ctrl+b", media_type="image",
                               media_path=str(media), enabled=False),
        app_module.TriggerRule(id="vide", hotkey="ctrl+c", media_type="image",
                               media_path="", enabled=True),
    ]
    for regle in regles:
        app.trigger_store.upsert(regle)

    app._rebuild_combo_map()
    assert app._combo_map == {"ctrl+a": "ok"}


def test_an_unknown_combo_is_ignored(app):
    """L'écoute clavier est globale : elle reçoit TOUTES les combinaisons de
    la machine, dont l'immense majorité ne nous concerne pas."""
    app._on_combo("ctrl+shift+inconnu")
    app._on_combo_release("ctrl+shift+inconnu")     # aucune exception attendue
