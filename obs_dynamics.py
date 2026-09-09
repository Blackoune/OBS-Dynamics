"""
OBS Dynamics — Point d'entrée : fenêtre principale, navigation, câblage.

L'application bascule les scènes OBS selon le jeu détecté, déclenche des
médias sur raccourci clavier et affiche le chat Twitch dans une source
navigateur. Tout tourne en threads locaux ; aucun serveur web externe, aucun
PowerShell côté utilisateur.

Le découpage en modules (2026-09-09) a remplacé un fichier unique de 4177
lignes. Chaque module porte une responsabilité :

    app_paths.py        chemins, journalisation, éveil DPI
    env_config.py       lecture/écriture du .env utilisateur
    games.py            scan Steam, modèle Game, persistance
    detection.py        processus + comparaison visuelle (sans interface)
    obs_client.py       WebSocket OBS v5 et boucle de scan
    ui_common.py        palette, police, libellés d'état, glisser-déposer
    ui_dashboard.py     grille de cartes de jeu
    ui_game_dialogs.py  fiche de jeu et relecture des patchs
    ui_settings.py      vue Paramètres
    ui_triggers.py      vue Raccourcis & Overlays
    ui_twitch_chat.py   vue Chat Twitch

Ce module ré-exporte les noms publics de ces modules : la suite de tests et
tout code existant peuvent continuer à faire `import obs_dynamics`.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from typing import Callable, Optional

from app_paths import (ASSETS_DIR, BASE_DIR, COVERS_DIR, DATA_DIR, ENV_PATH,
                       GAMES_PATH, HOTKEYS_PATH, I18N_PATH, ICON_PATH,
                       LOG_PATH, TWITCH_CHAT_PATH, TRIGGERS_PATH,
                       USER_CONFIG_DIR, enable_dpi_awareness, get_base_path,
                       get_user_config_dir, logger, migrate_legacy_env)

# i18n : initialisé AVANT toute construction de widget CTk, donc avant
# l'import des vues ci-dessous.
import i18n

i18n.init(path=I18N_PATH)

from i18n import t  # noqa: E402

# Modules locaux (racine du projet, embarqués par build.spec). Les vues
# reçoivent ctk depuis ui_common, qui a déjà réglé l'éveil DPI à temps.
import detection  # noqa: E402  (expose le module pour le monkeypatch des tests)
import screen_match  # noqa: E402
import secret_store  # noqa: E402
from env_config import ENV_KEYS, EnvConfigManager, OBSConfig  # noqa: E402
from cover_service import GameCoverService  # noqa: E402
from detection import (DECISION_MARGIN, DETECT_SCALE, _PATCHES, _PatchCache,  # noqa: E402
                       _PatchReviews, _REVIEWS, _SCALES, _ScaleCalibration,
                       _TEMPLATES, _TemplateCache, _best_match_score,
                       _capture_screen_bgr, _downscale, _imread_unicode,
                       _match_one, _rescale_template, detect_game_state,
                       image_stamp, is_game_active)
from games import Game, GameStore, SteamScanner  # noqa: E402
from hotkeys import (ComboListener, ComboRecorder, HotkeyManager,  # noqa: E402
                     format_combo, load_bindings)
from twitch_chat import (PLATFORMS as CHAT_PLATFORMS,  # noqa: E402
                         PLATFORM_COLORS as CHAT_COLORS, ChatHub,
                         TwitchChatStore, PlatformConfig, Status as ChatStatus)
from obs_client import (AsyncLoopThread, OBSClient, OBSClientError,  # noqa: E402
                        ScanWorker)
from overlay_server import (DEFAULT_PORT as OVERLAY_DEFAULT_PORT,  # noqa: E402
                            OverlayServer)
from triggers import (DURATION_PRESETS_MS, MEDIA_EXTENSIONS, MEDIA_TYPES,  # noqa: E402
                      TriggerRule, TriggerStore)
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_ACCENT_SOFT,  # noqa: E402
                       COL_BADGE_BG_ACTIVE, COL_BADGE_BG_INACTIVE,
                       COL_BADGE_FG, COL_BG, COL_BG_GRADIENT_TOP, COL_BORDER,
                       COL_BORDER_ACCENT, COL_CARD, COL_CARD_HOVER, COL_GREEN,
                       COL_RED, COL_SIDEBAR, COL_TEXT, COL_TEXT_MUTED,
                       COL_YELLOW, FONT_FAMILY, STATE_BADGE_BG,
                       _try_enable_dnd, badge_text, ctk, font,
                       parse_dropped_files, state_label)
from ui_dashboard import DashboardView, GameCard  # noqa: E402
from ui_game_dialogs import GameModal, PatchReviewDialog  # noqa: E402
from ui_twitch_chat import (ConnectionDialog, TwitchChatCard,  # noqa: E402
                            TwitchChatView)
from ui_settings import LanguageSegmentedControl, SettingsView  # noqa: E402
from ui_triggers import TriggerRow, TriggersView  # noqa: E402


# ============================================================================
# SIDEBAR
# ============================================================================
class Sidebar(ctk.CTkFrame):
    def __init__(self, master, on_nav: Callable[[str], None],
                 on_start: Callable[[], None], on_stop: Callable[[], None],
                 on_open_folder: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_SIDEBAR, corner_radius=0, width=240, **kwargs)
        self.grid_propagate(False)
        self.grid_rowconfigure(6, weight=1)
        self.on_nav = on_nav

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=22, pady=(26, 30))
        self._brand_icon_lbl = ctk.CTkLabel(brand, text=t("SIDEBAR_BRAND_ICON"), font=font(19, "bold"),
                                             text_color=COL_ACCENT)
        self._brand_icon_lbl.pack(side="left")
        self._brand_suffix_lbl = ctk.CTkLabel(brand, text=t("SIDEBAR_BRAND_SUFFIX"), font=font(19, "bold"),
                                               text_color=COL_TEXT)
        self._brand_suffix_lbl.pack(side="left")

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.nav_labels: dict[str, ctk.CTkLabel] = {}
        self._nav_btn("dashboard", t("SIDEBAR_NAV_DASHBOARD"), row=1)
        # Raccourcis & Overlays s'insère ENTRE l'accueil et les paramètres.
        self._nav_btn("triggers", t("SIDEBAR_NAV_TRIGGERS"), row=2)
        # Multi Stream s'insère ENTRE Raccourcis & Overlays et Paramètres.
        self._nav_btn("twitch_chat", t("SIDEBAR_NAV_TWITCH_CHAT"), row=3)
        self._nav_btn("settings", t("SIDEBAR_NAV_SETTINGS"), row=4)

        self._folder_btn = ctk.CTkButton(self, text=t("SIDEBAR_BTN_OPEN_FOLDER"), anchor="w", height=36,
                                          corner_radius=8, fg_color="transparent", hover_color=COL_CARD,
                                          text_color=COL_TEXT_MUTED, font=font(12), command=on_open_folder)
        self._folder_btn.grid(row=5, column=0, sticky="ew", padx=12, pady=(10, 3))

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=6, column=0, sticky="sew", padx=16, pady=20)

        self.status_dot = ctk.CTkLabel(ctrl, text="●", text_color=COL_RED, font=font(14))
        self.status_dot.pack(anchor="w")
        self.status_text = ctk.CTkLabel(ctrl, text=t("SIDEBAR_STATUS_STOPPED"), font=font(11),
                                         text_color=COL_TEXT_MUTED, wraplength=195, justify="left", anchor="w")
        self.status_text.pack(anchor="w", pady=(0, 10), fill="x")

        self.start_btn = ctk.CTkButton(ctrl, text=t("SIDEBAR_BTN_START"), height=38, fg_color=COL_GREEN,
                                        hover_color="#16A34A", font=font(12, "bold"), corner_radius=9,
                                        command=on_start)
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ctk.CTkButton(ctrl, text=t("SIDEBAR_BTN_STOP"), height=38, fg_color=COL_RED,
                                       hover_color="#DC2626", font=font(12, "bold"), corner_radius=9,
                                       command=on_stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)

        self._is_running = False

    # Icônes de navigation, séparées du libellé traduit.
    #
    # Elles étaient auparavant collées dans la chaîne i18n ("⌨️  Raccourcis").
    # Problème : les emoji n'ont pas tous la même largeur d'avance — 🏠 est
    # un emoji pleine chasse, ⌨️ et ⚙️ sont des glyphes texte promus en emoji
    # par un sélecteur de variante (U+FE0F) et se rendent plus étroits. Les
    # libellés démarraient donc à des abscisses différentes. En plaçant
    # l'icône dans sa propre colonne de largeur FIXE, le texte de tous les
    # onglets commence exactement au même endroit.
    NAV_ICONS = {"dashboard": "🏠", "triggers": "⌨️", "twitch_chat": "💬", "settings": "⚙️"}
    NAV_ICON_WIDTH = 28

    def _nav_btn(self, key: str, text: str, row: int) -> None:
        btn = ctk.CTkButton(self, text="", anchor="w", height=42, corner_radius=9,
                             fg_color="transparent", hover_color=COL_CARD,
                             command=lambda: self.on_nav(key))
        btn.grid(row=row, column=0, sticky="ew", padx=12, pady=3)

        icon = ctk.CTkLabel(btn, text=self.NAV_ICONS.get(key, ""), font=font(14),
                             width=self.NAV_ICON_WIDTH, anchor="center", fg_color="transparent")
        icon.place(x=12, rely=0.5, anchor="w")
        label = ctk.CTkLabel(btn, text=text, font=font(13), text_color=COL_TEXT,
                              anchor="w", fg_color="transparent")
        label.place(x=12 + self.NAV_ICON_WIDTH, rely=0.5, anchor="w")

        # Les labels posés sur le bouton interceptent le clic : on le relaie.
        for widget in (icon, label):
            widget.bind("<Button-1>", lambda _e, k=key: self.on_nav(k))

        self.nav_buttons[key] = btn
        self.nav_labels[key] = label

    def set_active(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=COL_CARD if k == key else "transparent",
                          border_width=1 if k == key else 0,
                          border_color=COL_BORDER_ACCENT if k == key else COL_BORDER)

    def set_running_state(self, running: bool) -> None:
        self._is_running = running
        self.status_dot.configure(text_color=COL_GREEN if running else COL_RED)
        self.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING") if running else t("SIDEBAR_STATUS_STOPPED"))
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def refresh_labels(self) -> None:
        self._brand_icon_lbl.configure(text=t("SIDEBAR_BRAND_ICON"))
        self._brand_suffix_lbl.configure(text=t("SIDEBAR_BRAND_SUFFIX"))
        self.nav_labels["dashboard"].configure(text=t("SIDEBAR_NAV_DASHBOARD"))
        self.nav_labels["triggers"].configure(text=t("SIDEBAR_NAV_TRIGGERS"))
        self.nav_labels["twitch_chat"].configure(text=t("SIDEBAR_NAV_TWITCH_CHAT"))
        self.nav_labels["settings"].configure(text=t("SIDEBAR_NAV_SETTINGS"))
        self._folder_btn.configure(text=t("SIDEBAR_BTN_OPEN_FOLDER"))
        self.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING") if self._is_running else t("SIDEBAR_STATUS_STOPPED"))
        self.start_btn.configure(text=t("SIDEBAR_BTN_START"))
        self.stop_btn.configure(text=t("SIDEBAR_BTN_STOP"))


# ============================================================================
# APPLICATION PRINCIPALE
# ============================================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(t("APP_TITLE_WINDOW"))
        self.geometry("1180x720")
        self.minsize(960, 620)
        self.configure(fg_color=COL_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        self.config_mgr = EnvConfigManager(ENV_PATH)
        # Reprend un .env où le mot de passe OBS et la clé RAWG sont encore en
        # clair : sans ça ils ne seraient chiffrés qu'au prochain
        # enregistrement depuis l'onglet Paramètres, donc peut-être jamais.
        # N'écrit rien s'il n'y a rien à chiffrer.
        self.config_mgr.encrypt_secrets_at_rest()
        # Applique la langue persistée en .env avant toute construction de vue.
        _saved_cfg = self.config_mgr.load()
        i18n.set_lang(_saved_cfg.lang)

        self.store = GameStore(GAMES_PATH)
        self.steam_scanner = SteamScanner()
        self.obs_loop = AsyncLoopThread()
        self._obs_client: Optional[OBSClient] = None
        self.scan_worker = ScanWorker(self.store, self.obs_loop, lambda: self._obs_client,
                                       self.config_mgr, self.post_ui)

        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        # Jaquettes : le service remet TOUS ses callbacks via post_ui, donc
        # aucun widget Tk n'est touché depuis le thread de téléchargement.
        self.cover_service = GameCoverService(COVERS_DIR, _saved_cfg.rawg_api_key,
                                              dispatch=self.post_ui)

        # Hotkeys globales : forcent un état de jeu quand la détection
        # visuelle se trompe. Démarrées avec la surveillance, pas avant.
        # Exclusions de fragments validées par l'utilisateur : elles portent sur
        # les IMAGES, donc elles doivent être en place avant le premier scan.
        _REVIEWS.load_from_games(self.store.load())

        self.hotkey_manager = HotkeyManager(on_hotkey=self._on_hotkey,
                                            bindings=load_bindings(HOTKEYS_PATH))

        # Superviseur de reconnexion OBS (backoff exponentiel).
        self._reconnect_stop = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None

        # --- Déclencheurs média (onglet Raccourcis & Overlays) -------------
        # Le serveur et l'écoute clavier tournent tant que l'application est
        # ouverte, indépendamment du Démarrer/Arrêter de la surveillance :
        # les sources navigateur d'OBS doivent rester joignables en
        # permanence, sinon elles affichent une page d'erreur au démarrage
        # d'OBS et ne se reconnectent qu'après un rechargement manuel.
        self.trigger_store = TriggerStore(TRIGGERS_PATH)

        # --- Multi Stream ---------------------------------------------------
        # Le hub est construit AVANT le serveur pour que celui-ci serve la
        # route /chat dès sa première requête : une source navigateur déjà
        # ouverte dans OBS interroge le serveur à la seconde où il écoute.
        self.twitch_chat_store = TwitchChatStore(TWITCH_CHAT_PATH)
        self.twitch_chat_store.ensure_token()   # jeton permanent, créé une fois

        self.chat_hub = ChatHub(self.twitch_chat_store,
                                on_status_change=self._on_chat_status)

        self.overlay = OverlayServer(
            self.trigger_store.get, port=_saved_cfg.overlay_port,
            chat_hub=self.chat_hub,
            chat_token_getter=lambda: self.twitch_chat_store.load().overlay_token,
            text_getter=t)
        self.overlay.start()
        self.chat_hub.apply_config(self.twitch_chat_store.load())
        self._combo_listener = ComboListener(on_combo=self._on_combo,
                                              on_release=self._on_combo_release)
        self._combo_map: dict[str, str] = {}   # combo -> rule_id
        self._rebuild_combo_map()
        self._combo_listener.start()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, on_nav=self._navigate, on_start=self._start, on_stop=self._stop,
                                on_open_folder=self._open_data_folder)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.dashboard = DashboardView(self.content, store=self.store, obs_loop=self.obs_loop,
                                        obs_client_getter=lambda: self._obs_client,
                                        steam_scanner=self.steam_scanner, post_ui=self.post_ui,
                                        cover_service=self.cover_service)
        self.triggers = TriggersView(self.content, store=self.trigger_store,
                                      overlay=self.overlay, post_ui=self.post_ui,
                                      on_rules_changed=self._rebuild_combo_map)
        self.twitch_chat = TwitchChatView(self.content, store=self.twitch_chat_store,
                                            hub=self.chat_hub, overlay=self.overlay)
        self.settings = SettingsView(self.content, self.config_mgr, on_saved=self._on_settings_saved)
        self.views: dict[str, ctk.CTkFrame] = {
            "dashboard": self.dashboard,
            "triggers": self.triggers,
            "twitch_chat": self.twitch_chat,
            "settings": self.settings,
        }
        self._navigate("dashboard")

        # --- Rechargement à chaud : toute vue exposant refresh_labels() est
        # notifiée à chaque changement de langue, sans jamais redémarrer
        # l'application ni recréer les widgets structurels.
        i18n.on_change(self._on_lang_changed)

        self._pump_ui_queue()

    def _on_lang_changed(self, _lang: str) -> None:
        self.title(t("APP_TITLE_WINDOW"))
        self.sidebar.refresh_labels()
        self.dashboard.refresh_labels()
        self.triggers.refresh_labels()
        self.twitch_chat.refresh_labels()
        self.settings.refresh_labels()

    def _on_chat_status(self, platform: str, status: str, detail: str) -> None:
        """Appelée depuis le thread d'un connecteur — aucune opération Tk ici.

        La vue peut ne pas exister encore : le hub démarre ses connecteurs
        avant la construction de l'interface, précisément pour que le chat
        soit déjà en ligne quand l'utilisateur ouvre l'onglet.
        """
        self.post_ui(lambda: self._apply_chat_status(platform, status, detail))

    def _apply_chat_status(self, platform: str, status: str, detail: str) -> None:
        view = getattr(self, "twitch_chat", None)
        if view is not None:
            view.apply_status(platform, status, detail)

    # -- Déclencheurs média ------------------------------------------------ #

    def _rebuild_combo_map(self) -> None:
        """Recalcule la table combinaison -> règle après toute édition.

        Une seule écoute clavier globale sert toutes les règles ; c'est cette
        table qui fait la résolution, plutôt qu'un listener par raccourci.
        """
        mapping: dict[str, str] = {}
        for rule in self.trigger_store.load():
            if rule.enabled and rule.is_complete:
                mapping[rule.hotkey] = rule.id
        self._combo_map = mapping
        logger.debug("Table des déclencheurs : %d combinaison(s) active(s).", len(mapping))

    def _on_combo(self, combo: str) -> None:
        """Appelé depuis le thread pynput — aucune opération Tk ici."""
        rule_id = self._combo_map.get(combo)
        if rule_id is None:
            return
        listeners = self.overlay.fire(rule_id)
        if listeners:
            logger.info(t("LOG_TRIGGER_FIRED", combo=format_combo(combo), count=listeners))
        else:
            # Cas le plus fréquent en cas de « ça ne marche pas » : la source
            # navigateur n'est pas ouverte dans OBS. On le dit explicitement.
            logger.warning(t("LOG_TRIGGER_NO_LISTENER", combo=format_combo(combo)))

    def _on_combo_release(self, combo: str) -> None:
        """Relâchement de la touche : ne concerne que les règles en mode
        maintien (durée = 0). Les règles minutées ignorent l'événement côté
        page, la durée configurée faisant foi."""
        rule_id = self._combo_map.get(combo)
        if rule_id is None:
            return
        self.overlay.fire(rule_id, action="hide")

    # -- Icône fenêtre + barre des tâches --------------------------------- #
    def _set_window_icon(self) -> None:
        if ICON_PATH.exists():
            try:
                self.iconbitmap(str(ICON_PATH))
            except Exception:
                logger.debug("iconbitmap a échoué (plateforme non-Windows ?).", exc_info=True)
        try:
            # AppUserModelID: force Windows à afficher l'icône dans la barre
            # des tâches au lieu de l'icône Python générique.
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OBSDynamics.App.1")
        except Exception:
            logger.debug("SetCurrentProcessExplicitAppUserModelID indisponible (non-Windows).", exc_info=True)

    def _open_data_folder(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(DATA_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(DATA_DIR)])
        except Exception:
            logger.exception("Impossible d'ouvrir le dossier de données.")

    # -- File d'attente UI thread-safe ------------------------------------ #
    def post_ui(self, callback: Callable[[], None]) -> None:
        self._ui_queue.put(callback)

    def _pump_ui_queue(self) -> None:
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                logger.exception("Erreur dans un callback UI en file d'attente.")
        self.after(50, self._pump_ui_queue)

    # -- Navigation --------------------------------------------------------#
    def _navigate(self, key: str) -> None:
        for view in self.views.values():
            view.grid_forget()
        self.views[key].grid(row=0, column=0, sticky="nsew")
        self.sidebar.set_active(key)

    # -- Démarrage / arrêt de la surveillance ------------------------------#
    def _start(self) -> None:
        self.sidebar.start_btn.configure(state="disabled", text=t("SIDEBAR_BTN_START_PROGRESS"))
        self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_CONNECTING_OBS"))
        threading.Thread(target=self._start_bg, daemon=True).start()

    def _start_bg(self) -> None:
        self.obs_loop.start()
        cfg = self.config_mgr.load()
        client = OBSClient(cfg.host, cfg.port, cfg.password)
        obs_error: Optional[str] = None
        try:
            future = self.obs_loop.run_coro(client.connect())
            future.result(timeout=10)
            self._obs_client = client
        except Exception as exc:
            obs_error = str(exc)
            logger.warning("Connexion OBS échouée, la surveillance démarre quand même sans bascule de scène : %s", exc)
            self._obs_client = None
        self.scan_worker.start(on_update=lambda results: self.post_ui(lambda: self.dashboard.apply_scan_results(results)))
        self.hotkey_manager.start()
        self._start_reconnect_supervisor()
        self.post_ui(lambda: self._on_start_done(obs_error))

    # -- Reconnexion OBS automatique --------------------------------------- #

    def _start_reconnect_supervisor(self) -> None:
        if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
            return
        self._reconnect_stop.clear()
        self._reconnect_thread = threading.Thread(target=self._reconnect_supervisor,
                                                   daemon=True, name="obs-reconnect")
        self._reconnect_thread.start()

    def _stop_reconnect_supervisor(self) -> None:
        self._reconnect_stop.set()
        if self._reconnect_thread is not None:
            self._reconnect_thread.join(timeout=5)
            self._reconnect_thread = None

    def _reconnect_supervisor(self) -> None:
        """Retente la connexion OBS avec un backoff exponentiel plafonné.

        Sans ce superviseur, une coupure d'OBS (fermeture, redémarrage, plantage)
        arrêtait définitivement les bascules de scène : le client restait marqué
        connecté et les requêtes échouaient en silence jusqu'à ce que
        l'utilisateur fasse Stop puis Start à la main.
        """
        base_delay, max_delay = 2.0, 60.0
        attempt = 0
        while not self._reconnect_stop.is_set():
            self._reconnect_stop.wait(base_delay)
            if self._reconnect_stop.is_set() or not self.scan_worker.is_running:
                continue
            client = self._obs_client
            if client is not None and client.is_connected:
                attempt = 0  # connexion saine, on remet le backoff à zéro
                continue

            attempt += 1
            delay = min(max_delay, base_delay * (2 ** min(attempt, 5)))
            logger.info(t("LOG_OBS_RECONNECT_ATTEMPT", attempt=attempt, delay=round(delay, 1)))
            cfg = self.config_mgr.load()
            new_client = OBSClient(cfg.host, cfg.port, cfg.password)
            try:
                self.obs_loop.run_coro(new_client.connect()).result(timeout=10)
            except Exception as exc:
                logger.debug("Reconnexion OBS échouée (tentative %d) : %s", attempt, exc)
                self._reconnect_stop.wait(delay)
                continue

            self._obs_client = new_client
            attempt = 0
            logger.info(t("LOG_OBS_RECONNECTED"))
            self.post_ui(lambda: self.sidebar.status_text.configure(
                text=t("SIDEBAR_STATUS_RUNNING_OBS_RECONNECTED")))

    def _on_start_done(self, obs_error: Optional[str]) -> None:
        self.sidebar.set_running_state(True)
        self.sidebar.start_btn.configure(text=t("SIDEBAR_BTN_START"))
        if obs_error:
            self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_ERROR", error=obs_error[:40]))
        else:
            self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_OK"))

    def _stop(self) -> None:
        self.sidebar.stop_btn.configure(state="disabled", text=t("SIDEBAR_BTN_STOP_PROGRESS"))
        threading.Thread(target=self._stop_bg, daemon=True).start()

    def _on_hotkey(self, state: str) -> None:
        """Appelé depuis le thread pynput : on ne touche à rien d'autre que
        le worker (thread-safe), et l'UI se met à jour au cycle suivant."""
        if not self.scan_worker.is_running:
            return
        self.scan_worker.force_state(state)

    def _stop_bg(self) -> None:
        self.scan_worker.stop()
        self.hotkey_manager.stop()
        self.scan_worker.clear_forced_states()
        self._stop_reconnect_supervisor()
        if self._obs_client is not None:
            try:
                self.obs_loop.run_coro(self._obs_client.disconnect()).result(timeout=5)
            except Exception:
                logger.debug("Erreur déconnexion OBS à l'arrêt (ignorée).", exc_info=True)
            self._obs_client = None
        self.post_ui(self._on_stop_done)

    def _on_stop_done(self) -> None:
        self.sidebar.set_running_state(False)
        self.sidebar.stop_btn.configure(text=t("SIDEBAR_BTN_STOP"))

    def _on_settings_saved(self) -> None:
        """Reconnecte OBS avec les nouveaux paramètres si la surveillance tourne déjà."""
        if not self.scan_worker.is_running:
            return
        threading.Thread(target=self._reconnect_obs_bg, daemon=True).start()

    def _reconnect_obs_bg(self) -> None:
        cfg = self.config_mgr.load()
        if self._obs_client is not None:
            try:
                self.obs_loop.run_coro(self._obs_client.disconnect()).result(timeout=5)
            except Exception:
                logger.debug("Erreur déconnexion OBS lors de la reconnexion.", exc_info=True)
        client = OBSClient(cfg.host, cfg.port, cfg.password)
        try:
            self.obs_loop.run_coro(client.connect()).result(timeout=10)
            self._obs_client = client
            self.post_ui(lambda: self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_RECONNECTED")))
        except Exception as exc:
            self._obs_client = None
            logger.warning("Reconnexion OBS échouée : %s", exc)
            # `exc` est supprimé par Python à la sortie du bloc except ; le lambda
            # étant différé (exécuté ~50ms plus tard par _pump_ui_queue sur le
            # thread UI), il faut figer le message MAINTENANT sous peine de
            # NameError sur variable libre au moment de l'exécution.
            err_text = str(exc)[:40]
            self.post_ui(lambda: self.sidebar.status_text.configure(
                text=t("SIDEBAR_STATUS_RUNNING_OBS_ERROR", error=err_text)))

    def _on_close(self) -> None:
        # Fermeture réentrante : Tk relaie WM_DELETE_WINDOW à chaque clic sur
        # la croix, et rien n'empêche un second passage pendant l'arrêt (qui
        # prend quelques secondes : déconnexion OBS, arrêt des threads). Le
        # deuxième `self.destroy()` lèverait alors « application has been
        # destroyed ». Une garde ici vaut mieux qu'un try/except par appelant.
        if getattr(self, "_closing", False):
            return
        self._closing = True

        # Désinscrire AVANT de détruire les widgets : i18n garde ses listeners
        # dans une liste de module, qui survit à la fenêtre. Sans ça, chaque
        # App fermée laisse un callback qui rappellera refresh_labels() sur
        # des widgets détruits au prochain changement de langue.
        i18n.off_change(self._on_lang_changed)
        self.scan_worker.stop()
        self.hotkey_manager.stop()
        self._combo_listener.stop()
        self.chat_hub.stop_all()
        self.overlay.stop()
        self._stop_reconnect_supervisor()
        self.cover_service.stop()
        if self._obs_client is not None:
            try:
                self.obs_loop.run_coro(self._obs_client.disconnect()).result(timeout=3)
            except Exception:
                pass
        self.obs_loop.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
