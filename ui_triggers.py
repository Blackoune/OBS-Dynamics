"""Vue Raccourcis & Overlays : une combinaison de touches, un média dans OBS."""
from __future__ import annotations

from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional

from app_paths import logger
from hotkeys import ComboRecorder, format_combo
from i18n import t
from overlay_server import OverlayServer
from triggers import (DURATION_PRESETS_MS, MEDIA_EXTENSIONS, MEDIA_TYPES,
                      TriggerRule, TriggerStore)
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_ACCENT_SOFT, COL_BG,
                       COL_BORDER, COL_BORDER_ACCENT, COL_CARD, COL_RED,
                       COL_RING_ACTIVE, COL_TEXT, COL_TEXT_MUTED, COL_YELLOW,
                       _try_enable_dnd, ctk, font)


# ============================================================================
# VUE : RACCOURCIS & OVERLAYS
# ============================================================================
class TriggerRow(ctk.CTkFrame):
    """Une règle : capture du raccourci, type de média, fichier, et juste
    en dessous — visuellement rattachée — l'URL de la source navigateur."""

    def __init__(self, master, rule: TriggerRule, store: TriggerStore,
                 overlay_url: str, on_changed: Callable[[], None],
                 on_delete: Callable[[TriggerRule], None],
                 post_ui: Callable[[Callable[[], None]], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._rule = rule
        self._store = store
        self._on_changed = on_changed
        self._on_delete = on_delete
        self._post_ui = post_ui
        self._recorder: Optional[ComboRecorder] = None
        self.grid_columnconfigure(0, weight=1)

        # --- Bloc principal de configuration --------------------------------
        # corner_radius asymétrique impossible en CTk : on colle deux cartes
        # l'une contre l'autre (pady=0) pour l'effet « rattaché ».
        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=12,
                             border_width=1, border_color=COL_BORDER)
        card.grid(row=0, column=0, sticky="ew")
        card.grid_columnconfigure(2, weight=1)

        self._hotkey_btn = ctk.CTkButton(
            card, text=self._hotkey_label(), width=210, height=38,
            fg_color=COL_BG, hover_color=COL_BORDER, border_width=1,
            border_color=COL_BORDER_ACCENT if rule.hotkey else COL_BORDER,
            font=font(12, "bold" if rule.hotkey else "normal"),
            command=self._start_capture)
        self._hotkey_btn.grid(row=0, column=0, padx=(14, 8), pady=14)

        self._type_var = ctk.StringVar(value=rule.media_type)
        self._type_menu = ctk.CTkOptionMenu(
            card, values=[t(f"TRIGGER_MEDIA_{k.upper()}") for k in MEDIA_TYPES],
            width=120, height=38, fg_color=COL_BG, button_color=COL_ACCENT,
            button_hover_color=COL_ACCENT_HOVER, font=font(12),
            command=self._on_type_changed)
        self._type_menu.set(t(f"TRIGGER_MEDIA_{rule.media_type.upper()}"))
        self._type_menu.grid(row=0, column=1, padx=8, pady=14)

        self._drop_zone = ctk.CTkButton(
            card, text=self._media_label(), height=38, anchor="w",
            fg_color=COL_BG, hover_color=COL_BORDER, border_width=1,
            border_color=COL_BORDER, font=font(11),
            text_color=COL_TEXT if rule.media_path else COL_TEXT_MUTED,
            command=self._browse_media)
        self._drop_zone.grid(row=0, column=2, sticky="ew", padx=8, pady=14)

        self._duration_menu = ctk.CTkOptionMenu(
            card, values=[self._duration_label(d) for d in DURATION_PRESETS_MS],
            width=110, height=38, fg_color=COL_BG, button_color=COL_ACCENT,
            button_hover_color=COL_ACCENT_HOVER, font=font(12),
            command=self._on_duration_changed)
        self._duration_menu.set(self._duration_label(rule.duration_ms))
        self._duration_menu.grid(row=0, column=3, padx=8, pady=14)

        self._delete_btn = ctk.CTkButton(
            card, text=t("TRIGGER_BTN_DELETE"), width=84, height=38,
            fg_color="#3A1420", hover_color=COL_RED, font=font(12),
            command=lambda: self._on_delete(self._rule))
        self._delete_btn.grid(row=0, column=4, padx=(8, 14), pady=14)

        self._dnd_active = _try_enable_dnd(self._drop_zone, self._on_files_dropped)

        # --- Bandeau URL, collé sous la carte -------------------------------
        url_bar = ctk.CTkFrame(self, fg_color=COL_BG, corner_radius=10,
                                border_width=1, border_color=COL_BORDER)
        url_bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 0))
        url_bar.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(url_bar, text=t("TRIGGER_URL_LABEL"), font=font(10),
                     text_color=COL_TEXT_MUTED).grid(row=0, column=0, padx=(12, 8), pady=8)

        self._url_var = ctk.StringVar(value=overlay_url)
        url_entry = ctk.CTkEntry(url_bar, textvariable=self._url_var, height=28,
                                  fg_color=COL_CARD, border_width=0, font=font(10))
        url_entry.grid(row=0, column=1, sticky="ew", pady=8)
        # Lecture seule mais sélectionnable : l'utilisateur doit pouvoir
        # copier à la main si le presse-papiers est indisponible.
        url_entry.configure(state="readonly")

        self._copy_btn = ctk.CTkButton(
            url_bar, text=t("TRIGGER_BTN_COPY"), width=90, height=28,
            fg_color=COL_ACCENT_SOFT, hover_color=COL_ACCENT_HOVER,
            font=font(11), command=self._copy_url)
        self._copy_btn.grid(row=0, column=2, padx=(8, 12), pady=8)

        self._status_lbl = ctk.CTkLabel(self, text=self._status_text(), font=font(10),
                                         text_color=self._status_color(), anchor="w")
        self._status_lbl.grid(row=2, column=0, sticky="w", padx=20, pady=(4, 0))

    # -- Libellés ---------------------------------------------------------- #

    def _hotkey_label(self) -> str:
        if self._rule.hotkey:
            return format_combo(self._rule.hotkey)
        return t("TRIGGER_HOTKEY_PLACEHOLDER")

    def _media_label(self) -> str:
        if self._rule.media_path:
            return Path(self._rule.media_path).name
        return t("TRIGGER_MEDIA_DROP_HINT") if getattr(self, "_dnd_active", False) \
            else t("TRIGGER_MEDIA_BROWSE_HINT")

    @staticmethod
    def _duration_label(ms: int) -> str:
        if ms <= 0:
            return t("TRIGGER_DURATION_HOLD")
        if ms < 1000:
            return t("TRIGGER_DURATION_MS", ms=ms)
        # Retire le .0 des durées entières : "2 s" plutôt que "2.0 s".
        seconds = ms / 1000
        return t("TRIGGER_DURATION_S", s=int(seconds) if seconds.is_integer() else seconds)

    def _status_text(self) -> str:
        if not self._rule.is_complete:
            return t("TRIGGER_STATUS_INCOMPLETE")
        if self._rule.duration_ms <= 0:
            return t("TRIGGER_DURATION_HOLD_HINT")
        return t("TRIGGER_STATUS_READY")

    def _status_color(self) -> str:
        """Le point plein en tête du libellé est neutre : c'est la couleur du
        texte qui distingue « prêt » (vert) de « incomplet » (jaune). Avant, ce
        vert venait de l'emoji de coche ; le label, lui, restait gris."""
        if not self._rule.is_complete:
            return COL_YELLOW
        if self._rule.duration_ms <= 0:
            return COL_TEXT_MUTED      # simple rappel, pas un état
        return COL_RING_ACTIVE         # même vert que les cartes actives

    def _on_duration_changed(self, _label: str) -> None:
        chosen = self._duration_menu.get()
        for ms in DURATION_PRESETS_MS:
            if self._duration_label(ms) == chosen:
                self._rule.duration_ms = ms
                break
        self._store.upsert(self._rule)
        self.refresh()
        self._on_changed()

    def refresh(self) -> None:
        self._hotkey_btn.configure(
            text=self._hotkey_label(),
            border_color=COL_BORDER_ACCENT if self._rule.hotkey else COL_BORDER,
            font=font(12, "bold" if self._rule.hotkey else "normal"))
        self._drop_zone.configure(
            text=self._media_label(),
            text_color=COL_TEXT if self._rule.media_path else COL_TEXT_MUTED)
        self._status_lbl.configure(text=self._status_text(),
                                   text_color=self._status_color())

    # -- Capture du raccourci ---------------------------------------------- #

    def _start_capture(self) -> None:
        if self._recorder is not None:
            return
        self._hotkey_btn.configure(text=t("TRIGGER_HOTKEY_LISTENING"),
                                    border_color=COL_ACCENT, font=font(12))
        self._recorder = ComboRecorder(on_captured=self._on_combo_captured)
        if not self._recorder.start():
            self._recorder = None
            self._hotkey_btn.configure(text=t("TRIGGER_HOTKEY_NO_PYNPUT"),
                                        border_color=COL_RED)

    def _on_combo_captured(self, combo: str) -> None:
        # Appelé depuis le thread pynput : on repasse par la file UI.
        self._post_ui(lambda: self._apply_combo(combo))

    def _apply_combo(self, combo: str) -> None:
        self._recorder = None
        if not combo:                       # Échap : on garde l'existant
            self.refresh()
            return
        conflict = self._store.conflicting(combo, exclude_id=self._rule.id)
        if conflict is not None:
            # refresh() AVANT le message : il réécrit texte et couleur de la
            # ligne d'état, donc placé après il effaçait l'avertissement.
            self.refresh()
            self._status_lbl.configure(
                text=t("TRIGGER_ERR_HOTKEY_TAKEN", combo=format_combo(combo)),
                text_color=COL_RED)
            return
        self._rule.hotkey = combo
        self._store.upsert(self._rule)
        self.refresh()          # remet aussi la couleur d'état
        self._on_changed()

    # -- Média -------------------------------------------------------------- #

    def _on_type_changed(self, _label: str) -> None:
        label_to_key = {t(f"TRIGGER_MEDIA_{k.upper()}"): k for k in MEDIA_TYPES}
        self._rule.media_type = label_to_key.get(self._type_menu.get(), "image")
        self._store.upsert(self._rule)
        self.refresh()
        self._on_changed()

    def _browse_media(self) -> None:
        exts = MEDIA_EXTENSIONS.get(self._rule.media_type, ())
        pattern = " ".join(f"*{e}" for e in exts)
        path = filedialog.askopenfilename(
            title=t("TRIGGER_FILEDIALOG_TITLE"),
            filetypes=[(t(f"TRIGGER_MEDIA_{self._rule.media_type.upper()}"), pattern),
                       (t("TRIGGER_FILEDIALOG_ALL"), "*.*")])
        if path:
            self._set_media(path)

    def _on_files_dropped(self, files: list[str]) -> None:
        if files:
            self._post_ui(lambda: self._set_media(files[0]))

    def _set_media(self, path: str) -> None:
        suffix = Path(path).suffix.lower()
        expected = MEDIA_EXTENSIONS.get(self._rule.media_type, ())
        if expected and suffix not in expected:
            # On avertit sans bloquer : l'utilisateur sait parfois mieux que
            # la table d'extensions (conteneurs exotiques, fichiers renommés).
            self._status_lbl.configure(
                text=t("TRIGGER_WARN_EXTENSION", ext=suffix or "?",
                       kind=t(f"TRIGGER_MEDIA_{self._rule.media_type.upper()}")),
                text_color=COL_YELLOW)
        else:
            self._status_lbl.configure(text_color=COL_TEXT_MUTED)
        self._rule.media_path = path
        self._store.upsert(self._rule)
        self.refresh()
        self._on_changed()

    # -- URL ---------------------------------------------------------------- #

    def _copy_url(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self._url_var.get())
            self._copy_btn.configure(text=t("TRIGGER_BTN_COPIED"))
            self.after(1500, lambda: self._copy_btn.configure(text=t("TRIGGER_BTN_COPY")))
        except Exception:
            logger.debug("Copie dans le presse-papiers échouée.", exc_info=True)

    def destroy(self) -> None:
        if self._recorder is not None:
            self._recorder.cancel()   # ne pas laisser un listener clavier orphelin
            self._recorder = None
        super().destroy()


