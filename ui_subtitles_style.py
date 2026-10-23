"""Fenêtre Style du widget de sous-titres.

Pas d'aperçu dessiné ici : chaque réglage part tout de suite vers les pages
ouvertes (OBS, ou le navigateur via « Ouvrir »), qui SONT l'aperçu, au pixel
près. L'écriture sur disque, elle, attend que l'utilisateur s'arrête
(`SAVE_DELAY_MS`) : un curseur qu'on fait glisser enverrait sinon une
écriture par cran.

Les contrôles sont décrits par une table (`SECTIONS`) plutôt qu'écrits un à
un : ajouter un champ au style revient à ajouter une ligne ici.
"""
from __future__ import annotations

import tkinter.font as tkfont
from tkinter import colorchooser
from typing import Any, Callable, Optional

import subtitles_style as sstyle
from i18n import t
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER, COL_CARD,
                       COL_TEXT, COL_TEXT_MUTED, SmoothScroll, ctk, font)

SAVE_DELAY_MS = 400

#: (clé i18n du titre, [(champ, type, options)])
SECTIONS: tuple = (
    ("SUBS_STY_PRESET", (("preset", "choice", tuple(sstyle.PRESETS) + (sstyle.CUSTOM,)),)),
    ("SUBS_STY_TEXT", (("font_family", "font", None),
                       ("font_size", "slider", "px"),
                       ("font_weight", "choice", sstyle.WEIGHTS),
                       ("italic", "switch", None),
                       ("uppercase", "switch", None),
                       ("letter_spacing", "slider", "px"),
                       ("align", "choice", sstyle.ALIGNS))),
    ("SUBS_STY_COLORS", (("text_color", "color", None),
                         ("partial_color", "color", None),
                         ("highlight_on", "switch", None),
                         ("highlight_color", "color", None))),
    ("SUBS_STY_READABILITY", (("outline_width", "slider", "px"),
                              ("outline_color", "color", None),
                              ("shadow_blur", "slider", "px"),
                              ("shadow_offset", "slider", "px"),
                              ("shadow_color", "color", None))),
    ("SUBS_STY_BACKGROUND", (("bg_mode", "choice", sstyle.BACKGROUNDS),
                             ("bg_color", "color", None),
                             ("bg_opacity", "slider", "%"),
                             ("bg_radius", "slider", "px"),
                             ("bg_padding", "slider", "px"))),
    ("SUBS_STY_LAYOUT", (("lines", "choice", (1, 2, 3, 4)),
                         ("max_width", "slider", "%"),
                         ("hold_s", "slider", "s"),
                         ("animation", "choice", sstyle.ANIMATIONS))),
)

#: Clé i18n du libellé de chaque option textuelle.
_OPTION_KEYS = {
    "tv": "SUBS_PRESET_TV", "outline": "SUBS_PRESET_OUTLINE", "band": "SUBS_PRESET_BAND",
    "karaoke": "SUBS_PRESET_KARAOKE", "minimal": "SUBS_PRESET_MINIMAL",
    # `band` (bandeau) sert de préréglage ET de mode de fond : un seul libellé.
    sstyle.CUSTOM: "SUBS_PRESET_CUSTOM",
    "left": "SUBS_ALIGN_LEFT", "center": "SUBS_ALIGN_CENTER", "right": "SUBS_ALIGN_RIGHT",
    "none": "SUBS_OPT_NONE", "box": "SUBS_BG_BOX", "band": "SUBS_BG_BAND",
    "fade": "SUBS_ANIM_FADE", "word": "SUBS_ANIM_WORD",
}

_FIELD_KEYS = {
    "preset": "SUBS_STY_PRESET", "font_family": "SUBS_F_FONT", "font_size": "SUBS_F_SIZE",
    "font_weight": "SUBS_F_WEIGHT", "italic": "SUBS_F_ITALIC",
    "uppercase": "SUBS_F_UPPERCASE", "letter_spacing": "SUBS_F_SPACING",
    "align": "SUBS_F_ALIGN", "text_color": "SUBS_F_TEXT_COLOR",
    "partial_color": "SUBS_F_PARTIAL_COLOR", "highlight_on": "SUBS_F_HIGHLIGHT",
    "highlight_color": "SUBS_F_HIGHLIGHT_COLOR", "outline_width": "SUBS_F_OUTLINE",
    "outline_color": "SUBS_F_OUTLINE_COLOR", "shadow_blur": "SUBS_F_SHADOW_BLUR",
    "shadow_offset": "SUBS_F_SHADOW_OFFSET", "shadow_color": "SUBS_F_SHADOW_COLOR",
    "bg_mode": "SUBS_F_BG_MODE", "bg_color": "SUBS_F_BG_COLOR",
    "bg_opacity": "SUBS_F_BG_OPACITY", "bg_radius": "SUBS_F_BG_RADIUS",
    "bg_padding": "SUBS_F_BG_PADDING", "lines": "SUBS_F_LINES",
    "max_width": "SUBS_F_MAX_WIDTH", "hold_s": "SUBS_F_HOLD", "animation": "SUBS_F_ANIMATION",
}


