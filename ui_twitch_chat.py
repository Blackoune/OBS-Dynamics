"""Vue Chat Twitch : carte de plateforme, connexion et lien overlay."""
from __future__ import annotations

from tkinter import messagebox
from typing import Callable

from app_paths import logger
from i18n import t
from twitch_chat import (PLATFORMS as CHAT_PLATFORMS,
                         PLATFORM_COLORS as CHAT_COLORS, ChatHub,
                         TwitchChatStore, Status as ChatStatus)
from overlay_server import OverlayServer
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER,
                       COL_BORDER_ACCENT, COL_CARD, COL_CARD_HOVER, COL_GREEN,
                       COL_RED, COL_TEXT, COL_TEXT_MUTED, COL_YELLOW, ctk, font)


# ============================================================================
# VUE : MULTI STREAM
# ============================================================================
# Correspondance état de connecteur -> couleur de pastille. Le vert/rouge est
# celui du reste de l'application ; « en cours » et « indisponible » partagent
# le jaune parce qu'aucun des deux n'est ni une réussite ni une panne.
_CHAT_STATUS_COLORS = {
    ChatStatus.CONNECTED: COL_GREEN,
    ChatStatus.ERROR: COL_RED,
    ChatStatus.CONNECTING: COL_YELLOW,
    ChatStatus.NEEDS_CONFIG: COL_TEXT_MUTED,
    ChatStatus.DISCONNECTED: COL_TEXT_MUTED,
    # Vert : le compte est bel et bien connecté. Le jaune de « connexion en
    # cours » a été lu comme une panne et a conduit à défaire une connexion
    # qui fonctionnait.
}

_CHAT_STATUS_KEYS = {
    ChatStatus.CONNECTED: "TWITCH_CHAT_STATUS_CONNECTED",
    ChatStatus.ERROR: "TWITCH_CHAT_STATUS_ERROR",
    ChatStatus.CONNECTING: "TWITCH_CHAT_STATUS_CONNECTING",
    ChatStatus.NEEDS_CONFIG: "TWITCH_CHAT_STATUS_NEEDS_CONFIG",
    ChatStatus.DISCONNECTED: "TWITCH_CHAT_STATUS_DISCONNECTED",
}


#: Plateformes désignées par un nom de chaîne.
CHANNEL_PLATFORMS = ("twitch",)


class ConnectionDialog(ctk.CTkToplevel):
    """Fenêtre de configuration d'une plateforme.

    Twitch se lit en IRC anonyme : le nom de la chaîne suffit, il n'y a rien
    d'autre à demander. Cette fenêtre est donc volontairement minuscule.
    """

    def __init__(self, master, platform: str, channel: str,
                 on_validate: Callable[[str], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_BG, **kwargs)
        self._platform = platform
        self._on_validate = on_validate
        suffix = platform.upper()

        self.title(t("TWITCH_CHAT_DIALOG_TITLE", platform=t(f"TWITCH_CHAT_PLATFORM_{suffix}")))
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14,
                            border_width=1, border_color=COL_BORDER)
        card.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text=t(f"TWITCH_CHAT_PLATFORM_{suffix}"), font=font(16, "bold"),
                     text_color=CHAT_COLORS.get(platform, COL_ACCENT),
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))

        ctk.CTkLabel(card, text=t(f"TWITCH_CHAT_DIALOG_CHANNEL_{suffix}"), font=font(12),
                     text_color=COL_TEXT_MUTED,
                     anchor="w").grid(row=1, column=0, sticky="ew", padx=20)
        self._channel_var = ctk.StringVar(value=channel)
        entry = ctk.CTkEntry(card, textvariable=self._channel_var, height=36,
                             fg_color=COL_BG, border_color=COL_BORDER)
        entry.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 12))

        ctk.CTkLabel(card, text=t(f"TWITCH_CHAT_DIALOG_HINT_{suffix}"), font=font(11),
                     text_color=COL_TEXT_MUTED, wraplength=440, justify="left",
                     anchor="w").grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 16))

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 18))
        ctk.CTkButton(buttons, text=t("TWITCH_CHAT_DIALOG_BTN_VALIDATE"), width=130, height=38,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                      font=font(13, "bold"), corner_radius=9,
                      command=self._validate).pack(side="left")
        ctk.CTkButton(buttons, text=t("TWITCH_CHAT_DIALOG_BTN_CLEAR"), width=110, height=38,
                      fg_color="transparent", hover_color=COL_CARD_HOVER,
                      border_width=1, border_color=COL_BORDER, text_color=COL_TEXT_MUTED,
                      font=font(12), corner_radius=9,
                      command=self._clear).pack(side="left", padx=8)
        ctk.CTkButton(buttons, text=t("TWITCH_CHAT_DIALOG_BTN_CANCEL"), width=110, height=38,
                      fg_color="transparent", hover_color=COL_CARD_HOVER,
                      border_width=1, border_color=COL_BORDER, text_color=COL_TEXT_MUTED,
                      font=font(12), corner_radius=9,
                      command=self.destroy).pack(side="left")

        self.bind("<Return>", lambda _e: self._validate())
        self.bind("<Escape>", lambda _e: self.destroy())
        # grab_set() après update_idletasks : sur Windows, capturer les
        # événements avant que la fenêtre soit réellement mappée laisse le
        # dialogue derrière la fenêtre principale, apparemment figé.
        self.update_idletasks()
        self.grab_set()
        entry.focus_set()

    def _validate(self) -> None:
        # Le # d'un salon IRC est une décoration d'affichage : le garder
        # ferait échouer le JOIN.
        self._on_validate(self._channel_var.get().strip().lstrip("@#"))
        self.destroy()

    def _clear(self) -> None:
        self._on_validate("")
        self.destroy()


