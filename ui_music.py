"""
ui_music.py — Onglet Widget Musique.

Étage d'essai : il montre, brut, ce que `music_smtc` obtient de Windows pour
chaque lecteur ouvert. Le but n'est pas encore d'habiller un overlay, mais de
répondre à la seule question qui conditionne la suite : quelles applications
publient réellement titre, artiste et pochette sur cette machine, et à quelle
résolution.

Avec `music_smtc.py`, ces deux fichiers forment la totalité de la
fonctionnalité. Les retirer et défaire le câblage dans `obs_dynamics.py`
(import, bouton de navigation, instanciation, `refresh_labels`, `stop`) suffit
à revenir à l'état d'avant, sans résidu ailleurs.
"""
from __future__ import annotations

import io
import tkinter as tk

from tkinter import messagebox
from typing import Any, Callable, Optional

from i18n import t
from music_catalog import BUILT_IN, MusicApp, identify, logo_path
from music_overlay import MusicHub
from music_smtc import (IMPORT_ERROR, SMTC_TIMEOUT, MusicWatcher, Session,
                        available)
from music_style import Style, StyleStore, overlay_size, preview_png
from ui_music_style import StyleDialog
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER,
                       COL_BORDER_ACCENT, COL_CARD, COL_CARD_HOVER, COL_GREEN,
                       COL_RED, COL_TEXT, COL_TEXT_MUTED, COL_YELLOW,
                       FONT_FAMILY, SmoothScroll, ctk, font)

try:
    from PIL import Image, ImageDraw, ImageTk
except Exception:                             # pragma: no cover
    Image = None                              # type: ignore[assignment]


#: Commande d'installation affichée quand les bindings manquent. `winsdk`,
#: qu'on trouve dans presque tous les exemples SMTC en ligne, n'a pas de roue
#: pour Python 3.14 : ce sont les paquets `winrt-*` qu'il faut.
PIP_HINT = ("pip install winrt-Windows.Media.Control "
            "winrt-Windows.Storage.Streams winrt-Windows.Foundation.Collections")

#: Couleur de la pastille d'état, par statut SMTC.
_STATUS_COLORS = {
    "PLAYING": COL_GREEN,
    "PAUSED": COL_YELLOW,
    "STOPPED": COL_TEXT_MUTED,
    "CLOSED": COL_TEXT_MUTED,
    "CHANGING": COL_YELLOW,
    "OPENED": COL_TEXT_MUTED,
}

#: Hauteur de la vignette, en pixels. C'est la HAUTEUR qui est fixe, pas le
#: côté : toutes les vignettes s'alignent sur la même ligne de base, et c'est
#: la largeur qui suit le rapport de l'image.
_COVER_H = 76

#: Largeur maximale d'une vignette. Sans ce plafond, une image très
#: panoramique repousserait le texte de la carte hors de l'écran.
_COVER_MAX_W = 135

#: Largeur minimale, pour une vignette en portrait.
_COVER_MIN_W = 44

#: Aperçu du rendu de l'overlay, à gauche du bouton Widget.
_STYLE_PREVIEW = (168, 54)


def fitted_cover_size(session: Session) -> tuple[int, int]:
    """Taille d'affichage d'une vignette, à rapport conservé.

    Une pochette d'album rend un carré — le format du futur vinyle. Une
    miniature de vidéo rend un rectangle à son propre rapport, au lieu d'être
    compressée dans un carré : écrasée, elle devient illisible et ne
    ressemble plus à la vidéo qu'elle annonce.
    """
    size = session.thumbnail_size
    if session.is_square_art or not size or size[1] <= 0:
        return (_COVER_H, _COVER_H)
    width = round(_COVER_H * size[0] / size[1])
    return (max(_COVER_MIN_W, min(width, _COVER_MAX_W)), _COVER_H)


def _mmss(ms: int) -> str:
    if ms <= 0:
        return "--:--"
    seconds = ms // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


_SUR = 4          # suréchantillonnage des arrondis, pour des bords lisses


