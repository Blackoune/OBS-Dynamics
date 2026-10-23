"""Onglet Sous-titres : source audio, langue, transcription en direct.

Lot 1 : la capture et le moteur. Le widget OBS et les sous-titres CC
viendront s'abonner au même flux d'évènements (`_on_text`).

La transcription s'affiche comme à l'antenne : la phrase en cours en gris,
réécrite à chaque bloc de 100 ms, puis figée en blanc à la première pause.
"""
from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import replace
from tkinter import messagebox
from typing import Any, Callable, Optional

import subtitles_audio as audio
import subtitles_engine as engine
from i18n import t
from subtitles_overlay import SubtitlesHub
from ui_subtitles_style import SubtitleStyleDialog
from ui_subtitles_words import WordBankDialog
import subtitles_words as sw
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER, COL_CARD,
                       COL_GREEN, COL_RED, COL_TEXT, COL_TEXT_MUTED, COL_YELLOW,
                       SmoothScroll, ctk, font)
import subtitles_cc as cc
from app_paths import logger

# Noms des langues dans leur propre langue : on les lit sans traduction.
LANG_NAMES = {"fr": "Français", "en": "English", "de": "Deutsch", "es": "Español"}

PRECISION_LABELS = {engine.PRECISION_FAST: "SUBS_PREC_FAST",
                    engine.PRECISION_ACCURATE: "SUBS_PREC_ACCURATE"}

#: Interrupteurs de la carte CC : (champ de CaptionSettings, clé i18n).
CC_FLAGS = (("center", "SUBS_CC_CENTER"), ("uppercase", "SUBS_CC_UPPERCASE"),
            ("filter_profanity", "SUBS_CC_PROFANITY"), ("keep_accents", "SUBS_CC_ACCENTS"))

#: Noms propres et licence : identiques dans toutes les langues.
CREDIT_REFINER = " · NVIDIA Parakeet TDT 0.6B v3 (CC-BY-4.0)."

MAX_LINES = 300            # au-delà, les plus anciennes lignes sont retirées
# En dessous : pas de son du tout. Le souffle d'un micro au repos mesure déjà
# ~0,002 à 0,003 (relevé sur un fifine SC3) ; une sortie muette, 0 exactement.
SILENCE_LEVEL = 0.0005
SILENCE_WARN_S = 5.0

_STATE_TEXT = {
    engine.ST_STOPPED: "SUBS_ST_STOPPED",
    engine.ST_LOADING: "SUBS_ST_LOADING",
    engine.ST_LISTENING: "SUBS_ST_LISTENING",
    engine.ST_WAITING_DEVICE: "SUBS_ST_WAITING_DEVICE",
    engine.ST_ERROR: "SUBS_ST_ERROR",
}
_STATE_COLOR = {
    engine.ST_STOPPED: COL_TEXT_MUTED,
    engine.ST_LOADING: COL_YELLOW,
    engine.ST_LISTENING: COL_GREEN,
    engine.ST_WAITING_DEVICE: COL_YELLOW,
    engine.ST_ERROR: COL_RED,
}


def _kind_label(kind: str) -> str:
    return t("SUBS_KIND_MIC") if kind == audio.KIND_MIC else t("SUBS_KIND_LOOPBACK")


def _mb(n: int) -> str:
    return str(round(n / 1_000_000))