class TwitchChatCard(ctk.CTkFrame):
    """Carte d'une plateforme : interrupteur d'affichage, bouton de connexion
    et pastille d'état."""

    # Point plein coloré par la plateforme : même glyphe que les pastilles
    # d'état du reste de l'interface, et il suit la palette au lieu d'imposer
    # les couleurs figées d'un emoji.
    DOT = "●"

    def __init__(self, master, platform: str,
                 on_toggle: Callable[[str, bool], None],
                 on_connect: Callable[[str], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_CARD, corner_radius=14,
                         border_width=1, border_color=COL_BORDER, **kwargs)
        self._platform = platform
        self._on_toggle = on_toggle
        self.grid_columnconfigure(0, weight=1)
        suffix = platform.upper()

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 10))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(header, text=self.DOT, font=font(15), width=24,
                     text_color=CHAT_COLORS.get(platform, COL_TEXT)).grid(
                         row=0, column=0, sticky="w")
        self._name_lbl = ctk.CTkLabel(header, text=t(f"TWITCH_CHAT_PLATFORM_{suffix}"),
                                      font=font(15, "bold"),
                                      text_color=CHAT_COLORS.get(platform, COL_TEXT),
                                      anchor="w")
        self._name_lbl.grid(row=0, column=1, sticky="w")

        self._switch_var = ctk.BooleanVar(value=True)
        self._switch = ctk.CTkSwitch(header, text="", variable=self._switch_var, width=44,
                                     progress_color=COL_ACCENT, button_color=COL_TEXT,
                                     command=self._toggled)
        self._switch.grid(row=0, column=2, sticky="e")

        self._switch_lbl = ctk.CTkLabel(self, text=t("TWITCH_CHAT_SWITCH_LABEL"), font=font(11),
                                        text_color=COL_TEXT_MUTED, anchor="w")
        self._switch_lbl.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))

        self._connect_btn = ctk.CTkButton(
            self, text=t("TWITCH_CHAT_BTN_CONNECT"), height=40, corner_radius=9,
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
            font=font(13, "bold"), command=lambda: on_connect(platform))
        self._connect_btn.grid(row=2, column=0, sticky="ew", padx=18)

        status_row = ctk.CTkFrame(self, fg_color="transparent")
        status_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(10, 4))
        self._dot = ctk.CTkLabel(status_row, text="●", font=font(12),
                                 text_color=COL_TEXT_MUTED, width=16)
        self._dot.pack(side="left")
        self._status_lbl = ctk.CTkLabel(status_row, text=t("TWITCH_CHAT_STATUS_DISCONNECTED"),
                                        font=font(11), text_color=COL_TEXT_MUTED, anchor="w")
        self._status_lbl.pack(side="left", padx=(4, 0))

        self._detail_lbl = ctk.CTkLabel(self, text="", font=font(10),
                                        text_color=COL_TEXT_MUTED, wraplength=300,
                                        justify="left", anchor="w")
        self._detail_lbl.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 16))

        self._status = ChatStatus.DISCONNECTED
        self._detail = ""

    def _toggled(self) -> None:
        self._on_toggle(self._platform, bool(self._switch_var.get()))

    def set_enabled(self, enabled: bool) -> None:
        """Positionne l'interrupteur SANS déclencher le callback (sinon
        charger la configuration réécrirait immédiatement le fichier)."""
        self._switch_var.set(enabled)

    def set_status(self, status: str, detail: str) -> None:
        self._status, self._detail = status, detail
        self.refresh_labels()

    def refresh_labels(self) -> None:
        suffix = self._platform.upper()
        self._name_lbl.configure(text=t(f"TWITCH_CHAT_PLATFORM_{suffix}"))
        self._switch_lbl.configure(text=t("TWITCH_CHAT_SWITCH_LABEL"))
        self._connect_btn.configure(text=t("TWITCH_CHAT_BTN_CONNECT"))
        self._dot.configure(text_color=_CHAT_STATUS_COLORS.get(self._status, COL_TEXT_MUTED))
        key = _CHAT_STATUS_KEYS.get(self._status, "TWITCH_CHAT_STATUS_DISCONNECTED")
        # Seul l'état « connecté » consomme le détail comme nom de chaîne.
        text = (t(key, account=self._detail or "?")
                if self._status == ChatStatus.CONNECTED else t(key))
        self._status_lbl.configure(text=text)
        self._detail_lbl.configure(text="", text_color=COL_TEXT_MUTED)