def _boite(w: int, h: int, rayon: int, fond: str, contour: Optional[str] = None):
    """Rectangle arrondi anticrénelé, transparent hors de l'arrondi."""
    grand = Image.new("RGBA", (w * _SUR, h * _SUR), (0, 0, 0, 0))
    ImageDraw.Draw(grand).rounded_rectangle(
        (0, 0, w * _SUR - 1, h * _SUR - 1), radius=rayon * _SUR, fill=fond,
        outline=contour, width=_SUR if contour else 0)
    return grand.resize((w, h), Image.Resampling.LANCZOS)


def _arrondir(image, rayon: int):
    """Même image, coins arrondis."""
    w, h = image.size
    masque = Image.new("L", (w * _SUR, h * _SUR), 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, w * _SUR - 1, h * _SUR - 1),
                                             radius=rayon * _SUR, fill=255)
    sortie = image.convert("RGBA")
    sortie.putalpha(masque.resize((w, h), Image.Resampling.LANCZOS))
    return sortie


class SessionCard(tk.Canvas):
    """Une session SMTC : pochette à gauche, métadonnées à droite.

    Toute la carte est peinte dans UN canvas — fond arrondi, textes,
    pochette, aperçu et boutons — au lieu d'être assemblée en widgets CTk.
    Elle en comptait 32 à 45 fenêtres Tk (270 pour la liste), chacune
    repeinte à son rythme pendant le défilement : c'étaient les rayures. Il
    n'en reste que deux, le canvas et le champ du lien, gardé réel pour
    qu'on puisse sélectionner l'URL à la main.
    """

    def __init__(self, master, app: MusicApp, session: Optional[Session] = None,
                 overlay_url: str = "", store: Optional[StyleStore] = None,
                 on_style_saved: Optional[Callable[[str, Style], None]] = None) -> None:
        # Le fond de la liste, visible dans les coins arrondis. CTk refuse
        # cget("bg") sur ses cadres : on le lit sur le canvas qu'il défile.
        fond = getattr(master, "_parent_canvas", None)
        super().__init__(master, highlightthickness=0, bd=0, height=1,
                         bg=fond.cget("bg") if fond is not None else COL_BG)
        try:
            self._s = ctk.ScalingTracker.get_widget_scaling(master) or 1.0
        except Exception:
            self._s = 1.0
        self._app = app
        self._session = session
        self._url = overlay_url
        self._store = store
        self._on_style_saved = on_style_saved
        self._bord = (COL_BORDER_ACCENT if session is not None and session.is_current
                      else COL_BORDER)
        # Tk ne garde qu'un nom vers chaque image, pas l'objet Python : sans
        # ces références le ramasse-miettes les libère et le canvas reste vide.
        self._photos: dict[str, Any] = {}
        self._note = False           # session sans pochette : on écrit ♪
        self._vignette = self._photo_gauche()
        self._apercu: Any = None
        self._apercu_id: Optional[int] = None
        self._taille_id: Optional[int] = None
        self._lien_txt: Optional[int] = None
        self._largeur = 0

        self._champ: Optional[tk.Entry] = None
        if overlay_url:
            # Lecture seule, mais sélectionnable : l'utilisateur doit pouvoir
            # copier à la main si le presse-papiers lui échappe.
            self._champ = tk.Entry(self, font=self._f(11), relief="flat", bd=0,
                                   fg=COL_TEXT_MUTED, bg=COL_BG,
                                   readonlybackground=COL_BG,
                                   highlightthickness=1,
                                   highlightbackground=COL_BORDER,
                                   highlightcolor=COL_BORDER_ACCENT)
            self._champ.insert(0, overlay_url)
            self._champ.configure(state="readonly")
            if store is not None:
                self._refresh_style_preview(redessiner=False)
        self.bind("<Configure>", self._on_configure)

    # -- mesures ----------------------------------------------------------- #

    def _p(self, valeur: float) -> int:
        """Unités CTk -> pixels, mise à l'échelle de Windows comprise."""
        return round(valeur * self._s)

    def _f(self, taille: int, graisse: str = "normal") -> tuple:
        return (FONT_FAMILY, -self._p(taille), graisse)

    def _photo(self, nom: str, image) -> Any:
        self._photos[nom] = ImageTk.PhotoImage(image)
        return self._photos[nom]

    # -- images ------------------------------------------------------------ #

    def _photo_gauche(self) -> Any:
        """Pochette de la session, sinon logo du lecteur. Sans logo : None,
        le monogramme est alors écrit en texte."""
        if Image is None:
            return None
        if self._session is not None:
            w, h = fitted_cover_size(self._session)
            taille = (self._p(w), self._p(h))
            image = self._decode(self._session, taille)
            if image is not None:
                return self._photo("gauche", _arrondir(image, self._p(10)))
            self._note = True
            return self._photo("gauche", _boite(*taille, self._p(10), COL_BG))
        chemin = logo_path(self._app.key)
        if chemin is None:
            return None
        cote = self._p(_COVER_H)
        fond = _boite(cote, cote, self._p(12), COL_BG)
        try:
            with Image.open(chemin) as brut:
                interieur = cote - self._p(18)
                logo = brut.convert("RGBA").resize((interieur, interieur),
                                                   Image.Resampling.LANCZOS)
            fond.alpha_composite(logo, (self._p(9), self._p(9)))
        except Exception:
            return None
        return self._photo("gauche", fond)

    @staticmethod
    def _decode(session: Session, size: tuple[int, int]):
        if session.thumbnail is None or Image is None:
            return None
        try:
            with Image.open(io.BytesIO(session.thumbnail)) as raw:
                return raw.convert("RGB").resize(size, Image.Resampling.LANCZOS)
        except Exception:
            return None

    def _refresh_style_preview(self, redessiner: bool = True) -> None:
        if self._store is None or Image is None:
            return
        style = self._store.get(self._app.key)
        # Rendu au double puis réduit : net quelle que soit la mise à l'échelle.
        png = preview_png(style, width=_STYLE_PREVIEW[0],
                          height=_STYLE_PREVIEW[1], echelle=2,
                          background=self._store.background_path(self._app.key))
        try:
            with Image.open(io.BytesIO(png)) as brut:
                # RGBA : les coins arrondis laissent voir la carte derrière.
                image = brut.convert("RGBA").resize(
                    (self._p(_STYLE_PREVIEW[0]), self._p(_STYLE_PREVIEW[1])),
                    Image.Resampling.LANCZOS)
            self._apercu = self._photo("apercu", image)
        except Exception:
            return
        if redessiner and self._apercu_id is not None:
            self.itemconfigure(self._apercu_id, image=self._apercu)

    # -- dessin ------------------------------------------------------------ #

    def _on_configure(self, event: Any) -> None:
        # Changer la hauteur renvoie un <Configure> : on ne redessine que
        # quand la LARGEUR change, sinon on bouclerait.
        if event.width != self._largeur:
            self._largeur = event.width
            self._dessiner()

    def _texte(self, x: int, y: int, texte: str, taille: int, couleur: str,
               graisse: str = "normal", largeur: int = 0, ancre: str = "nw") -> tuple:
        item = self.create_text(x, y, text=texte, anchor=ancre, fill=couleur,
                                font=self._f(taille, graisse), width=largeur)
        return item, self.bbox(item)

    def _dessiner(self) -> None:
        p = self._p
        W = self._largeur
        self.delete("all")
        x0 = p(16 + _COVER_MAX_W + 14)
        largeur_texte = max(p(120), W - x0 - p(16))

        if self._vignette is not None:
            self.create_image(p(16), p(16), image=self._vignette, anchor="nw")
            bas_gauche = p(16) + self._vignette.height()
            if self._note:
                self._texte(p(16) + self._vignette.width() // 2,
                            p(16) + self._vignette.height() // 2, "♪", 26,
                            COL_TEXT_MUTED, ancre="center")
        else:
            self._texte(p(16 + _COVER_H / 2), p(16 + _COVER_H / 2), self._app.monogram,
                        24, self._app.color, "bold", ancre="center")
            bas_gauche = p(16 + _COVER_H)

        session = self._session
        if session is None:
            _i, bb = self._texte(x0, p(18), self._app.label, 15, COL_TEXT_MUTED, "bold")
            _i, bb = self._texte(x0, bb[3] + p(2), t("MUSIC_OFFLINE"), 11, COL_TEXT_MUTED)
        else:
            x, milieu = x0, p(16 + 9)
            tete = [("●", 12, "normal", _STATUS_COLORS.get(session.status, COL_TEXT_MUTED), 2),
                    (t(f"MUSIC_STATUS_{session.status}"), 11, "bold", COL_TEXT_MUTED, 10),
                    (self._app.label, 11, "bold", self._app.color, 10)]
            if session.is_current:
                tete.append((t("MUSIC_BADGE_CURRENT"), 10, "bold", COL_ACCENT, 0))
            for texte, taille, graisse, couleur, ecart in tete:
                _i, bb = self._texte(x, milieu, texte, taille, couleur, graisse, ancre="w")
                x = bb[2] + p(ecart)
            _i, bb = self._texte(x0, p(16 + 22), session.title or t("MUSIC_NO_TITLE"),
                                 15, COL_TEXT, "bold", largeur_texte)
            sous_titre = session.artist or t("MUSIC_NO_ARTIST")
            if session.album:
                sous_titre = f"{sous_titre} — {session.album}"
            _i, bb = self._texte(x0, bb[3] + p(2), sous_titre, 12, COL_TEXT_MUTED,
                                 largeur=largeur_texte)
            _i, bb = self._texte(x0, bb[3] + p(8), self._facts(session), 10,
                                 COL_TEXT_MUTED, largeur=largeur_texte)

        y = max(bas_gauche, bb[3]) + p(16)
        if session is not None and session.errors:
            _i, bb = self._texte(p(16), y, " · ".join(session.errors), 10, COL_RED,
                                 largeur=W - p(32))
            y = bb[3] + p(14)

        if self._champ is not None:
            y = self._dessiner_lien(y)

        self._fond(W, y)
        self.configure(height=y)

    def _dessiner_lien(self, y: int) -> int:
        p = self._p
        W = self._largeur
        if self._store is not None:
            cadre = (p(_STYLE_PREVIEW[0] + 4), p(_STYLE_PREVIEW[1] + 4))
            self.create_image(p(16), y, anchor="nw", image=self._photo(
                "cadre", _boite(*cadre, p(8), COL_BG, COL_BORDER)))
            if self._apercu is not None:
                self._apercu_id = self.create_image(p(16) + cadre[0] // 2,
                                                    y + cadre[1] // 2, image=self._apercu)
            self._bouton("widget", W - p(16 + 150), y + (cadre[1] - p(34)) // 2,
                         150, 34, 12, t("MUSIC_BTN_WIDGET"), COL_ACCENT,
                         COL_ACCENT_HOVER, None, COL_BG, self._open_style)
            y += cadre[1] + p(8)

        bouton_x = W - p(16 + 160)
        self.create_window(p(16), y, anchor="nw", window=self._champ,
                           width=max(p(80), bouton_x - p(10 + 16)), height=p(32))
        self._lien_txt = self._bouton("lien", bouton_x, y, 160, 32, 11,
                                      t("MUSIC_BTN_COPY_LINK"), COL_CARD, COL_CARD_HOVER,
                                      COL_BORDER_ACCENT, COL_ACCENT, self._copy_link)
        _i, bb = self._texte(p(16), y + p(32 + 6), t("MUSIC_OVERLAY_HINT"), 10,
                             COL_TEXT_MUTED, largeur=W - p(32))
        # La taille à saisir dans OBS, à côté du lien qu'on y colle.
        self._taille_id, bb = self._texte(p(16), bb[3] + p(2), " ", 10, COL_ACCENT, "bold")
        self._refresh_size()
        return self.bbox(self._taille_id)[3] + p(16)

    def _bouton(self, nom: str, x: int, y: int, w: int, h: int, taille: int,
                texte: str, fond: str, survol: str, contour: Optional[str],
                couleur: str, action: Callable[[], None]) -> int:
        """Bouton peint : deux images (repos, survol) et un texte, même tag."""
        p = self._p
        if f"{nom}-repos" not in self._photos:
            self._photo(f"{nom}-repos", _boite(p(w), p(h), p(8), fond, contour))
            self._photo(f"{nom}-survol", _boite(p(w), p(h), p(8), survol, contour))
        repos, dessus = self._photos[f"{nom}-repos"], self._photos[f"{nom}-survol"]
        tag = f"btn-{nom}"
        image = self.create_image(x, y, anchor="nw", image=repos, tags=tag)
        texte_id = self.create_text(x + p(w) // 2, y + p(h) // 2, text=texte,
                                    fill=couleur, font=self._f(taille, "bold"), tags=tag)

        def entrer(_e: Any) -> None:
            self.itemconfigure(image, image=dessus)
            self.configure(cursor="hand2")

        def sortir(_e: Any) -> None:
            self.itemconfigure(image, image=repos)
            self.configure(cursor="")

        self.tag_bind(tag, "<Enter>", entrer)
        self.tag_bind(tag, "<Leave>", sortir)
        self.tag_bind(tag, "<ButtonRelease-1>", lambda _e: action())
        return texte_id

    def _fond(self, W: int, H: int) -> None:
        """Fond arrondi de la carte : quatre coins en image, le reste en
        rectangles Tk — rien de lourd à refaire quand la largeur change."""
        r = self._p(14)
        if "coin0" not in self._photos:
            rond = _boite(2 * r, 2 * r, r, COL_CARD, self._bord)
            for i, zone in enumerate(((0, 0, r, r), (r, 0, 2 * r, r),
                                      (0, r, r, 2 * r), (r, r, 2 * r, 2 * r))):
                self._photo(f"coin{i}", rond.crop(zone))
        coins = [self._photos[f"coin{i}"] for i in range(4)]
        items = [
            self.create_rectangle(r, 0, W - r, H, fill=COL_CARD, width=0),
            self.create_rectangle(0, r, W, H - r, fill=COL_CARD, width=0),
            self.create_line(r, 0, W - r, 0, fill=self._bord),
            self.create_line(r, H - 1, W - r, H - 1, fill=self._bord),
            self.create_line(0, r, 0, H - r, fill=self._bord),
            self.create_line(W - 1, r, W - 1, H - r, fill=self._bord),
            self.create_image(0, 0, image=coins[0], anchor="nw"),
            self.create_image(W, 0, image=coins[1], anchor="ne"),
            self.create_image(0, H, image=coins[2], anchor="sw"),
            self.create_image(W, H, image=coins[3], anchor="se"),
        ]
        for item in reversed(items):
            self.tag_lower(item)

    # -- actions ----------------------------------------------------------- #

    def _open_style(self) -> None:
        if self._store is None:
            return
        StyleDialog(self, self._app.key, self._app.label, self._store,
                    self._style_saved)

    def _style_saved(self, key: str, style: Style) -> None:
        self._refresh_style_preview()
        self._refresh_size()
        if self._on_style_saved is not None:
            self._on_style_saved(key, style)

    def _refresh_size(self) -> None:
        if self._store is None or self._taille_id is None:
            return
        largeur, hauteur = overlay_size(self._store.get(self._app.key))
        self.itemconfigure(self._taille_id, text=t("MUSIC_SOURCE_SIZE", w=largeur,
                                                   h=hauteur))

    def _copy_link(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._url)
        self._libelle_lien(t("MUSIC_BTN_LINK_COPIED"))
        self.after(1500, lambda: self._libelle_lien(t("MUSIC_BTN_COPY_LINK")))

    def _libelle_lien(self, texte: str) -> None:
        try:
            if self._lien_txt is not None:
                self.itemconfigure(self._lien_txt, text=texte)
        except tk.TclError:
            pass        # carte remplacée entre-temps


    @staticmethod
    def _facts(session: Session) -> str:
        """La ligne technique : c'est elle qui tranche le tableau des sources."""
        if session.thumbnail is None:
            cover = t("MUSIC_COVER_NONE")
        else:
            size = session.thumbnail_size
            dims = f"{size[0]}x{size[1]}" if size else "?"
            # Appeler « pochette » la miniature d'une vidéo serait faux, et
            # c'est précisément la distinction que cet onglet doit rendre
            # lisible.
            cle = "MUSIC_COVER_OK" if session.is_square_art else "MUSIC_THUMB_OK"
            cover = t(cle, format=session.thumbnail_format or "?",
                      dims=dims, kb=len(session.thumbnail) // 1024)
        position = f"{_mmss(session.position_ms)} / {_mmss(session.duration_ms)}"
        return f"{session.app_id}\n{cover}  ·  {position}"


class MusicView(ctk.CTkFrame):
    """Onglet Widget Musique : la liste vivante des sessions SMTC."""

    def __init__(self, master, post_ui: Callable[[Callable[[], None]], None],
                 hub: Optional[MusicHub] = None,
                 overlay: Optional[Any] = None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._sessions: list[Session] = []
        self._error: str = ""
        self._hub = hub
        self._overlay = overlay
        #: Carte affichée par clé de lecteur, avec ce qu'elle montre.
        self._cartes: dict[str, tuple[Any, SessionCard]] = {}
        self._bandeau: Optional[ctk.CTkFrame] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        self._title_lbl = ctk.CTkLabel(header, text=t("MUSIC_TITLE"),
                                       font=font(22, "bold"), text_color=COL_TEXT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(header, text=t("MUSIC_SUBTITLE"), font=font(11),
                                          text_color=COL_TEXT_MUTED, justify="left",
                                          wraplength=780)
        self._subtitle_lbl.pack(anchor="w", pady=(2, 0))

        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.grid(row=1, column=0, sticky="ew", padx=28, pady=(6, 10))
        self._refresh_btn = ctk.CTkButton(toolbar, text=t("MUSIC_BTN_REFRESH"), width=130,
                                          height=36, corner_radius=9, fg_color=COL_ACCENT,
                                          hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                                          font=font(12, "bold"), command=self._refresh)
        self._refresh_btn.pack(side="left")
        self._copy_btn = ctk.CTkButton(toolbar, text=t("MUSIC_BTN_COPY"), width=150,
                                       height=36, corner_radius=9, fg_color="transparent",
                                       border_width=1, border_color=COL_BORDER,
                                       hover_color=COL_CARD, text_color=COL_TEXT,
                                       font=font(12), command=self._copy_report)
        self._copy_btn.pack(side="left", padx=(10, 0))
        # Même allure que celui du chat : discret, parce qu'il casse chaque
        # source déjà collée dans OBS.
        self._regen_btn = ctk.CTkButton(toolbar, text=t("MUSIC_BTN_REGENERATE"),
                                        width=170, height=36, corner_radius=9,
                                        fg_color="transparent", border_width=1,
                                        border_color=COL_BORDER,
                                        hover_color=COL_CARD,
                                        text_color=COL_TEXT_MUTED, font=font(12),
                                        command=self._regenerate,
                                        state="normal" if hub else "disabled")
        self._regen_btn.pack(side="left", padx=(10, 0))
        self._status_lbl = ctk.CTkLabel(toolbar, text="", font=font(11),
                                        text_color=COL_TEXT_MUTED)
        self._status_lbl.pack(side="left", padx=(16, 0))

        self._logo_lbl = ctk.CTkLabel(self, text=t("MUSIC_LOGO_HINT"), font=font(10),
                                      text_color=COL_TEXT_MUTED, anchor="w",
                                      justify="left", wraplength=880)
        self._logo_lbl.grid(row=2, column=0, sticky="ew", padx=28, pady=(0, 10))

        self._list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._list.grid(row=3, column=0, sticky="nsew", padx=22, pady=(0, 22))
        self._list.grid_columnconfigure(0, weight=1)
        self._scroller = SmoothScroll(self._list)

        self._watcher: Optional[MusicWatcher] = None
        if available():
            self._watcher = MusicWatcher(on_update=self._apply_sessions,
                                         dispatch=post_ui,
                                         on_error=self._apply_error)
            self._watcher.start()
        else:
            self._error = IMPORT_ERROR or "?"
        self._render()

    # -- cycle de vie ------------------------------------------------------ #

    def stop(self) -> None:
        """Rend les abonnements SMTC. Appelée à la fermeture de la fenêtre."""
        if self._watcher is not None:
            self._watcher.stop()

    # -- entrées ----------------------------------------------------------- #

    def _refresh(self) -> None:
        if self._watcher is not None:
            self._watcher.refresh_now()

    def _apply_sessions(self, sessions: list[Session]) -> None:
        self._sessions = sessions
        self._error = ""
        # Le hub d'abord : les overlays déjà ouverts dans OBS doivent suivre le
        # morceau même quand cet onglet n'est pas affiché.
        if self._hub is not None:
            self._hub.publish(sessions)
        self._render()

    def _style_saved(self, key: str, _style: Style) -> None:
        """Pousse le nouveau style vers les overlays déjà ouverts dans OBS."""
        if self._hub is not None:
            self._hub.publish_style(key)

    def _overlay_url(self, app: MusicApp) -> str:
        """URL de l'overlay d'un lecteur.

        Elle se calcule depuis la CLÉ du catalogue, pas depuis une session :
        c'est ce qui la rend disponible avant même le premier morceau, et
        identique d'une exécution à l'autre.
        """
        if self._overlay is None:
            return ""
        return self._overlay.music_url(app.key)

    def _regenerate(self) -> None:
        """Invalide les liens de toutes les sources musique, après accord."""
        if self._hub is None:
            return
        if not messagebox.askyesno(t("CONFIRM_DIALOG_TITLE"),
                                   t("MUSIC_CONFIRM_REGENERATE"), parent=self):
            return
        try:
            self._hub.regenerate_token()
        except OSError:
            messagebox.showerror(t("MUSIC_ERROR"), t("MUSIC_REGENERATE_FAILED"),
                                 parent=self)
            return
        self._render()                # les cartes affichent les nouveaux liens

    def _apply_error(self, message: str) -> None:
        """Affiche l'erreur, traduite quand la sonde en donne la raison.

        Les autres messages sont des exceptions brutes : elles partent telles
        quelles dans le relevé à coller dans un ticket.
        """
        self._error = (t("MUSIC_ERROR_TIMEOUT") if message == SMTC_TIMEOUT
                       else message)
        self._render()

    def _copy_report(self) -> None:
        """Met le relevé dans le presse-papiers, pour le coller dans un ticket."""
        self.clipboard_clear()
        self.clipboard_append(self._report())
        self._copy_btn.configure(text=t("MUSIC_BTN_COPIED"))
        self.after(1500, lambda: self._copy_btn.configure(text=t("MUSIC_BTN_COPY")))

    def _report(self) -> str:
        lines = [f"SMTC available={available()} error={self._error or 'none'}"]
        for session in self._sessions:
            size = session.thumbnail_size
            lines.append(
                f"[{session.status}]{' *current' if session.is_current else ''} "
                f"{session.app_id}\n"
                f"    title={session.title!r} artist={session.artist!r} "
                f"album={session.album!r}\n"
                f"    cover={session.thumbnail_format or 'none'} "
                f"{size[0] if size else 0}x{size[1] if size else 0} "
                f"{len(session.thumbnail or b'')}B  "
                f"pos={session.position_ms}/{session.duration_ms}ms\n"
                f"    errors={session.errors}")
        return "\n".join(lines)

    # -- rendu ------------------------------------------------------------- #

    def _render(self, tout: bool = False) -> None:
        """Met la liste à jour, en ne refaisant QUE les cartes qui changent.

        Chaque évènement SMTC reconstruisait les huit cartes et leurs aperçus :
        ~200 ms de gel, et le défilement revenait en haut. Une carte dont la
        session et le lien sont identiques est gardée telle quelle. `tout`
        force la reconstruction, pour un changement de langue.
        """
        if tout or not available():
            for _signature, carte in self._cartes.values():
                carte.destroy()
            self._cartes = {}
        if self._bandeau is not None:
            self._bandeau.destroy()
            self._bandeau = None

        if not available():
            self._status_lbl.configure(text="")
            self._refresh_btn.configure(state="disabled")
            self._copy_btn.configure(state="disabled")
            self._banner(t("MUSIC_UNAVAILABLE"), COL_RED,
                         f"{self._error}\n\n{PIP_HINT}")
            return

        self._status_lbl.configure(text=t("MUSIC_COUNT", count=len(self._sessions)))
        rang = 0
        if self._error:
            self._banner(t("MUSIC_ERROR"), COL_RED, self._error)
            rang = 1

        gardees: dict[str, tuple[Any, SessionCard]] = {}
        for app, session in self._entries():
            url = self._overlay_url(app)
            signature = (session, url)
            ancienne = self._cartes.pop(app.key, None)
            if ancienne is not None and ancienne[0] == signature:
                carte = ancienne[1]
            else:
                if ancienne is not None:
                    ancienne[1].destroy()
                carte = SessionCard(self._list, app, session, overlay_url=url,
                                    store=self._hub.styles if self._hub else None,
                                    on_style_saved=self._style_saved)
            carte.grid(row=rang + 1, column=0, sticky="ew", padx=6, pady=6)
            gardees[app.key] = (signature, carte)
            rang += 1
        # Les sources hors catalogue qui ont cessé de jouer.
        for _signature, carte in self._cartes.values():
            carte.destroy()
        self._cartes = gardees

    def _entries(self) -> list[tuple[MusicApp, Optional[Session]]]:
        """Les lecteurs à afficher : ceux du catalogue, plus ceux détectés.

        Les lecteurs livrés par défaut restent listés même éteints. C'est le
        but : fermer Spotify ne doit pas faire disparaître son lien, sinon la
        source navigateur préparée dans OBS pointerait vers une URL absente de
        l'interface, et il faudrait la refaire à chaque fois.

        Une source hors catalogue, elle, n'apparaît que tant qu'elle joue :
        personne n'a préparé d'overlay pour un navigateur fermé il y a
        trois jours.
        """
        vivantes: dict[str, tuple[MusicApp, Session]] = {}
        for session in self._sessions:
            app = identify(session.app_id)
            precedente = vivantes.get(app.key)
            # Même arbitrage que le hub : la session qui joue l'emporte.
            if precedente is None or (session.is_playing
                                      and not precedente[1].is_playing):
                vivantes[app.key] = (app, session)

        entrees: list[tuple[MusicApp, Optional[Session]]] = []
        for app in BUILT_IN:
            trouvee = vivantes.pop(app.key, None)
            entrees.append((app, trouvee[1] if trouvee else None))
        for app, session in vivantes.values():
            entrees.append((app, session))

        # En lecture d'abord, puis les lecteurs présents, puis les éteints :
        # ce qui passe à l'antenne en ce moment se lit sans faire défiler.
        def ordre(entree: tuple[MusicApp, Optional[Session]]) -> tuple:
            app, session = entree
            if session is None:
                return (2, app.label.lower())
            return (0 if session.is_playing else 1, app.label.lower())

        return sorted(entrees, key=ordre)

    def _banner(self, title: str, color: str, detail: str) -> None:
        frame = ctk.CTkFrame(self._list, fg_color=COL_CARD, corner_radius=14,
                             border_width=1, border_color=COL_BORDER)
        frame.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        self._bandeau = frame
        frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(frame, text=title, font=font(14, "bold"), text_color=color,
                     anchor="w").grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
        ctk.CTkLabel(frame, text=detail, font=font(11), text_color=COL_TEXT_MUTED,
                     anchor="w", justify="left",
                     wraplength=720).grid(row=1, column=0, sticky="ew", padx=18,
                                          pady=(0, 16))

    def refresh_labels(self) -> None:
        self._title_lbl.configure(text=t("MUSIC_TITLE"))
        self._subtitle_lbl.configure(text=t("MUSIC_SUBTITLE"))
        self._logo_lbl.configure(text=t("MUSIC_LOGO_HINT"))
        self._refresh_btn.configure(text=t("MUSIC_BTN_REFRESH"))
        self._copy_btn.configure(text=t("MUSIC_BTN_COPY"))
        self._regen_btn.configure(text=t("MUSIC_BTN_REGENERATE"))
        self._render(tout=True)
