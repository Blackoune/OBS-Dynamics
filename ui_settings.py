"""Vue Paramètres : connexion OBS, seuils de détection, langue."""
from __future__ import annotations

import time
import unicodedata
from dataclasses import replace
from typing import Callable

import i18n
from env_config import EnvConfigManager
from i18n import t
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_ACCENT_SOFT, COL_BG,
                       COL_BORDER, COL_CARD, COL_GREEN, COL_RED, COL_TEXT,
                       COL_TEXT_MUTED, SmoothScroll, ctk, font)


def alpha_key(name: str) -> str:
    """Tri alphabétique qui ignore accents et casse : « Čeština » se range
    au C, pas après le Z. Les autres écritures suivent l'alphabet latin."""
    decomposed = unicodedata.normalize("NFD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


# ============================================================================
# VUE : PARAMÈTRES
# ============================================================================
class SettingsView(ctk.CTkFrame):
    def __init__(self, master, config_mgr: EnvConfigManager, on_saved: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.config_mgr = config_mgr
        self.on_saved = on_saved
        self.grid_columnconfigure(0, weight=1)

        self._title_lbl = ctk.CTkLabel(self, text=t("SETTINGS_TITLE"), font=font(22, "bold"), text_color=COL_TEXT)
        self._title_lbl.grid(row=0, column=0, sticky="w", padx=28, pady=(28, 16))

        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14, border_width=1, border_color=COL_BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=28)
        card.grid_columnconfigure(1, weight=1)

        self._section_lbl = ctk.CTkLabel(card, text=t("SETTINGS_SECTION_OBS_WS"), font=font(14, "bold"),
                                          text_color=COL_TEXT)
        self._section_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self.host_var = ctk.StringVar()
        self.port_var = ctk.StringVar()
        self.pwd_var = ctk.StringVar()
        self.interval_var = ctk.StringVar()
        self.threshold_var = ctk.StringVar()

        self._field_labels: dict[str, ctk.CTkLabel] = {}
        self._field_labels["host"] = self._field(card, 1, t("SETTINGS_LABEL_HOST"), self.host_var)
        self._field_labels["port"] = self._field(card, 2, t("SETTINGS_LABEL_PORT"), self.port_var)
        self._field_labels["password"] = self._password_field(card, 3, t("SETTINGS_LABEL_PASSWORD"), self.pwd_var)
        self._field_labels["interval"] = self._field(card, 4, t("SETTINGS_LABEL_SCAN_INTERVAL"), self.interval_var)
        self._field_labels["threshold"] = self._field(card, 5, t("SETTINGS_LABEL_MATCH_THRESHOLD"), self.threshold_var)

        self.msg_lbl = ctk.CTkLabel(card, text="", font=font(11))
        self.msg_lbl.grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 0))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=7, column=0, columnspan=2, sticky="ew", padx=20, pady=18)
        self._save_btn = ctk.CTkButton(btn_row, text=t("SETTINGS_BTN_SAVE"), width=160, height=38,
                                        fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                        text_color="#0F0C1B", corner_radius=9,
                                        font=font(13, "bold"), command=self._save)
        self._save_btn.pack(side="left")

        # --- Sélecteur de langue : segmented control géométriquement stable ---
        lang_card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14, border_width=1, border_color=COL_BORDER)
        lang_card.grid(row=2, column=0, sticky="ew", padx=28, pady=(20, 0))
        lang_card.grid_columnconfigure(1, weight=1)

        self._lang_section_lbl = ctk.CTkLabel(lang_card, text=t("SETTINGS_SECTION_LANGUAGE"),
                                               font=font(14, "bold"), text_color=COL_TEXT)
        self._lang_section_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self._lang_seg = LanguageSegmentedControl(lang_card, on_select=self._on_lang_selected)
        self._lang_seg.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 18))

        # Les autres langues du catalogue, trop nombreuses pour des boutons,
        # dans un menu à droite, par ordre alphabétique. Chacune y porte son
        # nom natif : qui la parle la reconnaît, quelle que soit la langue
        # affichée au moment du choix.
        names = {i18n.lang_name(code): code for code in i18n.available_langs()
                 if code not in LanguageSegmentedControl.LANGS}
        self._more_langs = dict(sorted(names.items(), key=lambda item: alpha_key(item[0])))
        self._lang_menu = ScrollableOptionMenu(
            lang_card, values=list(self._more_langs), width=190, dynamic_resizing=False,
            height=LanguageSegmentedControl.LANG_BTN_HEIGHT + 8, corner_radius=10,
            font=font(12, "bold"),
            command=lambda name: self._on_lang_selected(self._more_langs[name]),
        )
        self._lang_menu.grid(row=1, column=1, sticky="w", pady=(0, 18))

        self._load_into_form()

    def _field(self, parent, row: int, label: str, var: ctk.StringVar) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(parent, text=label, font=font(12), text_color=COL_TEXT_MUTED)
        lbl.grid(row=row, column=0, sticky="w", padx=20, pady=6)
        ctk.CTkEntry(parent, textvariable=var, width=200, height=34, fg_color=COL_BG,
                     border_color=COL_BORDER).grid(row=row, column=1, sticky="e", padx=20, pady=6)
        return lbl

    def _password_field(self, parent, row: int, label: str, var: ctk.StringVar) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(parent, text=label, font=font(12), text_color=COL_TEXT_MUTED)
        lbl.grid(row=row, column=0, sticky="w", padx=20, pady=6)
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=1, sticky="e", padx=20, pady=6)
        self._pwd_entry = ctk.CTkEntry(wrapper, textvariable=var, width=160, height=34, show="•",
                                        fg_color=COL_BG, border_color=COL_BORDER)
        self._pwd_entry.pack(side="left")
        self._pwd_visible = False
        # Point plein = mot de passe masqué, cercle vide = mot de passe en
        # clair. Deux glyphes monochromes de la même famille que le reste de
        # l'interface, là où l'emoji d'œil imposait ses propres couleurs.
        self._pwd_btn = ctk.CTkButton(wrapper, text="●", width=34, height=34,
                                      fg_color=COL_BG, hover_color=COL_BORDER,
                                      command=self._toggle_pwd)
        self._pwd_btn.pack(side="left", padx=(4, 0))
        return lbl

    def _toggle_pwd(self) -> None:
        self._pwd_visible = not self._pwd_visible
        self._pwd_entry.configure(show="" if self._pwd_visible else "•")
        self._pwd_btn.configure(text="○" if self._pwd_visible else "●")

    def _load_into_form(self) -> None:
        cfg = self.config_mgr.load()
        self.host_var.set(cfg.host)
        self.port_var.set(str(cfg.port))
        self.pwd_var.set(cfg.password)
        self.interval_var.set(str(cfg.scan_interval_seconds))
        self.threshold_var.set(str(cfg.match_threshold))
        self._show_lang(cfg.lang)

    def _show_lang(self, lang: str) -> None:
        """Un seul des deux contrôles porte la langue active : son bouton pour
        FR/EN/ES, sinon le menu, qui affiche alors son nom en surbrillance."""
        self._lang_seg.set_active(lang, notify=False)
        seg = LanguageSegmentedControl
        in_menu = lang not in seg.LANGS
        self._lang_menu.set(i18n.lang_name(lang) if in_menu else t("SETTINGS_LANG_MORE"))
        self._lang_menu.configure(
            fg_color=seg.COL_ACTIVE if in_menu else seg.COL_INACTIVE,
            button_color=seg.COL_ACTIVE if in_menu else seg.COL_INACTIVE,
            button_hover_color=seg.COL_ACTIVE_HOVER if in_menu else seg.COL_INACTIVE_HOVER,
            text_color="#FFFFFF" if in_menu else COL_TEXT_MUTED,
        )

    def _on_lang_selected(self, lang: str) -> None:
        """Applique le changement de langue à chaud (i18n.set_lang notifie
        tous les listeners) puis persiste le choix dans .env, sans jamais
        redémarrer l'application ni casser les libellés déjà affichés."""
        if not i18n.set_lang(lang):
            return
        self._show_lang(lang)
        cfg = self.config_mgr.load()
        cfg.lang = lang
        self.config_mgr.save(cfg)

    def refresh_labels(self) -> None:
        """Rechargement à chaud après changement de langue."""
        self._title_lbl.configure(text=t("SETTINGS_TITLE"))
        self._section_lbl.configure(text=t("SETTINGS_SECTION_OBS_WS"))
        self._lang_section_lbl.configure(text=t("SETTINGS_SECTION_LANGUAGE"))
        self._field_labels["host"].configure(text=t("SETTINGS_LABEL_HOST"))
        self._field_labels["port"].configure(text=t("SETTINGS_LABEL_PORT"))
        self._field_labels["password"].configure(text=t("SETTINGS_LABEL_PASSWORD"))
        self._field_labels["interval"].configure(text=t("SETTINGS_LABEL_SCAN_INTERVAL"))
        self._field_labels["threshold"].configure(text=t("SETTINGS_LABEL_MATCH_THRESHOLD"))
        self._save_btn.configure(text=t("SETTINGS_BTN_SAVE"))
        self._lang_seg.refresh_labels()
        self._show_lang(i18n.current_lang())

    def _save(self) -> None:
        try:
            port = int(self.port_var.get())
            if not (0 < port <= 65535):
                raise ValueError(t("SETTINGS_ERR_PORT_RANGE"))
            interval = max(0.5, float(self.interval_var.get()))
            threshold = min(1.0, max(0.0, float(self.threshold_var.get())))
        except ValueError as exc:
            self.msg_lbl.configure(text=t("SETTINGS_ERR_INVALID_VALUE", error=exc), text_color=COL_RED)
            return

        # replace() sur la config chargee, et non OBSConfig(...) : cet ecran
        # n'edite que 5 champs alors que save() reecrit les 9 cles. Repartir
        # d'une instance neuve remettait RAWG_API_KEY et OBS_OVERLAY_PORT a
        # leur valeur par defaut a chaque enregistrement.
        cfg = replace(
            self.config_mgr.load(),
            host=self.host_var.get().strip() or "localhost",
            port=port, password=self.pwd_var.get(),
            scan_interval_seconds=interval, match_threshold=threshold,
            lang=i18n.current_lang(),
        )
        if self.config_mgr.save(cfg):
            self.msg_lbl.configure(text=t("SETTINGS_SAVE_SUCCESS"), text_color=COL_GREEN)
            self.on_saved()
        else:
            self.msg_lbl.configure(text=t("SETTINGS_SAVE_FAILED"), text_color=COL_RED)