class TwitchChatView(ctk.CTkFrame):
    """Onglet Multi Stream : une carte par plateforme, puis le lien overlay."""

    COLUMNS = 2

    def __init__(self, master, store: TwitchChatStore, hub: ChatHub,
                 overlay: OverlayServer, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._store = store
        self._hub = hub
        self._overlay = overlay
        self._cards: dict[str, TwitchChatCard] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        self._title_lbl = ctk.CTkLabel(header, text=t("TWITCH_CHAT_TITLE"),
                                       font=font(22, "bold"), text_color=COL_TEXT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(header, text=t("TWITCH_CHAT_SUBTITLE"), font=font(11),
                                          text_color=COL_TEXT_MUTED)
        self._subtitle_lbl.pack(anchor="w", pady=(2, 0))

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=1, column=0, sticky="nsew", padx=22, pady=(4, 10))
        for column in range(self.COLUMNS):
            self.scroll.grid_columnconfigure(column, weight=1, uniform="chat")

        for index, platform in enumerate(CHAT_PLATFORMS):
            card = TwitchChatCard(self.scroll, platform,
                                   on_toggle=self._on_toggle, on_connect=self._open_dialog)
            card.grid(row=index // self.COLUMNS, column=index % self.COLUMNS,
                      sticky="nsew", padx=6, pady=6)
            self._cards[platform] = card

        # -(-n // c) = division entière arrondie au-dessus : la section du lien
        # se pose sous la dernière RANGÉE, même quand la dernière est à moitié
        # remplie. Réutiliser l'index de boucle marcherait par accident.
        self._build_overlay_section(row=-(-len(CHAT_PLATFORMS) // self.COLUMNS))
        self.reload()

    def _build_overlay_section(self, row: int) -> None:
        card = ctk.CTkFrame(self.scroll, fg_color=COL_CARD, corner_radius=14,
                            border_width=1, border_color=COL_BORDER_ACCENT)
        card.grid(row=row, column=0, columnspan=self.COLUMNS, sticky="ew", padx=6, pady=(14, 6))
        card.grid_columnconfigure(0, weight=1)

        self._overlay_title_lbl = ctk.CTkLabel(card, text=t("TWITCH_CHAT_OVERLAY_SECTION"),
                                               font=font(14, "bold"), text_color=COL_TEXT,
                                               anchor="w")
        self._overlay_title_lbl.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))

        self._url_var = ctk.StringVar()
        # readonly plutôt que disabled : le texte reste sélectionnable à la
        # souris (donc copiable à la main) sans pouvoir être édité.
        ctk.CTkEntry(card, textvariable=self._url_var, height=36, state="readonly",
                     fg_color=COL_BG, border_color=COL_BORDER).grid(
            row=1, column=0, sticky="ew", padx=20)

        buttons = ctk.CTkFrame(card, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", padx=20, pady=12)
        self._copy_btn = ctk.CTkButton(buttons, text=t("TWITCH_CHAT_BTN_COPY"), width=150,
                                       height=36, fg_color=COL_ACCENT,
                                       hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                                       font=font(12, "bold"), corner_radius=9,
                                       command=self._copy_url)
        self._copy_btn.pack(side="left")
        self._regen_btn = ctk.CTkButton(buttons, text=t("TWITCH_CHAT_BTN_REGENERATE"), width=170,
                                        height=36, fg_color="transparent",
                                        hover_color=COL_CARD_HOVER, border_width=1,
                                        border_color=COL_BORDER, text_color=COL_TEXT_MUTED,
                                        font=font(12), corner_radius=9,
                                        command=self._regenerate)
        self._regen_btn.pack(side="left", padx=8)

        self._note_lbl = ctk.CTkLabel(card, text=t("TWITCH_CHAT_OVERLAY_NOTE"), font=font(10),
                                      text_color=COL_TEXT_MUTED, wraplength=560,
                                      justify="left", anchor="w")
        self._note_lbl.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 18))

    # -- Données ----------------------------------------------------------- #

    def reload(self) -> None:
        """Recharge interrupteurs, états et URL depuis le disque."""
        cfg = self._store.load()
        for platform, card in self._cards.items():
            card.set_enabled(cfg.platform(platform).enabled)
            status, detail = self._hub.status(platform)
            card.set_status(status, detail)
        self._url_var.set(self._overlay.chat_url())

    def apply_status(self, platform: str, status: str, detail: str) -> None:
        """Appelée sur le thread UI quand un connecteur change d'état."""
        card = self._cards.get(platform)
        if card is not None:
            card.set_status(status, detail)

    def _on_toggle(self, platform: str, enabled: bool) -> None:
        pcfg = self._store.load().platform(platform)
        pcfg.enabled = enabled
        self._store.set_platform(platform, pcfg)
        # Filtre appliqué à chaud, sans toucher au connecteur : la session
        # reste ouverte, réafficher la plateforme est instantané.
        self._hub.set_enabled(platform, enabled)

    def _open_dialog(self, platform: str) -> None:
        cfg = self._store.load()
        ConnectionDialog(self, platform, cfg.platform(platform).channel,
                         on_validate=lambda channel: self._save_connection(platform, channel))

    def _save_connection(self, platform: str, channel: str) -> None:
        cfg = self._store.load()
        pcfg = cfg.platform(platform)
        pcfg.channel = channel
        # Le salon suivi est la seule identité que l'application connaisse.
        pcfg.account = channel
        cfg.platforms[platform] = pcfg
        self._store.save(cfg)
        self._hub.apply_config(cfg)
        self.reload()

    # -- Lien overlay ------------------------------------------------------ #

    def _copy_url(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._url_var.get())
            self._copy_btn.configure(text=t("TWITCH_CHAT_BTN_COPIED"))
            self.after(1500, lambda: self._copy_btn.configure(text=t("TWITCH_CHAT_BTN_COPY")))
        except Exception:
            logger.debug("Copie du lien multi-chat échouée.", exc_info=True)

    def _regenerate(self) -> None:
        if not messagebox.askyesno(t("CONFIRM_DIALOG_TITLE"), t("TWITCH_CHAT_CONFIRM_REGENERATE")):
            return
        self._store.regenerate_token()
        self._url_var.set(self._overlay.chat_url())

    def refresh_labels(self) -> None:
        self._title_lbl.configure(text=t("TWITCH_CHAT_TITLE"))
        self._subtitle_lbl.configure(text=t("TWITCH_CHAT_SUBTITLE"))
        self._overlay_title_lbl.configure(text=t("TWITCH_CHAT_OVERLAY_SECTION"))
        self._copy_btn.configure(text=t("TWITCH_CHAT_BTN_COPY"))
        self._regen_btn.configure(text=t("TWITCH_CHAT_BTN_REGENERATE"))
        self._note_lbl.configure(text=t("TWITCH_CHAT_OVERLAY_NOTE"))
        for card in self._cards.values():
            card.refresh_labels()
