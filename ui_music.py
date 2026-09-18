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

from typing import Any, Callable, Optional

from i18n import t
from music_catalog import BUILT_IN, MusicApp, identify, logo_path
from music_overlay import MusicHub
from music_smtc import IMPORT_ERROR, MusicWatcher, Session, available
from music_style import Style, StyleStore, overlay_size, preview_png
from ui_music_style import StyleDialog
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER,
                       COL_BORDER_ACCENT, COL_CARD, COL_GREEN, COL_RED,
                       COL_TEXT, COL_TEXT_MUTED, COL_YELLOW, ctk, font)

try:
    from PIL import Image
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

#: Marge horizontale totale autour de la vignette, reprise par la colonne.
_COVER_PAD_X = 30


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


class SessionCard(ctk.CTkFrame):
    """Une session SMTC : pochette à gauche, métadonnées à droite."""

    def __init__(self, master, app: MusicApp, session: Optional[Session] = None,
                 overlay_url: str = "", store: Optional[StyleStore] = None,
                 on_style_saved: Optional[Callable[[str, Style], None]] = None,
                 **kwargs) -> None:
        actif = session is not None and session.is_current
        super().__init__(master, fg_color=COL_CARD, corner_radius=14,
                         border_width=1,
                         border_color=COL_BORDER_ACCENT if actif else COL_BORDER,
                         **kwargs)
        self._app = app
        self.grid_columnconfigure(1, weight=1)
        # Largeur de colonne fixe, même pour une vignette étroite : sinon le
        # texte démarre à une abscisse différente sur chaque carte, selon le
        # rapport de son image, et la liste paraît de travers.
        self.grid_columnconfigure(0, minsize=_COVER_MAX_W + _COVER_PAD_X)
        # La CTkImage doit survivre à la fin du constructeur : Tk ne garde
        # qu'un identifiant vers l'image, pas l'objet Python. Sans cet
        # attribut, le ramasse-miettes la libère et la vignette reste vide.
        self._cover: Optional[ctk.CTkImage] = None

        self._url = overlay_url
        self._store = store
        self._on_style_saved = on_style_saved
        self._style_img: Optional[ctk.CTkImage] = None
        if session is None:
            self._build_offline()
        else:
            self._build_cover(session)
            self._build_meta(session)
        if overlay_url:
            if store is not None:
                self._build_widget_row()
            self._build_link()

    def _build_widget_row(self) -> None:
        """Apercu du rendu à gauche, bouton Widget à droite.

        Placée au-dessus du lien : on règle l'apparence, puis on copie l'URL.
        L'aperçu montre CE lecteur précisément, donc on voit d'un coup d'œil
        ce qui a déjà été préparé pour chaque application.
        """
        rangee = ctk.CTkFrame(self, fg_color="transparent")
        rangee.grid(row=5, column=0, columnspan=2, sticky="ew",
                    padx=16, pady=(0, 8))
        rangee.grid_columnconfigure(1, weight=1)

        cadre = ctk.CTkFrame(rangee, fg_color=COL_BG, corner_radius=8,
                             border_width=1, border_color=COL_BORDER,
                             width=_STYLE_PREVIEW[0] + 4,
                             height=_STYLE_PREVIEW[1] + 4)
        cadre.grid(row=0, column=0, sticky="w")
        cadre.grid_propagate(False)
        self._style_lbl = ctk.CTkLabel(cadre, text="")
        self._style_lbl.place(relx=0.5, rely=0.5, anchor="center")
        self._refresh_style_preview()

        ctk.CTkButton(rangee, text=t("MUSIC_BTN_WIDGET"), width=150, height=34,
                      corner_radius=8, fg_color=COL_ACCENT,
                      hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                      font=font(12, "bold"),
                      command=self._open_style).grid(row=0, column=2, sticky="e")

    def _refresh_style_preview(self) -> None:
        if self._store is None or Image is None:
            return
        style = self._store.get(self._app.key)
        # Rendu au double de la taille affichee : CTkImage reduit ensuite selon
        # la mise a l echelle de Windows, ce qui reste net. Lui donner la taille
        # exacte l obligeait a agrandir, d ou l aspect pixelise.
        png = preview_png(style, width=_STYLE_PREVIEW[0],
                          height=_STYLE_PREVIEW[1], echelle=2,
                          background=self._store.background_path(self._app.key))
        try:
            with Image.open(io.BytesIO(png)) as brut:
                # RGBA : les coins arrondis laissent voir la carte derriere.
                image = brut.convert("RGBA")
            self._style_img = ctk.CTkImage(light_image=image, dark_image=image,
                                           size=_STYLE_PREVIEW)
            self._style_lbl.configure(image=self._style_img)
        except Exception:
            pass

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

    def _build_offline(self) -> None:
        """Carte d'un lecteur connu mais qui ne joue rien.

        Elle existe pour que le lien reste visible et copiable avant même
        d'avoir lancé le lecteur : on prépare la source dans OBS une fois, et
        elle s'allume toute seule le jour où la musique part.
        """
        self._build_badge(self._app, COL_BG)
        ctk.CTkLabel(self, text=self._app.label, font=font(15, "bold"),
                     text_color=COL_TEXT_MUTED, anchor="w").grid(
                         row=1, column=1, sticky="ew", padx=(0, 16), pady=(18, 0))
        ctk.CTkLabel(self, text=t("MUSIC_OFFLINE"), font=font(11),
                     text_color=COL_TEXT_MUTED, anchor="w").grid(
                         row=2, column=1, sticky="ew", padx=(0, 16), pady=(2, 18))

    def _build_badge(self, app: MusicApp, fond: str) -> None:
        """Pastille de gauche : le logo du lecteur, sinon le monogramme.

        Les huit lecteurs livrés ont leur logo dans `assets/music/<clé>.png`.
        Le monogramme sert aux sources hors catalogue, qui n'en ont aucun.
        """
        holder = ctk.CTkFrame(self, fg_color=fond, corner_radius=12,
                              width=_COVER_H, height=_COVER_H)
        holder.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(16, 14), pady=16)
        holder.grid_propagate(False)

        chemin = logo_path(app.key)
        image = None
        if chemin is not None and Image is not None:
            try:
                with Image.open(chemin) as brut:
                    image = brut.convert("RGBA").resize((_COVER_H - 18, _COVER_H - 18))
            except Exception:
                image = None
        if image is not None:
            self._cover = ctk.CTkImage(light_image=image, dark_image=image,
                                       size=(_COVER_H - 18, _COVER_H - 18))
            ctk.CTkLabel(holder, text="", image=self._cover).place(
                relx=0.5, rely=0.5, anchor="center")
            return
        ctk.CTkLabel(holder, text=app.monogram, font=font(24, "bold"),
                     text_color=app.color).place(relx=0.5, rely=0.5, anchor="center")

    def _build_cover(self, session: Session) -> None:
        width, height = fitted_cover_size(session)
        holder = ctk.CTkFrame(self, fg_color=COL_BG, corner_radius=10,
                              width=width, height=height)
        holder.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(16, 14), pady=16)
        holder.grid_propagate(False)

        image = self._decode(session, (width, height))
        if image is None:
            ctk.CTkLabel(holder, text="♪", font=font(26),
                         text_color=COL_TEXT_MUTED).place(relx=0.5, rely=0.5,
                                                          anchor="center")
            return
        self._cover = ctk.CTkImage(light_image=image, dark_image=image,
                                   size=(width, height))
        ctk.CTkLabel(holder, text="", image=self._cover).place(relx=0.5, rely=0.5,
                                                               anchor="center")

    @staticmethod
    def _decode(session: Session, size: tuple[int, int]):
        if session.thumbnail is None or Image is None:
            return None
        try:
            with Image.open(io.BytesIO(session.thumbnail)) as raw:
                return raw.convert("RGB").resize(size)
        except Exception:
            return None

    def _build_meta(self, session: Session) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=(16, 0))
        ctk.CTkLabel(head, text="●", font=font(12),
                     text_color=_STATUS_COLORS.get(session.status, COL_TEXT_MUTED),
                     width=14).pack(side="left")
        ctk.CTkLabel(head, text=t(f"MUSIC_STATUS_{session.status}"), font=font(11, "bold"),
                     text_color=COL_TEXT_MUTED).pack(side="left", padx=(2, 10))
        ctk.CTkLabel(head, text=self._app.label, font=font(11, "bold"),
                     text_color=self._app.color).pack(side="left", padx=(0, 10))
        if session.is_current:
            ctk.CTkLabel(head, text=t("MUSIC_BADGE_CURRENT"), font=font(10, "bold"),
                         text_color=COL_ACCENT).pack(side="left")

        ctk.CTkLabel(self, text=session.title or t("MUSIC_NO_TITLE"),
                     font=font(15, "bold"), text_color=COL_TEXT, anchor="w",
                     justify="left", wraplength=560).grid(row=1, column=1, sticky="ew",
                                                          padx=(0, 16), pady=(4, 0))
        subtitle = session.artist or t("MUSIC_NO_ARTIST")
        if session.album:
            subtitle = f"{subtitle} — {session.album}"
        ctk.CTkLabel(self, text=subtitle, font=font(12), text_color=COL_TEXT_MUTED,
                     anchor="w", justify="left",
                     wraplength=560).grid(row=2, column=1, sticky="ew",
                                          padx=(0, 16), pady=(2, 0))

        ctk.CTkLabel(self, text=self._facts(session), font=font(10),
                     text_color=COL_TEXT_MUTED, anchor="w", justify="left",
                     wraplength=560).grid(row=3, column=1, sticky="ew",
                                          padx=(0, 16), pady=(8, 16))

        if session.errors:
            ctk.CTkLabel(self, text=" · ".join(session.errors), font=font(10),
                         text_color=COL_RED, anchor="w", justify="left",
                         wraplength=700).grid(row=4, column=0, columnspan=2, sticky="ew",
                                              padx=16, pady=(0, 14))

    def _build_link(self) -> None:
        """Le lien de l'overlay, en bas de la carte.

        Une URL par source plutôt qu'une seule pour « la musique » : deux
        lecteurs ouverts donnent deux liens indépendants, et c'est
        l'utilisateur qui choisit lequel mettre à l'écran.
        """
        rangee = ctk.CTkFrame(self, fg_color="transparent")
        rangee.grid(row=6, column=0, columnspan=2, sticky="ew",
                    padx=16, pady=(0, 16))
        rangee.grid_columnconfigure(0, weight=1)

        champ = ctk.CTkEntry(rangee, height=32, font=font(11),
                             fg_color=COL_BG, border_color=COL_BORDER,
                             text_color=COL_TEXT_MUTED)
        champ.insert(0, self._url)
        # Lecture seule, mais sélectionnable : l'utilisateur doit pouvoir
        # copier à la main si le presse-papiers lui échappe.
        champ.configure(state="readonly")
        champ.grid(row=0, column=0, sticky="ew")

        self._link_btn = ctk.CTkButton(rangee, text=t("MUSIC_BTN_COPY_LINK"),
                                       width=160, height=32, corner_radius=8,
                                       fg_color="transparent", border_width=1,
                                       border_color=COL_BORDER_ACCENT,
                                       hover_color=COL_CARD, text_color=COL_ACCENT,
                                       font=font(11, "bold"), command=self._copy_link)
        self._link_btn.grid(row=0, column=1, padx=(10, 0))

        ctk.CTkLabel(rangee, text=t("MUSIC_OVERLAY_HINT"), font=font(10),
                     text_color=COL_TEXT_MUTED, anchor="w").grid(
                         row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        # La taille a saisir dans OBS, a cote du lien qu'on y colle.
        self._size_lbl = ctk.CTkLabel(rangee, text="", font=font(10, "bold"),
                                      text_color=COL_ACCENT, anchor="w")
        self._size_lbl.grid(row=2, column=0, columnspan=2, sticky="ew",
                            pady=(2, 0))
        self._refresh_size()

    def _refresh_size(self) -> None:
        if self._store is None:
            return
        largeur, hauteur = overlay_size(self._store.get(self._app.key))
        self._size_lbl.configure(text=t("MUSIC_SOURCE_SIZE", w=largeur,
                                        h=hauteur))

    def _copy_link(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self._url)
        self._link_btn.configure(text=t("MUSIC_BTN_LINK_COPIED"))
        self.after(1500,
                   lambda: self._link_btn.configure(text=t("MUSIC_BTN_COPY_LINK")))

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

    def _apply_error(self, message: str) -> None:
        self._error = message
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

    def _render(self) -> None:
        for child in self._list.winfo_children():
            child.destroy()

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

        for app, session in self._entries():
            carte = SessionCard(self._list, app, session,
                                overlay_url=self._overlay_url(app),
                                store=self._hub.styles if self._hub else None,
                                on_style_saved=self._style_saved)
            carte.grid(row=rang + 1, column=0, sticky="ew", padx=6, pady=6)
            rang += 1

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
        self._render()