class SubtitlesView(ctk.CTkFrame):

    def __init__(self, master, post_ui: Callable[[Callable[[], None]], None],
                 settings_path=engine.SETTINGS_PATH, models_root=engine.MODELS_DIR,
                 hub: Optional[SubtitlesHub] = None, overlay: Optional[Any] = None,
                 words_path=sw.WORDS_PATH, cc_path=cc.CC_PATH,
                 obs_client_getter: Callable[[], Any] = lambda: None,
                 obs_loop: Optional[Any] = None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._post_ui = post_ui
        self._cc_path = cc_path
        self._obs_client = obs_client_getter
        self._obs_loop = obs_loop
        self._cc_live = False                 # OBS en direct, d'après la dernière sonde
        self._cc_state = "SUBS_CC_NO_OBS"
        self._cc_queue = cc.CaptionQueue(self._send_caption, cc.load_settings(cc_path))
        self._cc_polling = False
        self._cc_poll_job: Optional[str] = None
        self._cc_tick_job: Optional[str] = None
        self._cc_save_job: Optional[str] = None
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._words_path = words_path
        self._words = sw.load_words(words_path)
        self._corrector = sw.Corrector(self._words)
        self._words_dialog: Optional[WordBankDialog] = None
        self._hub = hub
        self._overlay = overlay
        self._style_dialog: Optional[SubtitleStyleDialog] = None
        self._test_jobs: list[str] = []
        self._settings_path = settings_path
        self._models_root = models_root
        self._settings = engine.load_settings(settings_path)
        self._devices: list[audio.Device] = []
        self._labels: dict[str, audio.Device] = {}       # libellé du menu -> périphérique
        self._transcriber: Optional[engine.Transcriber] = None
        self._download_cancel: Optional[threading.Event] = None
        self._downloading: Optional[engine.Model] = None
        self._state = engine.ST_STOPPED
        self._state_detail = ""
        self._compute_ms = 0.0
        self._last_sound = time.monotonic()
        self._silent = False

        # Tout l'onglet défile : quatre cartes ne tiennent pas dans une fenêtre
        # de 620 px de haut, et la transcription doit rester accessible.
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.grid(row=0, column=0, sticky="nsew")
        self._body.grid_columnconfigure(0, weight=1)
        self._scroller = SmoothScroll(self._body)

        header = ctk.CTkFrame(self._body, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        self._title_lbl = ctk.CTkLabel(header, text=t("SUBS_TITLE"),
                                       font=font(22, "bold"), text_color=COL_TEXT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(header, text=t("SUBS_SUBTITLE"), font=font(11),
                                          text_color=COL_TEXT_MUTED, justify="left",
                                          wraplength=780)
        self._subtitle_lbl.pack(anchor="w", pady=(2, 0))
        # Les étapes, dans l'ordre : sans elles, on active le CC et on attend
        # des sous-titres alors que rien n'écoute encore.
        self._howto_lbl = ctk.CTkLabel(header, text=t("SUBS_HOWTO"), font=font(11),
                                       text_color=COL_TEXT, justify="left", wraplength=780,
                                       anchor="w")
        self._howto_lbl.pack(anchor="w", pady=(8, 0))

        self._build_controls()
        self._build_widget()
        self._build_cc()
        self._build_live()

        self._credit_lbl = ctk.CTkLabel(self._body, text=t("SUBS_CREDIT") + CREDIT_REFINER,
                                        font=font(10), text_color=COL_TEXT_MUTED, anchor="w")
        self._credit_lbl.grid(row=5, column=0, sticky="ew", padx=28, pady=(0, 16))

        self._refresh_devices()
        self._render()
        self._cc_poll()
        self._cc_tick()

    # -- construction ------------------------------------------------------ #

    def _card(self, row: int, **grid) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self._body, fg_color=COL_CARD, corner_radius=14,
                            border_width=1, border_color=COL_BORDER)
        card.grid(row=row, column=0, sticky="nsew", padx=28, **grid)
        card.grid_columnconfigure(1, weight=1)
        return card

    def _field(self, card, row: int, key: str, pady=8) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(card, text=t(key), font=font(12, "bold"),
                           text_color=COL_TEXT, anchor="w")
        lbl.grid(row=row, column=0, sticky="w", padx=(18, 14), pady=pady)
        return lbl

    def _ghost_btn(self, parent, key: str, command, width: int = 120) -> ctk.CTkButton:
        return ctk.CTkButton(parent, text=t(key), width=width, height=34, corner_radius=9,
                             fg_color="transparent", border_width=1,
                             border_color=COL_BORDER, hover_color=COL_BG,
                             text_color=COL_TEXT, font=font(12), command=command)

    def _menu(self, parent, command) -> ctk.CTkOptionMenu:
        return ctk.CTkOptionMenu(parent, values=[""], height=34, corner_radius=9,
                                 fg_color=COL_BG, button_color=COL_BORDER,
                                 button_hover_color=COL_ACCENT_HOVER,
                                 dropdown_fg_color=COL_CARD, text_color=COL_TEXT,
                                 font=font(12), dropdown_font=font(12),
                                 dynamic_resizing=False, command=command)

    def _build_controls(self) -> None:
        card = self._card(1, pady=(6, 12))
        self._card_controls = card

        self._source_lbl = self._field(card, 0, "SUBS_SOURCE", pady=(16, 8))
        self._device_menu = self._menu(card, self._on_device_chosen)
        self._device_menu.grid(row=0, column=1, sticky="ew", pady=(16, 8))
        self._refresh_btn = self._ghost_btn(card, "SUBS_BTN_REFRESH", self._refresh_devices)
        self._refresh_btn.grid(row=0, column=2, padx=(10, 18), pady=(16, 8))

        self._lang_lbl = self._field(card, 1, "SUBS_LANG")
        self._lang_menu = self._menu(card, self._on_lang_chosen)
        self._lang_menu.configure(values=[LANG_NAMES[k] for k in engine.MODELS])
        self._lang_menu.grid(row=1, column=1, sticky="ew", pady=8)
        self._download_btn = self._ghost_btn(
            card, "SUBS_BTN_DOWNLOAD", lambda: self._toggle_download(self._model))
        self._download_btn.grid(row=1, column=2, padx=(10, 18), pady=8)

        # Précision et latence sur une seule ligne : la fenêtre descend à
        # 620 px de haut, et la transcription en direct doit rester visible.
        self._precision_lbl = self._field(card, 2, "SUBS_PRECISION")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=2, column=1, sticky="ew", pady=8)
        row.grid_columnconfigure(2, weight=1)
        self._precision_btn = ctk.CTkSegmentedButton(
            row, values=[t(k) for k in PRECISION_LABELS.values()], font=font(12),
            selected_color=COL_ACCENT, selected_hover_color=COL_ACCENT_HOVER,
            unselected_color=COL_BG, text_color=COL_TEXT, command=self._on_precision_chosen)
        self._precision_btn.grid(row=0, column=0)
        self._pause_lbl = ctk.CTkLabel(row, text=t("SUBS_END_PAUSE"), font=font(12),
                                       text_color=COL_TEXT_MUTED)
        self._pause_lbl.grid(row=0, column=1, padx=(18, 8))
        lo, hi = engine.END_PAUSE_RANGE
        self._pause_slider = ctk.CTkSlider(row, from_=lo, to=hi,
                                           number_of_steps=round((hi - lo) * 10),
                                           progress_color=COL_ACCENT, button_color=COL_ACCENT,
                                           button_hover_color=COL_ACCENT_HOVER,
                                           command=self._on_pause_moved)
        self._pause_slider.grid(row=0, column=2, sticky="ew")
        self._pause_value = ctk.CTkLabel(row, text="", width=44, font=font(12),
                                         text_color=COL_TEXT, anchor="e")
        self._pause_value.grid(row=0, column=3, padx=(8, 0))
        self._refiner_btn = self._ghost_btn(
            card, "SUBS_BTN_DOWNLOAD", lambda: self._toggle_download(engine.REFINER))
        self._refiner_btn.grid(row=2, column=2, padx=(10, 18), pady=8)

        self._model_lbl = ctk.CTkLabel(card, text="", font=font(11),
                                       text_color=COL_TEXT_MUTED, anchor="w", justify="left")
        self._model_lbl.grid(row=3, column=1, columnspan=2, sticky="ew")
        self._progress = ctk.CTkProgressBar(card, height=6, progress_color=COL_ACCENT,
                                            fg_color=COL_BG)
        self._progress.set(0)

        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.grid(row=5, column=0, columnspan=3, sticky="ew", padx=18, pady=(10, 16))
        bar.grid_columnconfigure(2, weight=1)
        self._start_btn = ctk.CTkButton(bar, text=t("SUBS_BTN_START"), width=130, height=38,
                                        corner_radius=9, fg_color=COL_ACCENT,
                                        hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                                        font=font(12, "bold"), command=self._toggle_run)
        self._start_btn.grid(row=0, column=0, rowspan=2)
        self._state_lbl = ctk.CTkLabel(bar, text="", font=font(12, "bold"), anchor="w")
        self._state_lbl.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(16, 0))
        self._meter = ctk.CTkProgressBar(bar, height=8, width=160,
                                         progress_color=COL_GREEN, fg_color=COL_BG)
        self._meter.set(0)
        self._meter.grid(row=1, column=1, sticky="w", padx=(16, 12), pady=(4, 0))
        self._compute_lbl = ctk.CTkLabel(bar, text="", font=font(11),
                                         text_color=COL_TEXT_MUTED, anchor="w")
        self._compute_lbl.grid(row=1, column=2, sticky="ew", pady=(4, 0))

    def _build_widget(self) -> None:
        """Sortie 1 : la source navigateur à coller dans OBS."""
        card = self._card(2, pady=(0, 12))
        self._widget_lbl = ctk.CTkLabel(card, text=t("SUBS_WIDGET_TITLE"), font=font(14, "bold"),
                                        text_color=COL_TEXT, anchor="w")
        self._widget_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 4))
        self._widget_switch = ctk.CTkSwitch(card, text=t("SUBS_WIDGET_ENABLED"), font=font(12),
                                            text_color=COL_TEXT, progress_color=COL_ACCENT,
                                            command=self._toggle_widget)
        self._widget_switch.grid(row=0, column=2, sticky="e", padx=18, pady=(14, 4))

        self._url_entry = ctk.CTkEntry(card, height=32, font=font(11), fg_color=COL_BG,
                                       border_color=COL_BORDER, text_color=COL_TEXT_MUTED)
        self._url_entry.grid(row=1, column=0, columnspan=3, sticky="ew", padx=18, pady=4)

        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=18, pady=(4, 4))
        self._copy_btn = self._ghost_btn(bar, "SUBS_BTN_COPY_LINK", self._copy_link, width=130)
        self._open_btn = self._ghost_btn(bar, "SUBS_BTN_OPEN", self._open_page, width=110)
        self._test_btn = self._ghost_btn(bar, "SUBS_BTN_TEST", self._test_phrase, width=120)
        self._style_btn = ctk.CTkButton(bar, text=t("SUBS_BTN_STYLE"), width=110, height=34,
                                        corner_radius=9, fg_color=COL_ACCENT,
                                        hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                                        font=font(12, "bold"), command=self._open_style)
        self._regen_btn = self._ghost_btn(bar, "SUBS_BTN_REGENERATE", self._regenerate, width=150)
        self._regen_btn.configure(text_color=COL_TEXT_MUTED)
        for i, b in enumerate((self._style_btn, self._copy_btn, self._open_btn,
                               self._test_btn, self._regen_btn)):
            b.pack(side="left", padx=(0 if i == 0 else 8, 0))

        self._widget_hint = ctk.CTkLabel(card, text=t("SUBS_WIDGET_HINT"), font=font(10),
                                         text_color=COL_TEXT_MUTED, anchor="w", justify="left",
                                         wraplength=760)
        self._widget_hint.grid(row=3, column=0, columnspan=3, sticky="ew", padx=18, pady=(2, 14))
        self._refresh_widget()

    def _url(self) -> str:
        return self._overlay.subtitles_url() if self._overlay is not None else ""

    def _refresh_widget(self) -> None:
        url, on = self._url(), self._hub is not None and self._hub.enabled
        self._url_entry.configure(state="normal")
        self._url_entry.delete(0, "end")
        self._url_entry.insert(0, url)
        self._url_entry.configure(state="readonly")
        self._widget_switch.select() if on else self._widget_switch.deselect()
        has_hub = "normal" if self._hub is not None else "disabled"
        self._widget_switch.configure(state=has_hub)
        self._style_btn.configure(state=has_hub)
        self._regen_btn.configure(state=has_hub)
        for b in (self._copy_btn, self._open_btn):
            b.configure(state="normal" if url else "disabled")
        self._test_btn.configure(state="normal" if url and on else "disabled")

    def _toggle_widget(self) -> None:
        if self._hub is not None:
            self._hub.set_enabled(bool(self._widget_switch.get()))
        self._refresh_widget()

    def _copy_link(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._url())
        self._copy_btn.configure(text=t("SUBS_BTN_COPIED"))
        self.after(1500, lambda: self._copy_btn.configure(text=t("SUBS_BTN_COPY_LINK")))

    def _open_page(self) -> None:
        url = self._url()
        if url:
            webbrowser.open(url)

    def _test_phrase(self) -> None:
        """Fait défiler une phrase mot par mot, comme le ferait le moteur."""
        if self._hub is None:
            return
        for job in self._test_jobs:
            self.after_cancel(job)
        words = t("SUBS_TEST_PHRASE").split()
        hub = self._hub
        self._test_jobs = [
            self.after(180 * i, lambda n=i: hub.publish_text("partial", " ".join(words[:n])))
            for i in range(1, len(words) + 1)]
        self._test_jobs.append(self.after(180 * len(words) + 300,
                                          lambda: hub.publish_text("final", " ".join(words))))

    def _open_style(self) -> None:
        if self._hub is None:
            return
        if self._style_dialog is not None and self._style_dialog.winfo_exists():
            self._style_dialog.lift()
            self._style_dialog.focus()
            return
        hub = self._hub
        self._style_dialog = SubtitleStyleDialog(
            self, hub.style, on_change=lambda style, persist: hub.set_style(style, persist))

    def _regenerate(self) -> None:
        if self._hub is None or not messagebox.askyesno(
                t("CONFIRM_DIALOG_TITLE"), t("SUBS_CONFIRM_REGENERATE"), parent=self):
            return
        try:
            self._hub.regenerate_token()
        except OSError:
            messagebox.showerror(t("SUBS_TITLE"), t("SUBS_REGENERATE_FAILED"), parent=self)
            return
        self._refresh_widget()

    # -- sortie 2 : sous-titres CC ----------------------------------------- #

    CC_POLL_MS = 3000         # sonde de l'état OBS (connecté, en direct)
    CC_TICK_MS = 200          # cadence d'envoi des morceaux

    def _build_cc(self) -> None:
        card = self._card(3, pady=(0, 12))
        s = self._cc_queue.settings
        self._cc_lbl = ctk.CTkLabel(card, text=t("SUBS_CC_TITLE"), font=font(14, "bold"),
                                    text_color=COL_TEXT, anchor="w")
        self._cc_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 4))
        self._cc_switch = ctk.CTkSwitch(card, text=t("SUBS_CC_ENABLED"), font=font(12),
                                        text_color=COL_TEXT, progress_color=COL_ACCENT,
                                        command=lambda: self._cc_set(
                                            enabled=bool(self._cc_switch.get())))
        self._cc_switch.grid(row=0, column=2, sticky="e", padx=18, pady=(14, 4))

        status = ctk.CTkFrame(card, fg_color="transparent")
        status.grid(row=1, column=0, columnspan=3, sticky="ew", padx=18, pady=4)
        status.grid_columnconfigure(0, weight=1)
        self._cc_status = ctk.CTkLabel(status, text="", font=font(12, "bold"), anchor="w")
        self._cc_status.grid(row=0, column=0, sticky="w")
        self._cc_test_btn = self._ghost_btn(status, "SUBS_BTN_TEST", self._cc_test, width=120)
        self._cc_test_btn.grid(row=0, column=1)

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", padx=18, pady=4)
        self._cc_width_lbl = ctk.CTkLabel(opts, text=t("SUBS_CC_WIDTH"), font=font(12),
                                          text_color=COL_TEXT_MUTED)
        self._cc_width_lbl.pack(side="left")
        lo, hi = cc.WIDTH_RANGE
        self._cc_width = ctk.CTkSlider(opts, from_=lo, to=hi, number_of_steps=hi - lo, width=140,
                                       progress_color=COL_ACCENT, button_color=COL_ACCENT,
                                       button_hover_color=COL_ACCENT_HOVER,
                                       command=lambda v: self._cc_set(width=int(round(v))))
        self._cc_width.set(s.width)
        self._cc_width.pack(side="left", padx=(8, 4))
        self._cc_width_val = ctk.CTkLabel(opts, text=str(s.width), width=28, font=font(12),
                                          text_color=COL_TEXT)
        self._cc_width_val.pack(side="left", padx=(0, 16))
        self._cc_lines_lbl = ctk.CTkLabel(opts, text=t("SUBS_CC_LINES"), font=font(12),
                                          text_color=COL_TEXT_MUTED)
        self._cc_lines_lbl.pack(side="left")
        self._cc_lines = ctk.CTkSegmentedButton(
            opts, values=[str(n) for n in cc.LINE_CHOICES], font=font(12),
            selected_color=COL_ACCENT, selected_hover_color=COL_ACCENT_HOVER,
            unselected_color=COL_BG, text_color=COL_TEXT,
            command=lambda v: self._cc_set(lines=int(v)))
        self._cc_lines.set(str(s.lines))
        self._cc_lines.pack(side="left", padx=(8, 0))

        flags = ctk.CTkFrame(card, fg_color="transparent")
        flags.grid(row=3, column=0, columnspan=3, sticky="ew", padx=18, pady=4)
        self._cc_flags: dict[str, ctk.CTkSwitch] = {}
        for field, key in CC_FLAGS:
            sw_ = ctk.CTkSwitch(flags, text=t(key), font=font(12), text_color=COL_TEXT,
                                progress_color=COL_ACCENT,
                                command=lambda f=field: self._cc_set(
                                    **{f: bool(self._cc_flags[f].get())}))
            sw_.select() if getattr(s, field) else sw_.deselect()
            sw_.pack(side="left", padx=(0, 18))
            self._cc_flags[field] = sw_

        self._cc_hint = ctk.CTkLabel(card, text=t("SUBS_CC_HINT"), font=font(10),
                                     text_color=COL_TEXT_MUTED, anchor="w", justify="left",
                                     wraplength=760)
        self._cc_hint.grid(row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(2, 14))
        self._cc_switch.select() if s.enabled else self._cc_switch.deselect()
        self._cc_render()

    def _cc_set(self, **changes) -> None:
        settings = replace(self._cc_queue.settings, **changes)
        if settings == self._cc_queue.settings:
            return
        self._cc_queue.settings = settings
        if "enabled" in changes and not settings.enabled:
            self._cc_queue.clear()
        self._cc_width_val.configure(text=str(settings.width))
        # Une écriture quand le curseur s'arrête, pas une par cran.
        if self._cc_save_job is not None:
            self.after_cancel(self._cc_save_job)
        self._cc_save_job = self.after(400, self._cc_save)
        self._cc_render()

    def _cc_save(self) -> None:
        self._cc_save_job = None
        try:
            cc.save_settings(self._cc_queue.settings, self._cc_path)
        except OSError:
            pass

    def _cc_render(self) -> None:
        on = self._cc_queue.settings.enabled
        colors = {"SUBS_CC_LIVE": COL_GREEN, "SUBS_CC_NOT_LIVE": COL_YELLOW,
                  "SUBS_CC_NO_OBS": COL_TEXT_MUTED}
        self._cc_status.configure(text=t(self._cc_state) if on else t("SUBS_CC_OFF"),
                                  text_color=colors[self._cc_state] if on else COL_TEXT_MUTED)
        self._cc_test_btn.configure(state="normal" if on and self._cc_live else "disabled")

    def _cc_poll(self) -> None:
        """Sonde OBS sans bloquer l'interface : la réponse revient par post_ui.

        Une seule sonde à la fois (un OBS lent ne doit pas en empiler une
        toutes les 3 s), et aucune tant que le CC est désactivé."""
        self._cc_poll_job = self.after(self.CC_POLL_MS, self._cc_poll)
        if not self._cc_queue.settings.enabled or self._cc_polling:
            return
        client = self._obs_client()
        if client is None or not client.is_connected or self._obs_loop is None:
            self._cc_apply_status(False, False)
            return
        try:
            future = self._obs_loop.run_coro(client.stream_active())
        except Exception:
            self._cc_apply_status(False, False)
            return
        self._cc_polling = True

        # Appelée dans le thread asyncio : rien d'autre que post_ui ici.
        def done(f) -> None:
            try:
                connected, live = True, bool(f.result())
            except BaseException:
                connected, live = False, False
            self._post_ui(lambda: self._cc_poll_done(connected, live))

        future.add_done_callback(done)

    def _cc_poll_done(self, connected: bool, live: bool) -> None:
        self._cc_polling = False
        if self.winfo_exists():                  # fenêtre peut-être déjà fermée
            self._cc_apply_status(connected, live)

    def _cc_apply_status(self, connected: bool, live: bool) -> None:
        if self._cc_live and not live:
            self._cc_queue.clear()       # fin du live : OBS refuserait tout
        self._cc_live = live
        self._cc_state = ("SUBS_CC_LIVE" if live else "SUBS_CC_NOT_LIVE") if connected \
            else "SUBS_CC_NO_OBS"
        self._cc_render()

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        for job in (self._cc_poll_job, self._cc_tick_job, self._cc_save_job):
            if job is not None:
                self.after_cancel(job)

    def _cc_tick(self) -> None:
        self._cc_tick_job = self.after(self.CC_TICK_MS, self._cc_tick)
        if self._cc_queue.settings.enabled and self._cc_live:
            self._cc_queue.tick()

    def _send_caption(self, chunk: str) -> None:
        client = self._obs_client()
        if client is None or not client.is_connected or self._obs_loop is None:
            return
        try:
            future = self._obs_loop.run_coro(client.send_stream_caption(chunk))
        except Exception:
            return

        def done(f) -> None:
            if f.cancelled():
                return
            if f.exception() is not None:
                logger.warning("Sous-titre CC refusé par OBS : %s", f.exception())
            else:
                # Une trace par envoi : sans elle, « rien chez les spectateurs »
                # ne se distinguait pas de « rien d'envoyé ».
                logger.info("Sous-titre CC accepté par OBS : %r", chunk)

        future.add_done_callback(done)

    def _cc_test(self) -> None:
        self._cc_queue.push(t("SUBS_TEST_PHRASE"))

    def _build_live(self) -> None:
        card = self._card(4, pady=(0, 10))
        card.grid_rowconfigure(1, weight=1)
        self._live_lbl = ctk.CTkLabel(card, text=t("SUBS_LIVE"), font=font(14, "bold"),
                                      text_color=COL_TEXT, anchor="w")
        self._live_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 6))
        tools = ctk.CTkFrame(card, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e", padx=18, pady=(14, 6))
        self._words_btn = self._ghost_btn(tools, "SUBS_BTN_WORDS", self._open_words, width=170)
        self._words_btn.pack(side="left", padx=(0, 8))
        self._clear_btn = self._ghost_btn(tools, "SUBS_BTN_CLEAR", self._clear, width=100)
        self._clear_btn.pack(side="left")
        self._refresh_words_btn()
        self._box = ctk.CTkTextbox(card, fg_color=COL_BG, text_color=COL_TEXT, height=260,
                                   font=font(15), wrap="word", corner_radius=10,
                                   border_width=0)
        self._box.grid(row=1, column=0, columnspan=3, sticky="nsew", padx=18, pady=(0, 16))
        self._box.tag_config("partial", foreground=COL_TEXT_MUTED)
        self._box.tag_config("final", foreground=COL_TEXT)
        self._box.mark_set("partial_start", "end-1c")
        self._box.mark_gravity("partial_start", "left")
        self._box.configure(state="disabled")

    # -- périphériques et langue ------------------------------------------ #

    def _refresh_devices(self) -> None:
        try:
            self._devices = audio.list_devices()
        except Exception:
            self._devices = []
        self._labels = {f"{_kind_label(d.kind)} · {d.name}": d for d in self._devices}
        values = list(self._labels) or [t("SUBS_NO_DEVICE")]
        self._device_menu.configure(values=values)
        chosen = audio.find_device(self._devices, self._settings.device_name,
                                   self._settings.device_kind)
        if chosen is None and self._devices:
            # Premier lancement : le micro par défaut de Windows. On sous-titre
            # d'abord ce que dit le streamer ; le son du PC se choisit ensuite.
            default = audio.default_input_name()
            mics = [d for d in self._devices if d.kind == audio.KIND_MIC]
            chosen = next((d for d in mics if d.name == default),
                          mics[0] if mics else self._devices[0])
            self._save(device_name=chosen.name, device_kind=chosen.kind)
        label = next((k for k, d in self._labels.items() if d == chosen), values[0])
        self._device_menu.set(label)
        self._render()

    def _on_device_chosen(self, label: str) -> None:
        device = self._labels.get(label)
        if device is not None:
            self._save(device_name=device.name, device_kind=device.kind)
        self._render()

    def _on_lang_chosen(self, name: str) -> None:
        lang = next((k for k, v in LANG_NAMES.items() if v == name), self._settings.lang)
        self._save(lang=lang)
        self._render()

    def _save(self, **changes) -> None:
        self._settings = replace(self._settings, **changes)
        try:
            engine.save_settings(self._settings, self._settings_path)
        except OSError:
            pass                     # le réglage reste valable jusqu'à la fermeture

    @property
    def _model(self) -> engine.Model:
        return engine.MODELS[self._settings.lang]

    @property
    def _device(self) -> Optional[audio.Device]:
        return audio.find_device(self._devices, self._settings.device_name,
                                 self._settings.device_kind)

    # -- téléchargement ---------------------------------------------------- #

    def _on_precision_chosen(self, label: str) -> None:
        key = next((k for k, v in PRECISION_LABELS.items() if t(v) == label),
                   self._settings.precision)
        self._save(precision=key)
        self._render()

    def _on_pause_moved(self, value: float) -> None:
        pause = round(value, 1)
        self._pause_value.configure(text=f"{pause:.1f} s")
        if pause != self._settings.end_pause:
            self._save(end_pause=pause)

    def _toggle_download(self, model: engine.Model) -> None:
        """Un seul téléchargement à la fois ; le bouton qui l'a lancé l'annule."""
        if self._download_cancel is not None:
            self._download_cancel.set()
            return
        cancel = threading.Event()
        self._download_cancel = cancel
        self._downloading = model

        def progress(done: int, total: int) -> None:
            self._post_ui(lambda: self._show_progress(done, total))

        def run() -> None:
            error = ""
            try:
                engine.download(model, self._models_root, progress, cancel)
            except engine.DownloadCancelled:
                pass
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self._post_ui(lambda: self._download_done(error))

        threading.Thread(target=run, name="subtitles-download", daemon=True).start()
        self._progress.set(0)
        self._progress.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(0, 18), pady=(4, 0))
        self._render()

    def _show_progress(self, done: int, total: int) -> None:
        if self._download_cancel is None:
            return
        self._progress.set(done / total if total else 0)
        self._model_lbl.configure(text=t("SUBS_DOWNLOADING", done=_mb(done), total=_mb(total)),
                                  text_color=COL_TEXT_MUTED)

    def _download_done(self, error: str) -> None:
        self._download_cancel = None
        self._downloading = None
        self._progress.grid_remove()
        self._render()
        if error:
            self._model_lbl.configure(text=t("SUBS_DOWNLOAD_FAILED", error=error),
                                      text_color=COL_RED)

    # -- marche / arrêt ---------------------------------------------------- #

    def _toggle_run(self) -> None:
        if self._transcriber is not None:
            self.stop()
            return
        device = self._device
        if device is None or not engine.is_installed(self._model, self._models_root):
            return
        # Chaque callback emporte SA session : un message en retard d'une
        # session arrêtée ne doit jamais toucher celle qui l'a remplacée.
        def mine(fn):
            return lambda *args: self._post_ui(
                lambda: fn(*args) if self._transcriber is session else None)

        pause = self._settings.end_pause
        refine = None
        if self._refining_possible():
            folder = engine.model_dir(engine.REFINER, self._models_root)
            refine = lambda on_revise: engine.Refiner.load(folder, on_revise)  # noqa: E731
        session = engine.Transcriber(
            device, engine.model_dir(self._model, self._models_root),
            on_text=mine(self._on_text), on_tick=mine(self._on_tick),
            on_state=mine(self._on_state),
            load=lambda folder: engine.Recognizer.load(folder, end_pause=pause),
            refine=refine)
        self._transcriber = session
        # En mode Précis, le CC attend la phrase corrigée avant d'envoyer.
        self._cc_queue.wait_revise = refine is not None
        session.start()
        self._render()

    def stop(self) -> None:
        """Arrête l'écoute. Appelée aussi à la fermeture de la fenêtre."""
        if self._download_cancel is not None:
            self._download_cancel.set()
        for job in self._test_jobs:
            self.after_cancel(job)
        self._test_jobs = []
        for dialog in (self._style_dialog, self._words_dialog):
            if dialog is not None and dialog.winfo_exists():
                dialog.close()                  # écrit un réglage encore en attente
        if self._hub is not None:
            self._hub.clear()                   # sinon la phrase en cours reste dans OBS
        # Les phrases qui attendaient leur correction partent telles quelles.
        self._cc_queue.flush_pending()
        self._cc_queue.wait_revise = False
        transcriber, self._transcriber = self._transcriber, None
        if transcriber is not None:
            # Sans attendre : le thread finit son bloc de 100 ms seul, et ses
            # derniers messages sont ignorés (voir `mine`). Attendre ici gelait
            # l'interface pendant un chargement de modèle.
            transcriber.stop(timeout=0)
        self._state, self._state_detail = engine.ST_STOPPED, ""
        self._meter.set(0)
        self._compute_ms = 0.0
        self._render()

    def _on_state(self, state: str, detail: str) -> None:
        self._state, self._state_detail = state, detail
        self._last_sound, self._silent = time.monotonic(), False
        if state in (engine.ST_ERROR, engine.ST_STOPPED):
            self._transcriber = None
        self._render()

    def _on_tick(self, lvl: float, compute_ms: float) -> None:
        # Source muette : le plus souvent, une sortie son choisie à la place
        # du micro. Le dire au lieu de laisser l'onglet « à l'écoute » de rien.
        now = time.monotonic()
        if lvl > SILENCE_LEVEL:
            self._last_sound = now
        silent = now - self._last_sound > SILENCE_WARN_S
        if silent != self._silent:
            self._silent = silent
            self._render()
        self._meter.set(lvl)
        # Moyenne glissante : la valeur brute saute d'un bloc à l'autre.
        self._compute_ms = compute_ms if not self._compute_ms else \
            self._compute_ms * 0.9 + compute_ms * 0.1
        self._compute_lbl.configure(text=t("SUBS_COMPUTE", ms=round(self._compute_ms)))

    # -- transcription ----------------------------------------------------- #

    def _refining_possible(self) -> bool:
        return (self._settings.precision == engine.PRECISION_ACCURATE
                and engine.is_installed(engine.REFINER, self._models_root))

    # -- banque de mots ---------------------------------------------------- #

    def _refresh_words_btn(self) -> None:
        self._words_btn.configure(text=t("SUBS_BTN_WORDS", count=len(self._words)))

    def _open_words(self) -> None:
        if self._words_dialog is not None and self._words_dialog.winfo_exists():
            self._words_dialog.lift()
            self._words_dialog.focus()
            return
        self._words_dialog = WordBankDialog(self, self._words, on_change=self._words_changed)

    def _words_changed(self, words: list[sw.Word], persist: bool) -> None:
        self._words = words
        self._corrector = sw.Corrector(words)       # vaut pour la phrase suivante
        self._refresh_words_btn()
        if persist:
            try:
                sw.save_words(words, self._words_path)
            except OSError:
                pass                 # la banque reste active jusqu'à la fermeture

    # -- transcription ----------------------------------------------------- #

    def _on_text(self, kind: str, text: str, uid: int = -1) -> None:
        # Avant tout le reste : l'onglet, le widget et le CC voient le même texte.
        text = self._corrector.apply(text)
        if self._cc_queue.settings.enabled and self._cc_live:
            self._cc_queue.on_text(kind, text, uid)
        if self._hub is not None:
            self._hub.publish_text(kind, text, uid)
        box = self._box
        box.configure(state="normal")
        if kind == engine.REVISE:
            # La phrase corrigée remplace celle du premier passage, à sa place,
            # même si d'autres phrases ont été ajoutées depuis.
            tag = f"u{uid}"
            ranges = box.tag_ranges(tag)
            if ranges:
                start = ranges[0]
                box.delete(start, ranges[1])
                box.insert(start, text, ("final", tag))
            box.configure(state="disabled")
            return
        box.delete("partial_start", "end-1c")
        if kind == engine.FINAL:
            box.insert("end", text, ("final", f"u{uid}"))
            box.insert("end", "\n", "final")
            box.mark_set("partial_start", "end-1c")
            lines = int(box.index("end-1c").split(".")[0])
            if lines > MAX_LINES:
                box.delete("1.0", f"{lines - MAX_LINES}.0")
                # Les étiquettes `u<n>` des lignes retirées : sinon Tk les
                # garde toutes, une par phrase, pendant tout le live.
                for tag in box.tag_names():
                    if tag.startswith("u") and not box.tag_ranges(tag):
                        box.tag_delete(tag)
        else:
            box.insert("end", text, "partial")
        box.configure(state="disabled")
        box.see("end")

    def _clear(self) -> None:
        if self._hub is not None:
            self._hub.clear()
        self._box.configure(state="normal")
        self._box.delete("1.0", "end")
        self._box.mark_set("partial_start", "end-1c")
        self._box.configure(state="disabled")

    # -- rendu ------------------------------------------------------------- #

    def _render(self) -> None:
        running = self._transcriber is not None
        downloading = self._download_cancel is not None
        installed = engine.is_installed(self._model, self._models_root)
        ok = engine.available()

        self._lang_menu.set(LANG_NAMES[self._settings.lang])
        locked = "disabled" if running or downloading else "normal"
        self._device_menu.configure(state=locked if self._devices else "disabled")
        self._lang_menu.configure(state=locked)
        self._refresh_btn.configure(state="disabled" if running else "normal")
        # Précision et pause sont lues au démarrage : figées pendant l'écoute.
        self._precision_btn.set(t(PRECISION_LABELS[self._settings.precision]))
        self._precision_btn.configure(state=locked)
        self._pause_slider.set(self._settings.end_pause)
        self._pause_slider.configure(state=locked)
        self._pause_value.configure(text=f"{self._settings.end_pause:.1f} s")

        accurate = self._settings.precision == engine.PRECISION_ACCURATE
        refiner_ok = engine.is_installed(engine.REFINER, self._models_root)
        for btn, model, done in ((self._download_btn, self._model, installed),
                                 (self._refiner_btn, engine.REFINER, refiner_ok)):
            if downloading and self._downloading == model:
                btn.configure(text=t("SUBS_BTN_CANCEL"), state="normal")
            else:
                btn.configure(text=t("SUBS_BTN_DOWNLOAD"),
                              state="disabled" if done or running or downloading else "normal")
        if accurate and not refiner_ok:
            self._refiner_btn.grid()
        else:
            self._refiner_btn.grid_remove()
        if not downloading:
            lines = [t("SUBS_MODEL_READY") if installed
                     else t("SUBS_MODEL_MISSING", size=_mb(self._model.size))]
            if accurate:
                lines.append(t("SUBS_REFINER_READY") if refiner_ok
                             else t("SUBS_REFINER_MISSING", size=_mb(engine.REFINER.size)))
            self._model_lbl.configure(
                text="  ·  ".join(lines),
                text_color=COL_GREEN if installed and (refiner_ok or not accurate)
                else COL_TEXT_MUTED)

        can_start = ok and installed and self._device is not None and not downloading
        self._start_btn.configure(
            text=t("SUBS_BTN_STOP") if running else t("SUBS_BTN_START"),
            fg_color=COL_RED if running else COL_ACCENT,
            state="normal" if running or can_start else "disabled")

        if not ok:
            self._state_lbl.configure(text=t("SUBS_UNAVAILABLE", error=engine.import_error()),
                                      text_color=COL_RED)
        elif running and self._silent and self._state == engine.ST_LISTENING:
            self._state_lbl.configure(text=t("SUBS_NO_SIGNAL"), text_color=COL_YELLOW)
        else:
            self._state_lbl.configure(
                text=t(_STATE_TEXT[self._state], detail=self._state_detail),
                text_color=_STATE_COLOR[self._state])
        if not running:
            self._compute_lbl.configure(text="")

    def refresh_labels(self) -> None:
        self._title_lbl.configure(text=t("SUBS_TITLE"))
        self._subtitle_lbl.configure(text=t("SUBS_SUBTITLE"))
        self._howto_lbl.configure(text=t("SUBS_HOWTO"))
        self._source_lbl.configure(text=t("SUBS_SOURCE"))
        self._lang_lbl.configure(text=t("SUBS_LANG"))
        self._precision_lbl.configure(text=t("SUBS_PRECISION"))
        self._precision_btn.configure(values=[t(k) for k in PRECISION_LABELS.values()])
        self._pause_lbl.configure(text=t("SUBS_END_PAUSE"))
        self._refresh_btn.configure(text=t("SUBS_BTN_REFRESH"))
        self._live_lbl.configure(text=t("SUBS_LIVE"))
        self._clear_btn.configure(text=t("SUBS_BTN_CLEAR"))
        self._cc_lbl.configure(text=t("SUBS_CC_TITLE"))
        self._cc_switch.configure(text=t("SUBS_CC_ENABLED"))
        self._cc_width_lbl.configure(text=t("SUBS_CC_WIDTH"))
        self._cc_lines_lbl.configure(text=t("SUBS_CC_LINES"))
        self._cc_hint.configure(text=t("SUBS_CC_HINT"))
        self._cc_test_btn.configure(text=t("SUBS_BTN_TEST"))
        for field, key in CC_FLAGS:
            self._cc_flags[field].configure(text=t(key))
        self._cc_render()
        self._refresh_words_btn()
        self._credit_lbl.configure(text=t("SUBS_CREDIT") + CREDIT_REFINER)
        self._widget_lbl.configure(text=t("SUBS_WIDGET_TITLE"))
        self._widget_switch.configure(text=t("SUBS_WIDGET_ENABLED"))
        self._widget_hint.configure(text=t("SUBS_WIDGET_HINT"))
        for b, key in ((self._copy_btn, "SUBS_BTN_COPY_LINK"), (self._open_btn, "SUBS_BTN_OPEN"),
                       (self._test_btn, "SUBS_BTN_TEST"), (self._style_btn, "SUBS_BTN_STYLE"),
                       (self._regen_btn, "SUBS_BTN_REGENERATE")):
            b.configure(text=t(key))
        self._refresh_devices()         # les libellés « Micro · » sont traduits