class LanguageSegmentedControl(ctk.CTkFrame):
    """Sélecteur de langue en groupe de boutons segmentés à géométrie fixe :
    chaque bouton a une largeur figée (LANG_BTN_WIDTH) et le highlight actif
    ne fait que changer de couleur de fond — il ne redimensionne, ne déplace
    et ne pousse jamais les widgets voisins, quelle que soit la langue active
    (corrige le bug de décalage du sélecteur mentionné dans les specs)."""

    LANGS = ("fr", "en", "es")
    LANG_BTN_WIDTH = 64
    LANG_BTN_HEIGHT = 34
    COL_ACTIVE = "#3B82F6"
    COL_ACTIVE_HOVER = "#2563EB"
    COL_INACTIVE = COL_BG
    COL_INACTIVE_HOVER = COL_BORDER

    def __init__(self, master, on_select: Callable[[str], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_BG, corner_radius=10,
                          border_width=1, border_color=COL_BORDER, **kwargs)
        self._on_select = on_select
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._active_lang = i18n.current_lang()

        for i, lang in enumerate(self.LANGS):
            btn = ctk.CTkButton(
                self, text=t(f"LANG_{lang.upper()}"), width=self.LANG_BTN_WIDTH, height=self.LANG_BTN_HEIGHT,
                corner_radius=8, font=font(12, "bold"),
                fg_color=self.COL_INACTIVE, hover_color=self.COL_INACTIVE_HOVER,
                text_color=COL_TEXT_MUTED, border_width=0,
                command=lambda l=lang: self._select(l),
            )
            btn.grid(row=0, column=i, padx=3, pady=3)
            self._buttons[lang] = btn

        self._apply_active_style()

    def _select(self, lang: str) -> None:
        if lang == self._active_lang:
            return
        self._active_lang = lang
        self._apply_active_style()
        self._on_select(lang)

    def set_active(self, lang: str, notify: bool = True) -> None:
        # Une langue sans bouton (choisie dans le menu voisin) est acceptée :
        # aucun bouton ne reste alors allumé.
        self._active_lang = lang
        self._apply_active_style()
        if notify:
            self._on_select(lang)

    def _apply_active_style(self) -> None:
        # Géométrie strictement inchangée : seule fg_color/text_color change,
        # jamais width/height/padx/pady -> le carré actif reste verrouillé
        # dans son footprint sans décaler les boutons adjacents.
        for lang, btn in self._buttons.items():
            is_active = lang == self._active_lang
            btn.configure(
                fg_color=self.COL_ACTIVE if is_active else self.COL_INACTIVE,
                hover_color=self.COL_ACTIVE_HOVER if is_active else self.COL_INACTIVE_HOVER,
                text_color="#FFFFFF" if is_active else COL_TEXT_MUTED,
            )

    def refresh_labels(self) -> None:
        for lang, btn in self._buttons.items():
            btn.configure(text=t(f"LANG_{lang.upper()}"))


class ScrollableOptionMenu(ctk.CTkOptionMenu):
    """CTkOptionMenu dont la liste s'ouvre sur VISIBLE_ROWS lignes qui
    défilent, au lieu du menu natif de Windows qui étale toutes les valeurs
    sur la hauteur de l'écran.

    Remplace `_open_dropdown_menu`, interne à customtkinter : la version est
    épinglée dans requirements.txt, à revérifier si on la change.
    """

    VISIBLE_ROWS = 10
    ROW_HEIGHT = 30

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._popup: ctk.CTkToplevel | None = None
        self._list: ctk.CTkScrollableFrame | None = None
        self._closed_at = 0.0
        self._anchor: tuple[int, int, int, int] | None = None
        # La liste est une fenêtre à part, posée à l'écran : elle ne suit pas
        # la fenêtre principale. Celle-ci bouge ou change de taille -> on
        # ferme, l'utilisateur rouvre une fois la fenêtre en place.
        self.winfo_toplevel().bind("<Configure>", self._on_window_configure, add="+")

    def _window_box(self) -> tuple[int, int, int, int]:
        top = self.winfo_toplevel()
        return top.winfo_rootx(), top.winfo_rooty(), top.winfo_width(), top.winfo_height()

    def _on_window_configure(self, event) -> None:
        # Le binding de la fenêtre reçoit aussi le <Configure> de chacun de
        # ses widgets : seul celui de la fenêtre elle-même compte, et
        # seulement s'il l'a vraiment déplacée ou redimensionnée.
        if (self._popup is not None and event.widget is self.winfo_toplevel()
                and self._window_box() != self._anchor):
            self.close_dropdown()

    def _open_dropdown_menu(self) -> None:
        # Cliquer sur ce bouton liste ouverte la ferme d'abord (perte de
        # focus), puis arrive ici : sans ce délai, elle se rouvrirait aussitôt.
        if self._popup is not None or time.monotonic() - self._closed_at < 0.3:
            return
        popup = self._popup = ctk.CTkToplevel(self, fg_color=COL_BORDER)
        popup.overrideredirect(True)
        rows = min(len(self._values), self.VISIBLE_ROWS)
        self._list = ctk.CTkScrollableFrame(popup, width=self._current_width - 19,
                                            height=rows * self.ROW_HEIGHT,
                                            fg_color=COL_CARD, corner_radius=0)
        self._list.pack(padx=1, pady=1)
        for value in self._values:
            ctk.CTkButton(self._list, text=value, anchor="w", height=self.ROW_HEIGHT,
                          corner_radius=6, font=font(12), text_color=COL_TEXT,
                          fg_color=COL_ACCENT_SOFT if value == self._current_value else "transparent",
                          hover_color=COL_BORDER,
                          command=lambda v=value: self._pick(v)).pack(fill="x")
        SmoothScroll(self._list)

        popup.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + 2
        if y + popup.winfo_reqheight() > self.winfo_screenheight():   # pas la place dessous
            y = self.winfo_rooty() - popup.winfo_reqheight() - 2
        popup.geometry(f"+{x}+{y}")
        if self._current_value in self._values:   # la langue active, en vue
            first = max(0, self._values.index(self._current_value) - rows // 2)
            self._list._parent_canvas.yview_moveto(first / len(self._values))
        self._anchor = self._window_box()
        popup.bind("<Escape>", lambda _e: self.close_dropdown())
        popup.bind("<FocusOut>", lambda _e: self.after(10, self._close_if_focus_left))
        popup.focus_force()

    def _close_if_focus_left(self) -> None:
        # FocusOut remonte aussi d'un widget à l'autre DANS la liste : on ne
        # ferme que si le focus est parti ailleurs (fenêtre principale, autre
        # application).
        if self._popup is None:
            return
        try:
            focus = self._popup.focus_get()
        except KeyError:          # focus sur un widget que Tk ne connaît pas
            focus = None
        if focus is None or not str(focus).startswith(str(self._popup)):
            self.close_dropdown()

    def _pick(self, value: str) -> None:
        self.close_dropdown()
        self._dropdown_callback(value)

    def close_dropdown(self) -> None:
        if self._popup is None:
            return
        popup, self._popup, self._list = self._popup, None, None
        self._closed_at = time.monotonic()
        popup.destroy()