def _option_label(value: Any) -> str:
    key = _OPTION_KEYS.get(value)
    return t(key) if key else str(value)


def installed_fonts(root) -> list[str]:
    """Familles installées, sans les variantes verticales `@…` des polices
    asiatiques ni les noms que la page refuserait."""
    names = {f for f in tkfont.families(root)
             if not f.startswith("@") and sstyle._FONT.fullmatch(f)}
    return sorted(names, key=str.lower)


class SubtitleStyleDialog(ctk.CTkToplevel):

    def __init__(self, master, style: sstyle.Style,
                 on_change: Callable[[sstyle.Style, bool], None]) -> None:
        """`on_change(style, persist)` : persist=False pendant un réglage en
        cours, True quand il faut écrire."""
        super().__init__(master, fg_color=COL_BG)
        self.title(t("SUBS_STYLE_TITLE"))
        self.geometry("600x760")
        self.minsize(520, 480)
        self.transient(master.winfo_toplevel())
        self._style = style
        self._on_change = on_change
        self._save_job: Optional[str] = None
        self._widgets: dict[str, Any] = {}
        self._values: dict[str, Any] = {}   # libellé affiché -> valeur, par champ

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text=t("SUBS_STYLE_HINT"), font=font(11),
                     text_color=COL_TEXT_MUTED, justify="left", wraplength=540,
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 8))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=12)
        body.grid_columnconfigure(0, weight=1)
        self._scroller = SmoothScroll(body)
        fonts = installed_fonts(self)
        for row, (title_key, controls) in enumerate(SECTIONS):
            card = ctk.CTkFrame(body, fg_color=COL_CARD, corner_radius=12,
                                border_width=1, border_color=COL_BORDER)
            card.grid(row=row, column=0, sticky="ew", padx=8, pady=6)
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=t(title_key), font=font(13, "bold"),
                         text_color=COL_TEXT, anchor="w").grid(
                row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(12, 4))
            for i, (field, kind, extra) in enumerate(controls, start=1):
                self._build(card, i, field, kind, extra, fonts)
            ctk.CTkFrame(card, height=8, fg_color="transparent").grid(row=len(controls) + 1)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=14)
        ctk.CTkButton(bar, text=t("SUBS_STYLE_RESET"), width=150, height=36,
                      corner_radius=9, fg_color="transparent", border_width=1,
                      border_color=COL_BORDER, hover_color=COL_CARD, text_color=COL_TEXT,
                      font=font(12), command=lambda: self._apply_preset("tv")).pack(side="left")
        ctk.CTkButton(bar, text=t("SUBS_STYLE_CLOSE"), width=130, height=36,
                      corner_radius=9, fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                      text_color=COL_BG, font=font(12, "bold"),
                      command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._load_values()
        self._lift_job = self.after(50, self.lift)

    # -- construction ------------------------------------------------------ #

    def _build(self, card, row: int, field: str, kind: str, extra: Any,
               fonts: list[str]) -> None:
        if field != "preset":
            ctk.CTkLabel(card, text=t(_FIELD_KEYS[field]), font=font(12),
                         text_color=COL_TEXT_MUTED, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(16, 12), pady=5)
        col, span = (0, 3) if field == "preset" else (1, 2)
        pad = (16, 16) if field == "preset" else (0, 16)

        if kind == "choice":
            labels = {_option_label(v): v for v in extra}
            self._values[field] = labels
            w = ctk.CTkSegmentedButton(card, values=list(labels), font=font(11),
                                       selected_color=COL_ACCENT,
                                       selected_hover_color=COL_ACCENT_HOVER,
                                       unselected_color=COL_BG, text_color=COL_TEXT,
                                       command=lambda lbl, f=field: self._choice(f, lbl))
            w.grid(row=row, column=col, columnspan=span, sticky="ew", padx=pad, pady=5)
        elif kind == "slider":
            lo, hi = sstyle.RANGES[field]
            frame = ctk.CTkFrame(card, fg_color="transparent")
            frame.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=5)
            frame.grid_columnconfigure(0, weight=1)
            value_lbl = ctk.CTkLabel(frame, text="", width=54, font=font(11),
                                     text_color=COL_TEXT, anchor="e")
            value_lbl.grid(row=0, column=1, padx=(10, 0))
            w = ctk.CTkSlider(frame, from_=lo, to=hi, number_of_steps=int(hi - lo),
                              progress_color=COL_ACCENT, button_color=COL_ACCENT,
                              button_hover_color=COL_ACCENT_HOVER,
                              command=lambda v, f=field: self._slide(f, v))
            w.grid(row=0, column=0, sticky="ew")
            w.unit, w.value_lbl = extra, value_lbl
        elif kind == "switch":
            w = ctk.CTkSwitch(card, text="", progress_color=COL_ACCENT,
                              command=lambda f=field: self._set(f, bool(self._widgets[f].get())))
            w.grid(row=row, column=1, sticky="w", pady=5)
        elif kind == "color":
            w = ctk.CTkButton(card, text="", width=64, height=26, corner_radius=6,
                              border_width=1, border_color=COL_BORDER,
                              command=lambda f=field: self._pick_color(f))
            w.grid(row=row, column=1, sticky="w", pady=5)
        else:  # font
            w = ctk.CTkComboBox(card, values=fonts, height=30, font=font(12),
                                dropdown_font=font(12), fg_color=COL_BG,
                                border_color=COL_BORDER, button_color=COL_BORDER,
                                command=lambda name: self._set("font_family", name))
            w.grid(row=row, column=1, columnspan=2, sticky="ew", padx=(0, 16), pady=5)
            w.bind("<Return>", lambda _e: self._set("font_family", w.get().strip()))
            w.bind("<FocusOut>", lambda _e: self._set("font_family", w.get().strip()))
        self._widgets[field] = w

    # -- valeurs ----------------------------------------------------------- #

    def _load_values(self) -> None:
        """Remet chaque contrôle sur le style courant (sans déclencher leurs
        commandes : `set`, `select` et `deselect` n'en appellent aucune)."""
        s = self._style
        for _title, controls in SECTIONS:
            for field, kind, _extra in controls:
                w, value = self._widgets[field], getattr(s, field)
                if kind == "choice":
                    w.set(_option_label(value))
                elif kind == "slider":
                    w.set(value)
                    w.value_lbl.configure(text=f"{value} {w.unit}")
                elif kind == "switch":
                    w.select() if value else w.deselect()
                elif kind == "color":
                    w.configure(fg_color=value, hover_color=value)
                else:
                    w.set(value)

    def _choice(self, field: str, label: str) -> None:
        value = self._values[field][label]
        if field == "preset":
            if value != sstyle.CUSTOM:
                self._apply_preset(value)
            else:
                self._widgets["preset"].set(_option_label(self._style.preset))
            return
        self._set(field, value)

    def _slide(self, field: str, value: float) -> None:
        w = self._widgets[field]
        w.value_lbl.configure(text=f"{int(round(value))} {w.unit}")
        self._set(field, int(round(value)))

    def _pick_color(self, field: str) -> None:
        try:
            _rgb, hexa = colorchooser.askcolor(color=getattr(self._style, field), parent=self)
        except Exception:
            return
        if hexa:
            self._set(field, hexa.upper())

    def _set(self, field: str, value: Any) -> None:
        style = sstyle.edit(self._style, **{field: value})
        if style == self._style:
            if field == "font_family":
                self._widgets[field].set(self._style.font_family)   # nom refusé
            return
        self._style = style
        self._widgets["preset"].set(_option_label(style.preset))
        if field.endswith("_color"):
            self._widgets[field].configure(fg_color=value, hover_color=value)
        self._push()

    def _apply_preset(self, key: str) -> None:
        self._style = sstyle.PRESETS[key]
        self._load_values()
        self._push()

    def _push(self) -> None:
        self._on_change(self._style, False)
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(SAVE_DELAY_MS, self._save)

    def _save(self) -> None:
        self._save_job = None
        self._on_change(self._style, True)

    def close(self) -> None:
        self.after_cancel(self._lift_job)
        if self._save_job is not None:
            self.after_cancel(self._save_job)
            self._save()
        self.destroy()