class TriggersView(ctk.CTkFrame):
    def __init__(self, master, store: TriggerStore, overlay: OverlayServer,
                 post_ui: Callable[[Callable[[], None]], None],
                 on_rules_changed: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._store = store
        self._overlay = overlay
        self._post_ui = post_ui
        self._on_rules_changed = on_rules_changed
        self._rows: list[TriggerRow] = []

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        header.grid_columnconfigure(0, weight=1)

        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.grid(row=0, column=0, sticky="w")
        self._title_lbl = ctk.CTkLabel(title_col, text=t("TRIGGERS_TITLE"),
                                        font=font(22, "bold"), text_color=COL_TEXT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(title_col, text="", font=font(11),
                                           text_color=COL_TEXT_MUTED)
        self._subtitle_lbl.pack(anchor="w", pady=(2, 0))

        self._add_btn = ctk.CTkButton(
            header, text=t("TRIGGERS_BTN_ADD"), width=200, height=36,
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER, text_color="#0F0C1B",
            font=font(12, "bold"), corner_radius=9, command=self._add_rule)
        self._add_btn.grid(row=0, column=1, sticky="e")

        self._server_lbl = ctk.CTkLabel(self, text="", font=font(10),
                                         text_color=COL_TEXT_MUTED, anchor="w")
        self._server_lbl.grid(row=1, column=0, sticky="w", padx=30)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=22, pady=10)
        self.scroll.grid_columnconfigure(0, weight=1)

        self.render()

    def _server_text(self) -> str:
        if not self._overlay.is_running:
            return t("TRIGGERS_SERVER_STOPPED")
        if self._overlay.using_fallback_port:
            # Silence coupable si on ne le dit pas : les URL déjà collées dans
            # OBS pointent vers l'ancien port et ne répondront plus.
            return t("TRIGGERS_SERVER_FALLBACK_PORT",
                     url=self._overlay.base_url(),
                     wanted=self._overlay.requested_port)
        return t("TRIGGERS_SERVER_RUNNING", url=self._overlay.base_url())

    def _server_color(self) -> str:
        if not self._overlay.is_running or self._overlay.using_fallback_port:
            return COL_YELLOW
        return COL_TEXT_MUTED

    def render(self) -> None:
        for row in self._rows:
            row.destroy()
        self._rows.clear()
        for widget in self.scroll.winfo_children():
            widget.destroy()

        rules = self._store.load()
        self._subtitle_lbl.configure(text=t("TRIGGERS_SUBTITLE", count=len(rules)))
        self._server_lbl.configure(text=self._server_text(), text_color=self._server_color())

        # Un seul bouton d'ajout visible à la fois : celui de l'état vide tant
        # qu'aucune règle n'existe, celui de l'en-tête ensuite. Afficher les
        # deux en même temps était redondant.
        if rules:
            self._add_btn.grid()
        else:
            self._add_btn.grid_remove()

        if not rules:
            self._render_empty_state()
            return

        for i, rule in enumerate(rules):
            row = TriggerRow(self.scroll, rule=rule, store=self._store,
                              overlay_url=self._overlay.overlay_url(rule.id),
                              on_changed=self._on_rules_changed,
                              on_delete=self._delete_rule, post_ui=self._post_ui)
            row.grid(row=i, column=0, sticky="ew", pady=(0, 18))
            self._rows.append(row)

    def _render_empty_state(self) -> None:
        # `box` est centré dans une cellule qui occupe toute la largeur, et
        # chaque enfant est packé avec fill="x" + un label ancré au centre :
        # sinon un enfant étroit se cale sur la largeur du bloc le plus large
        # au lieu du centre géométrique de la vue. Le gros glyphe de clavier
        # qui ouvrait ce bloc est parti avec les autres pictogrammes : contour
        # fin, il jurait avec les glyphes pleins gardés ailleurs.
        box = ctk.CTkFrame(self.scroll, fg_color="transparent")
        box.grid(row=0, column=0, pady=60)

        ctk.CTkLabel(box, text=t("TRIGGERS_EMPTY_TITLE"), font=font(15, "bold"),
                     text_color=COL_TEXT, anchor="center",
                     justify="center").pack(fill="x", pady=(12, 4))
        ctk.CTkLabel(box, text=t("TRIGGERS_EMPTY_HINT"), font=font(11),
                     text_color=COL_TEXT_MUTED, justify="center", anchor="center",
                     wraplength=460).pack(fill="x", pady=(0, 18))
        ctk.CTkButton(box, text=t("TRIGGERS_BTN_ADD"), width=220, height=42,
                       fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                       text_color="#0F0C1B", font=font(13, "bold"),
                       corner_radius=9, command=self._add_rule).pack(anchor="center")

    def _add_rule(self) -> None:
        self._store.add()          # insérée en tête de liste
        self.render()
        self._on_rules_changed()

    def _delete_rule(self, rule: TriggerRule) -> None:
        if messagebox.askyesno(t("CONFIRM_DIALOG_TITLE"), t("TRIGGER_CONFIRM_DELETE")):
            self._store.delete(rule.id)
            self.render()
            self._on_rules_changed()

    def refresh_labels(self) -> None:
        self._title_lbl.configure(text=t("TRIGGERS_TITLE"))
        self._add_btn.configure(text=t("TRIGGERS_BTN_ADD"))
        self._server_lbl.configure(text=self._server_text(), text_color=self._server_color())
        self.render()
