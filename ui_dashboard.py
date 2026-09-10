"""Vue principale : grille de cartes de jeu et barre d'actions."""
from __future__ import annotations

import threading
import time
from tkinter import messagebox
from typing import Any, Callable, Optional

from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from app_paths import logger
from cover_service import GameCoverService
from games import Game, GameStore, SteamScanner
from i18n import t
from obs_client import AsyncLoopThread, OBSClient
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_ACCENT_SOFT,
                       COL_BADGE_FG, COL_BG, COL_BORDER, COL_CARD,
                       COL_CARD_HOVER, COL_GREEN, COL_RED, COL_TEXT,
                       COL_TEXT_MUTED, STATE_DOT, STATE_RING, badge_text, ctk,
                       font, is_running)
from ui_game_dialogs import GameModal


# ============================================================================
# RENDU PIL : coins arrondis de la jaquette + pastille d'état incrustée
# ============================================================================
# Pourquoi PIL plutôt que des widgets CustomTkinter : CTk peint le reste du
# canvas d'un coin arrondi avec la couleur du PARENT, pas celle de l'image
# posée dessous. Une pastille CTkFrame arrondie sur la jaquette montrait donc
# quatre encoches sombres aux angles, et l'artwork restait un rectangle net
# dans une carte arrondie. En composant les deux DANS l'image on récupère
# l'anticrénelage et l'alpha réels de PIL — et deux widgets Tk de moins par
# carte, ce qui compte sur une grille de quarante jeux.
_BADGE_H = 22          # hauteur de la pastille, en px logiques
_BADGE_MARGIN = 9      # retrait par rapport aux bords de la jaquette
_BADGE_DOT = 7
_SUPERSAMPLE = 4       # masque des coins tracé 4x puis réduit = bords lisses


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


@lru_cache(maxsize=8)
def _badge_font(px: int) -> Any:
    """Police de la pastille. Les familles sont essayées dans l'ordre ;
    `load_default()` ne ferme la liste que pour ne jamais faire échouer un
    rendu à cause d'une police absente."""
    for name in ("seguisb.ttf", "segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def _rounded_mask(size: tuple[int, int], radius: float) -> Image.Image:
    """Masque d'un rectangle à coins arrondis, anticrénelé.

    Tracé en `_SUPERSAMPLE`x puis réduit : `rounded_rectangle` ne lisse pas
    ses bords, et un arrondi crénelé sur une carte se voit tout de suite.
    """
    big = (size[0] * _SUPERSAMPLE, size[1] * _SUPERSAMPLE)
    mask = Image.new("L", big, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big[0] - 1, big[1] - 1),
                                           radius=radius * _SUPERSAMPLE, fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def _with_badge(base: Image.Image, state: str, scale: float,
                margin: float = _BADGE_MARGIN) -> Image.Image:
    """Incruste la pastille « Actif / Inactif » en haut à droite.

    Fond sombre translucide + liseré clair à 15 % : la pastille reste lisible
    sur une jaquette claire comme sombre sans masquer l'artwork, là où un
    rectangle plein rouge ou vert faisait tache.
    """
    def px(value: float) -> int:
        return max(1, round(value * scale))

    try:
        label = badge_text(state)
        fnt = _badge_font(max(8, round(11 * scale)))
        margin, height, dot = px(margin), px(_BADGE_H), px(_BADGE_DOT)
        side = px(9)
        width = side + dot + px(6) + round(fnt.getlength(label)) + side
        x1, y0 = base.width - margin, margin
        x0, y1 = x1 - width, y0 + height

        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.rounded_rectangle((x0, y0, x1, y1), radius=height / 2,
                               fill=(13, 10, 24, 214), outline=(255, 255, 255, 38),
                               width=px(1))
        middle = (y0 + y1) / 2
        dot_x = x0 + side
        draw.ellipse((dot_x, middle - dot / 2, dot_x + dot, middle + dot / 2),
                     fill=_rgb(STATE_DOT[is_running(state)]))
        draw.text((dot_x + dot + px(6), middle), label, font=fnt,
                  fill=_rgb(COL_BADGE_FG), anchor="lm")
        return Image.alpha_composite(base.convert("RGBA"), layer).convert("RGB")
    except Exception:
        # Police exotique, `anchor` non supporté par une police bitmap... :
        # mieux vaut une jaquette sans pastille qu'une carte vide.
        logger.debug("Incrustation de la pastille d'état échouée.", exc_info=True)
        return base


# ============================================================================
# COMPOSANT : CARTE JEU — poster 2:3, badge unique, overlay hover
# ============================================================================
class GameCard(ctk.CTkFrame):
    """Carte façon grille Steam : jaquette verticale 2:3, UN SEUL badge de
    statut (règle stricte anti-doublon), overlay sombre révélé au survol avec
    titre complet + source + actions Éditer/Supprimer."""

    CARD_WIDTH = 190
    CARD_HEIGHT = 285  # ratio 2:3
    # Liseré autour de la jaquette : c'est LUI qui dit l'état de la carte.
    # 4 px : assez pour se lire d'un coup d'œil sur une grille entière, et
    # assez épais pour que l'arrondi extérieur et celui de la jaquette restent
    # concentriques (rayon intérieur = rayon extérieur - épaisseur).
    BORDER_W = 4
    CARD_RADIUS = 14
    COVER_RADIUS = CARD_RADIUS - BORDER_W
    INNER_WIDTH = CARD_WIDTH - 2 * BORDER_W
    INNER_HEIGHT = CARD_HEIGHT - 2 * BORDER_W
    # L'overlay de survol occupe EXACTEMENT la place de la jaquette : même
    # retrait, même arrondi. Le coin de son rectangle mord bien dans l'arc du
    # liseré, mais `bg_color` (la couleur peinte hors des coins arrondis) est
    # réglée sur la couleur du liseré : l'arc reste continu.
    OVERLAY_INSET = BORDER_W
    OVERLAY_RADIUS = COVER_RADIUS
    # Surveillance du curseur pendant que l'overlay est ouvert. Tk n'envoie PAS
    # de <Leave> au cadre de l'overlay quand le curseur passe d'un de ses
    # boutons directement à l'extérieur de la carte : l'overlay restait affiché
    # jusqu'à ce qu'on repasse dessus. Un test de position périodique rattrape
    # ces cas, quels que soient les événements reçus.
    HOVER_POLL_MS = 50

    # Les cartes sans jaquette partagent leur image : le placeholder ne dépend
    # que de l'état, de la langue et du facteur d'échelle, pas du jeu. Sans ce
    # cache, afficher quarante jeux sans jaquette fabriquait quarante images.
    _placeholder_cache: dict[tuple[Any, ...], ctk.CTkImage] = {}

    def __init__(self, master, game: Game, state: str,
                 on_edit: Callable[[Game], None], on_delete: Callable[[Game], None],
                 cover_service: Optional[GameCoverService] = None,
                 bind_wheel: Optional[Callable[[Any], None]] = None, **kwargs) -> None:
        # Ni bordure ni coins arrondis CTk : liseré d'état, arrondis et jaquette
        # sont peints ensemble dans UNE image (voir _card_image). Le cadre ne
        # sert plus qu'à réserver la place ; les angles arrondis de l'image
        # laissent voir le fond de la grille.
        super().__init__(master, fg_color="transparent", corner_radius=0,
                          border_width=0,
                          width=self.CARD_WIDTH, height=self.CARD_HEIGHT, **kwargs)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.game_id = game.id
        self.game = game          # comparé par valeur pour décider d'un rebuild
        self._game = game
        self._current_state = state
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._cover_service = cover_service
        self._bind_wheel = bind_wheel
        self._ctk_image: Optional[ctk.CTkImage] = None
        self._base_cover = None   # jaquette prête (PIL), sans pastille
        self._overlay: Optional[ctk.CTkFrame] = None
        self._overlay_visible = False
        self._hover_job: Optional[str] = None

        # --- Zone "jaquette" ------------------------------------------------
        # Les enfants sont posés directement sur la carte : le cadre
        # intermédiaire d'autrefois n'apportait rien visuellement et ajoutait
        # deux fenêtres Tk par carte, toutes déplacées à chaque cran de
        # défilement. Moins de fenêtres = moins de repeints partiels visibles.
        self._poster = self

        # Le label couvre TOUTE la carte, liseré compris. C'est ce point qui
        # règle les angles : un label rentré de BORDER_W reste un rectangle, et
        # le coin de ce rectangle mord dans l'arc du liseré — il en peignait un
        # bout avec sa propre couleur, d'où l'angle amputé.
        # corner_radius=0 IMPÉRATIF : CTkLabel pose un
        # `padx=min(corner_radius, hauteur/2)` autour de son contenu, ce qui
        # encadrait la jaquette de deux bandes mortes et l'amputait d'autant.
        self._cover_lbl = ctk.CTkLabel(self._poster, text="", fg_color="transparent",
                                        corner_radius=0, width=self.CARD_WIDTH,
                                        height=self.CARD_HEIGHT)
        self._cover_lbl.place(x=0, y=0)

        icon_lbl = self._cover_lbl  # conservé pour les bindings de survol

        # Sans jaquette, le titre est le seul contenu de la carte : il occupe
        # donc le centre. L'emoji placeholder qui trônait ici jurait avec le
        # reste de l'interface et ne disait rien de plus que le titre.
        # fg_color explicite : « transparent » prendrait la couleur du CADRE,
        # désormais celle de la grille, et poserait une bande étrangère sur la
        # carte. Ce titre ne s'affiche que faute de jaquette, donc sur COL_CARD.
        self._title_static = ctk.CTkLabel(self._poster, text=game.name, font=font(13, "bold"),
                                           text_color=COL_TEXT, wraplength=self.CARD_WIDTH - 24,
                                           justify="center", fg_color=COL_CARD)
        self._title_static.place(relx=0.5, rely=0.5, anchor="center")
        title_static = self._title_static

        # Pastille d'état : incrustée dans l'image, pas un widget (voir
        # _with_badge). L'appel ci-dessous affiche le placeholder avec sa
        # pastille tant que la jaquette n'est pas arrivée.
        self._apply_image()

        # L'overlay de survol n'est PAS construit ici : voir _build_overlay().
        # dict.fromkeys : _poster vaut self depuis la suppression du cadre
        # intermédiaire, inutile de lier deux fois le même widget.
        for widget in dict.fromkeys((self, self._poster, icon_lbl, title_static)):
            widget.bind("<Enter>", self._show_overlay)
        self._poster.bind("<Leave>", self._on_poster_leave)

        # Demandé en dernier : la carte est entièrement construite, donc le
        # callback (remis sur le thread UI par le service) trouvera des
        # widgets valides quelle que soit la vitesse du cache.
        self._request_cover()

    # -- Jaquette ---------------------------------------------------------- #

    def _request_cover(self, force: bool = False) -> None:
        if self._cover_service is None:
            return
        self._cover_service.request_cover(self._game, self._on_cover_received, force=force)

    def _on_cover_received(self, pil_image: Optional[Image.Image]) -> None:
        """Appelé sur le thread UI (garanti par le contrat de dispatch du
        service). La carte peut avoir été détruite entre-temps par un rebuild
        de la grille : winfo_exists() évite le TclError."""
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        if pil_image is None:
            return  # on garde le placeholder (titre + pastille)
        try:
            # CTkImage redimensionne en interne avec le rééchantillonnage par
            # défaut de Pillow (bicubique), qui adoucit nettement en réduction :
            # mesuré à 25 % de netteté perdue sur une jaquette 300x450 ramenée
            # à la taille de la carte. On la réduit donc nous-mêmes en LANCZOS,
            # à la taille exacte que CTkImage demandera — son propre resize
            # devient alors sans effet.
            scaling = ctk.ScalingTracker.get_widget_scaling(self)
            border = max(1, round(self.BORDER_W * scaling))
            target = (max(1, round(self.CARD_WIDTH * scaling)) - 2 * border,
                      max(1, round(self.CARD_HEIGHT * scaling)) - 2 * border)
            self._base_cover = pil_image.resize(target, Image.Resampling.LANCZOS)
            self._apply_image()
            # La jaquette porte déjà le titre du jeu : afficher le nôtre
            # par-dessus ferait doublon illisible.
            self._title_static.place_forget()
            # Poser la jaquette repasse le label AU-DESSUS de l'overlay et
            # peut avaler le <Leave> : sans ça, « Modifier / Supprimer »
            # restait affiché après l'actualisation, et il fallait repasser la
            # souris sur la carte pour s'en débarrasser.
            self._settle_overlay()
        except Exception:
            logger.debug("Application de la jaquette échouée pour %s.", self._game.name,
                         exc_info=True)

    # -- Image affichée (jaquette ou placeholder) + pastille d'état --------- #

    @classmethod
    def _card_image(cls, cover, state: str, scaling: float,
                    backdrop: str) -> Image.Image:
        """Peint la carte entière : fond de grille, liseré d'état arrondi,
        jaquette arrondie par-dessus, puis la pastille.

        Les deux arrondis sont concentriques et anticrénelés par le même
        masque, donc les angles se referment proprement — ce que la bordure
        d'un CTkFrame, recouverte aux coins par le rectangle du label, ne
        savait pas faire.
        """
        width = max(1, round(cls.CARD_WIDTH * scaling))
        height = max(1, round(cls.CARD_HEIGHT * scaling))
        border = max(1, round(cls.BORDER_W * scaling))

        card = Image.new("RGB", (width, height), _rgb(backdrop))
        ring = Image.new("RGB", (width, height), _rgb(STATE_RING[is_running(state)]))
        card.paste(ring, (0, 0), _rounded_mask((width, height), cls.CARD_RADIUS * scaling))

        inner_size = (width - 2 * border, height - 2 * border)
        inner = Image.new("RGB", inner_size, _rgb(COL_CARD)) if cover is None else cover
        if inner.size != inner_size:
            inner = inner.resize(inner_size, Image.Resampling.LANCZOS)
        card.paste(inner, (border, border),
                   _rounded_mask(inner_size, cls.COVER_RADIUS * scaling))
        return _with_badge(card, state, scaling, margin=_BADGE_MARGIN + cls.BORDER_W)

    @classmethod
    def _placeholder_image(cls, state: str, scaling: float,
                           backdrop: str) -> ctk.CTkImage:
        """Image des cartes sans jaquette : liseré + fond de carte + pastille.

        Mutualisée entre toutes les cartes du même état (clé = état + libellé
        traduit + échelle + fond), donc une carte de plus ne coûte rien.
        """
        key = (is_running(state), badge_text(state), round(scaling, 2), backdrop)
        image = cls._placeholder_cache.get(key)
        if image is None:
            shown = cls._card_image(None, state, scaling, backdrop)
            image = ctk.CTkImage(light_image=shown, dark_image=shown,
                                 size=(cls.CARD_WIDTH, cls.CARD_HEIGHT))
            cls._placeholder_cache[key] = image
        return image

    def _backdrop(self) -> str:
        """Couleur que laissent voir les angles arrondis : celle de la grille.

        `_detect_color_of_master()` remonte la chaîne des parents transparents ;
        s'il échoue, le fond général de l'application reste le bon repli.
        """
        try:
            color = self._detect_color_of_master()
            if isinstance(color, (list, tuple)):
                color = color[0 if ctk.get_appearance_mode() == "Light" else 1]
            if isinstance(color, str) and color.startswith("#"):
                return color
        except Exception:
            logger.debug("Couleur de fond de la grille indétectable.", exc_info=True)
        return COL_BG

    def _apply_image(self) -> None:
        """(Re)compose l'image de la carte. Appelée à la construction, à
        l'arrivée de la jaquette, à chaque changement d'état et de langue."""
        try:
            scaling = ctk.ScalingTracker.get_widget_scaling(self)
            backdrop = self._backdrop()
            if self._base_cover is None:
                self._ctk_image = GameCard._placeholder_image(self._current_state,
                                                              scaling, backdrop)
            else:
                shown = self._card_image(self._base_cover, self._current_state,
                                         scaling, backdrop)
                self._ctk_image = ctk.CTkImage(light_image=shown, dark_image=shown,
                                               size=(self.CARD_WIDTH, self.CARD_HEIGHT))
            self._cover_lbl.configure(image=self._ctk_image)
        except Exception:
            logger.debug("Composition de l'image de carte échouée pour %s.",
                         self._game.name, exc_info=True)

    def _build_overlay(self) -> None:
        """Construit l'overlay de survol à la PREMIÈRE entrée souris.

        Le bâtir d'avance sur chaque carte coûtait la moitié du temps de
        render_games() — 2 boutons + 2 labels + 2 frames par carte, que CTk
        redessine coin arrondi par coin arrondi — pour des widgets qui ne sont
        visibles qu'au survol d'UNE carte à la fois.

        Opacité simulée par une couleur sombre unie : CTk ne gère pas l'alpha
        réel, et le contraste reste net sans dépendance supplémentaire.
        """
        game = self._game
        source_txt = t("GAME_SOURCE_STEAM") if game.source == "steam" else t("GAME_SOURCE_MANUAL")

        self._overlay = ctk.CTkFrame(self, fg_color="#08060F",
                                     bg_color=STATE_RING[is_running(self._current_state)],
                                     corner_radius=self.OVERLAY_RADIUS,
                                     width=self.CARD_WIDTH - 2 * self.OVERLAY_INSET,
                                     height=self.CARD_HEIGHT - 2 * self.OVERLAY_INSET)
        self._overlay_title = ctk.CTkLabel(self._overlay, text=game.name, font=font(13, "bold"),
                                            text_color=COL_TEXT, wraplength=self.CARD_WIDTH - 24,
                                            justify="center")
        self._overlay_title.place(relx=0.5, rely=0.30, anchor="center")
        self._overlay_source = ctk.CTkLabel(self._overlay, text=source_txt, font=font(10),
                                             text_color=COL_TEXT_MUTED)
        self._overlay_source.place(relx=0.5, rely=0.42, anchor="center")

        btn_row = ctk.CTkFrame(self._overlay, fg_color="transparent")
        btn_row.place(relx=0.5, rely=0.68, anchor="center")
        self._edit_btn = ctk.CTkButton(btn_row, text=t("GAME_CARD_BTN_EDIT"), width=76, height=28,
                                        fg_color=COL_ACCENT_SOFT, hover_color=COL_ACCENT_HOVER,
                                        font=font(11), corner_radius=8,
                                        command=lambda: self._on_edit(self._game))
        self._edit_btn.pack(side="left", padx=3)
        self._delete_btn = ctk.CTkButton(btn_row, text=t("GAME_CARD_BTN_DELETE"), width=90, height=28,
                                          fg_color="#3A1420", hover_color=COL_RED,
                                          font=font(11), corner_radius=8,
                                          command=lambda: self._on_delete(self._game))
        self._delete_btn.pack(side="left", padx=3)

        # <Leave> sur le cadre ET sur toute sa descendance : sans les enfants,
        # sortir de la carte depuis un bouton ne fermait rien. Et on ne ferme
        # pas à l'aveugle — passer du cadre à son propre bouton émet aussi un
        # <Leave> : c'est la position réelle du curseur qui décide.
        for widget in (self._overlay, self._overlay_title, self._overlay_source,
                       btn_row, self._edit_btn, self._delete_btn):
            widget.bind("<Leave>", self._on_poster_leave)
        # Créés après le binding récursif de la grille : sans ça l'overlay
        # avalerait la molette et bloquerait le défilement sous le curseur.
        if self._bind_wheel is not None:
            self._bind_wheel(self._overlay)

    _hover_blocked_until = 0.0

    @classmethod
    def suppress_hover(cls, seconds: float) -> None:
        """Neutralise l'overlay de survol pendant un défilement : les cartes
        glissent sous un curseur immobile, ce qui déclenche une rafale de
        <Enter>/<Leave> et fait clignoter les overlays au milieu du scroll."""
        cls._hover_blocked_until = time.monotonic() + seconds

    def _show_overlay(self, _event: Any = None) -> None:
        if self._overlay_visible or time.monotonic() < GameCard._hover_blocked_until:
            return
        if self._overlay is None:
            self._build_overlay()
        self._overlay_visible = True
        # Le liseré peut avoir changé de couleur depuis la dernière ouverture.
        self._overlay.configure(bg_color=STATE_RING[is_running(self._current_state)])
        self._overlay.place(x=self.OVERLAY_INSET, y=self.OVERLAY_INSET)
        self._overlay.lift()
        self._watch_pointer()

    def _settle_overlay(self) -> None:
        """Remet l'overlay dans l'état que dicte la position réelle du curseur."""
        if not self._overlay_visible:
            return
        try:
            pointer = self.winfo_pointerxy()
        except Exception:
            self._hide_overlay()
            return
        self._maybe_hide(pointer)
        if self._overlay_visible and self._overlay is not None:
            self._overlay.lift()   # la jaquette vient de passer devant

    def _on_poster_leave(self, _event: Any = None) -> None:
        # Fermeture INSTANTANÉE : plus de délai de 60 ms. Il servait à tolérer
        # le passage du poster à l'overlay, mais _maybe_hide teste déjà ce que
        # le curseur survole vraiment — un widget de la carte ne ferme rien.
        try:
            self._maybe_hide(self.winfo_pointerxy())
        except Exception:
            self._hide_overlay()

    def _watch_pointer(self) -> None:
        """Vérifie la position du curseur tant que l'overlay est ouvert.

        Filet de sécurité : certains trajets ne produisent aucun <Leave>
        exploitable — bouton vers l'extérieur, fenêtre qui perd le focus,
        carte qui glisse sous un curseur immobile.
        """
        self._hover_job = None
        if not self._overlay_visible:
            return
        try:
            if not self.winfo_exists():
                return
            self._maybe_hide(self.winfo_pointerxy())
            if self._overlay_visible:
                self._hover_job = self.after(self.HOVER_POLL_MS, self._watch_pointer)
        except Exception:
            logger.debug("Surveillance du curseur interrompue.", exc_info=True)

    def _maybe_hide(self, pointer_xy: tuple[int, int]) -> None:
        x, y = pointer_xy
        try:
            widget_under = self.winfo_containing(x, y)
        except Exception:
            widget_under = None
        if widget_under is None or not self._is_descendant(widget_under):
            self._hide_overlay()

    def _is_descendant(self, widget: Any) -> bool:
        current = widget
        while current is not None:
            if current == self:
                return True
            current = getattr(current, "master", None)
        return False

    def _hide_overlay(self, _event: Any = None) -> None:
        if not self._overlay_visible:
            return
        self._overlay_visible = False
        self._cancel_pointer_watch()
        self._overlay.place_forget()

    def _cancel_pointer_watch(self) -> None:
        if self._hover_job is None:
            return
        try:
            self.after_cancel(self._hover_job)
        except Exception:
            pass
        self._hover_job = None

    def destroy(self) -> None:
        # Une carte détruite pendant un survol laissait un `after` en attente
        # sur un widget disparu.
        self._cancel_pointer_watch()
        super().destroy()

    def refresh_labels(self) -> None:
        """Recharge les libellés dynamiques (source, boutons, badge) après un
        changement de langue à chaud — sans recréer les widgets."""
        self._apply_image()          # la pastille porte un libellé traduit
        if self._overlay is None:
            return  # jamais survolée : il sera bâti avec les bons libellés
        source_txt = t("GAME_SOURCE_STEAM") if self._game.source == "steam" else t("GAME_SOURCE_MANUAL")
        self._overlay_source.configure(text=source_txt)
        self._edit_btn.configure(text=t("GAME_CARD_BTN_EDIT"))
        self._delete_btn.configure(text=t("GAME_CARD_BTN_DELETE"))

    def set_state(self, state: str) -> None:
        """Met à jour uniquement le badge d'état, sans recréer le widget
        (appelé à chaque cycle de scan — doit rester O(1) et sans flicker)."""
        if state == self._current_state:
            return
        was_running = is_running(self._current_state)
        self._current_state = state
        # Deux signaux, un changement de couleur chacun : le liseré de la carte
        # et le point de la pastille. Les états fins (menu / en jeu) ne changent
        # rien visuellement, donc rien à recomposer pour eux.
        if is_running(state) == was_running and self._ctk_image is not None:
            return
        self._apply_image()
        if self._overlay is not None:
            self._overlay.configure(bg_color=STATE_RING[is_running(state)])


# ============================================================================
# VUE : DASHBOARD
# ============================================================================
# ============================================================================
# VUE : DASHBOARD (Bibliothèque — grille poster style Steam)
# ============================================================================
class DashboardView(ctk.CTkFrame):
    GRID_COLUMNS = 5          # repli si la largeur réelle n'est pas encore connue
    MIN_COLUMNS = 2
    MAX_COLUMNS = 8

    def __init__(self, master, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 steam_scanner: SteamScanner, post_ui: Callable[[Callable[[], None]], None],
                 cover_service: Optional[GameCoverService] = None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._store = store
        self._obs_loop = obs_loop
        self._get_obs_client = obs_client_getter
        self._steam_scanner = steam_scanner
        self._post_ui = post_ui
        self._cover_service = cover_service
        self._columns = self.GRID_COLUMNS
        self._resize_job: Optional[str] = None
        self._latest_states: dict[str, str] = {}
        self._steam_candidates: list[dict[str, str]] = []
        self._cards: dict[str, GameCard] = {}
        self._rendered_ids: tuple[str, ...] = ()
        self._empty_lbl: Optional[ctk.CTkLabel] = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        header.grid_columnconfigure(0, weight=1)

        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.grid(row=0, column=0, sticky="w")
        self._title_lbl = ctk.CTkLabel(title_col, text=t("DASHBOARD_TITLE"), font=font(22, "bold"),
                                        text_color=COL_TEXT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(title_col, text="", font=font(11), text_color=COL_TEXT_MUTED)
        self._subtitle_lbl.pack(anchor="w", pady=(2, 0))

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        self._add_btn = ctk.CTkButton(btns, text=t("DASHBOARD_BTN_ADD"), width=110, height=36,
                                       fg_color=COL_CARD, hover_color=COL_CARD_HOVER,
                                       border_width=1, border_color=COL_BORDER, corner_radius=9,
                                       font=font(12), command=self._open_add_modal)
        self._add_btn.pack(side="left", padx=(0, 8))
        self.scan_btn = ctk.CTkButton(btns, text=t("DASHBOARD_BTN_SCAN_STEAM"), width=160, height=36,
                                       fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                       text_color="#0F0C1B", font=font(12, "bold"), corner_radius=9,
                                       command=self.scan_steam_library)
        self.scan_btn.pack(side="left")

        self.status_lbl = ctk.CTkLabel(self, text="", font=font(11),
                                        text_color=COL_TEXT_MUTED, anchor="w")
        self.status_lbl.grid(row=1, column=0, sticky="w", padx=30)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=22, pady=10)
        for c in range(self.MAX_COLUMNS):
            self.scroll.grid_columnconfigure(c, weight=1)
        self._enable_smooth_scroll(self.scroll)
        # add="+" IMPÉRATIF : CTkScrollableFrame installe son propre
        # <Configure> sur ce frame pour recalculer la scrollregion du canvas.
        # Un bind() nu l'écrasait, la scrollregion restait figée sur la grille
        # vide — d'où un ascenseur géant, immobile, et un défilement qui
        # s'arrêtait avant la fin des cartes.
        self.scroll.bind("<Configure>", self._on_scroll_resize, add="+")

        self.render_games()

    # -- Grille responsive -------------------------------------------------- #

    def _columns_for_width(self, width: int) -> int:
        """Nombre de colonnes tenant dans `width`, carte + gouttière comprises."""
        slot = GameCard.CARD_WIDTH + 20  # 2 x padx=10
        return max(self.MIN_COLUMNS, min(self.MAX_COLUMNS, max(1, width // slot)))

    def _on_scroll_resize(self, event: Any) -> None:
        """<Configure> part en rafale pendant un redimensionnement : on
        débounce pour ne reconstruire la grille qu'une fois stabilisée."""
        wanted = self._columns_for_width(event.width)
        if wanted == self._columns:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, lambda w=wanted: self._apply_columns(w))

    def _apply_columns(self, columns: int) -> None:
        self._resize_job = None
        if columns == self._columns:
            return
        self._columns = columns
        self._layout_cards()

    def _layout_cards(self) -> None:
        """Repositionne les cartes DÉJÀ construites dans la grille.

        Changer de nombre de colonnes ne justifie pas de les détruire pour les
        recréer : mesuré à ~80 ms par carte (CTk redessine chaque coin arrondi
        en glyphes sur un canvas, ~1100 fenêtres Tk pour 40 jeux), soit plus de
        3 s de gel complet de l'UI à chaque palier de redimensionnement — c'est
        ça qui hachait le défilement, pas le scroll lui-même (0,01 ms/cran).
        Un simple re-grid() déplace les mêmes widgets, sans rien reconstruire.
        """
        cols = max(1, self._columns)
        for i, game_id in enumerate(self._rendered_ids):
            card = self._cards.get(game_id)
            if card is not None:
                card.grid(row=i // cols, column=i % cols, sticky="n", padx=10, pady=10)

    WHEEL_PIXELS_PER_NOTCH = 60

    def _enable_smooth_scroll(self, scrollable: ctk.CTkScrollableFrame) -> None:
        """Molette : UN SEUL déplacement du canvas par cran.

        L'ancienne version bouclait sur `yview_scroll(±1, "units")` : avec
        yscrollincrement=1 (ce que CTk configure sous Windows) ça faisait
        3 pixels par cran — d'où l'impression de ne pas avancer — répartis en
        3 repaints successifs des cartes, ce qui les déchirait visuellement.
        Un seul déplacement par événement = un seul repaint, net."""
        self._wheel_handler: Optional[Callable[[Any], str]] = None
        canvas = getattr(scrollable, "_parent_canvas", None)
        if canvas is None:
            return  # version de customtkinter sans canvas exposé — no-op sûr

        canvas.configure(yscrollincrement=1)  # unité = 1 pixel, quelle que soit la plateforme

        def _on_wheel(event: Any) -> str:
            if canvas.yview() == (0.0, 1.0):
                return "break"  # rien à faire défiler
            notches = event.delta / 120 or (1 if event.delta > 0 else -1)
            GameCard.suppress_hover(0.25)
            canvas.yview_scroll(-round(notches * self.WHEEL_PIXELS_PER_NOTCH), "units")
            return "break"

        self._wheel_handler = _on_wheel
        canvas.bind("<MouseWheel>", _on_wheel)
        for child in scrollable.winfo_children():
            child.bind("<MouseWheel>", _on_wheel)
        self._throttle_scrollbar(scrollable, canvas)

    def _throttle_scrollbar(self, scrollable: ctk.CTkScrollableFrame, canvas: Any) -> None:
        """Limite la barre de défilement à un déplacement par image.

        Faire glisser le curseur de la barre envoie une commande à CHAQUE
        pixel de souris : des dizaines de repositionnements par seconde, donc
        autant de repeints complets de la grille, et des cartes qui se
        déchirent pendant le glissement. On mémorise la dernière position
        demandée et on ne l'applique qu'une fois par trame (~60 Hz) : le
        déplacement reste fidèle au geste, mais la grille n'est redessinée
        qu'une fois au lieu de trente.
        """
        scrollbar = getattr(scrollable, "_scrollbar", None)
        if scrollbar is None:
            return  # version de customtkinter sans barre exposée — no-op sûr

        pending: dict[str, Any] = {"args": None, "job": None}

        def _flush() -> None:
            pending["job"] = None
            args = pending.pop("args", None)
            pending["args"] = None
            if args:
                GameCard.suppress_hover(0.25)
                canvas.yview(*args)

        def _on_drag(*args: Any) -> None:
            pending["args"] = args
            if pending["job"] is None:
                pending["job"] = self.after(16, _flush)

        scrollbar.configure(command=_on_drag)

    def _bind_wheel_recursive(self, widget: Any) -> None:
        """Applique le handler molette à une carte ET à toute sa descendance.

        L'ancien code ne bindait que les enfants existant au moment de
        l'appel : les GameCard créées ensuite avalaient l'événement molette,
        et la grille restait bloquée dès que le curseur passait sur une carte.
        """
        handler = getattr(self, "_wheel_handler", None)
        if handler is None:
            return
        try:
            widget.bind("<MouseWheel>", handler)
            for child in widget.winfo_children():
                self._bind_wheel_recursive(child)
        except Exception:
            logger.debug("Binding molette impossible sur %r.", widget, exc_info=True)

    def _clear(self) -> None:
        for widget in self.scroll.winfo_children():
            widget.destroy()
        self._cards.clear()
        self._empty_lbl = None

    def refresh_labels(self) -> None:
        """Rechargement à chaud de tous les libellés statiques après un
        changement de langue — pas de rebuild des cartes, juste leurs textes."""
        self._title_lbl.configure(text=t("DASHBOARD_TITLE"))
        self._add_btn.configure(text=t("DASHBOARD_BTN_ADD"))
        self.scan_btn.configure(text=t("DASHBOARD_BTN_SCAN_STEAM"))
        self._update_subtitle()
        for card in self._cards.values():
            card.refresh_labels()
        if not self._cards and not self._store.load():
            self.render_games()

    def _update_subtitle(self) -> None:
        count = len(self._store.load())
        self._subtitle_lbl.configure(text=t("DASHBOARD_SUBTITLE", count=count))

    def render_games(self) -> None:
        """Met la grille en accord avec le store, en RÉUTILISANT les cartes.

        Construire une carte coûte ~55 ms (CTk dessine chaque coin arrondi
        glyphe par glyphe sur un canvas dédié) : tout raser pour tout refaire
        gelait l'UI plus de 2 s à chaque ajout ou suppression d'un seul jeu.
        On ne recrée donc que ce qui a réellement changé — un jeu absent de la
        grille, ou dont les données ont été modifiées (le dataclass Game
        compare par valeur). Les cartes intactes gardent aussi leur jaquette
        déjà téléchargée, donc plus de clignotement au retour du modal.
        """
        games = self._store.load()
        self._rendered_ids = tuple(g.id for g in games)
        self._update_subtitle()

        wanted = {g.id for g in games}
        for game_id in [gid for gid in self._cards if gid not in wanted]:
            self._cards.pop(game_id).destroy()

        for game in games:
            existing = self._cards.get(game.id)
            if existing is not None:
                if existing.game == game:
                    continue          # inchangé : on garde la carte et sa jaquette
                existing.destroy()    # édité : les libellés et la jaquette sont périmés
            card = GameCard(self.scroll, game=game,
                             state=self._latest_states.get(game.id, "inactive"),
                             on_edit=self._open_edit_modal, on_delete=self._delete_game,
                             cover_service=self._cover_service,
                             bind_wheel=self._bind_wheel_recursive)
            self._cards[game.id] = card
            self._bind_wheel_recursive(card)

        self._show_empty_state(not games)
        self._layout_cards()

    def _show_empty_state(self, visible: bool) -> None:
        if visible and self._empty_lbl is None:
            self._empty_lbl = ctk.CTkLabel(self.scroll, text=t("DASHBOARD_EMPTY_STATE"),
                                            text_color=COL_TEXT_MUTED, font=font(12))
            self._empty_lbl.grid(row=0, column=0, padx=10, pady=30)
        elif not visible and self._empty_lbl is not None:
            self._empty_lbl.destroy()
            self._empty_lbl = None

    def apply_scan_results(self, results: list[dict[str, Any]]) -> None:
        """Appelé depuis ScanWorker (via la file UI thread-safe) à chaque cycle
        (toutes les 0.5-2s selon config). CRITIQUE : ne doit JAMAIS détruire/
        recréer les widgets si la liste de jeux n'a pas changé, sous peine de
        provoquer le flicker + reset du scroll de CTkScrollableFrame observés
        précédemment. Diff par ID : rebuild complet seulement si le set/ordre
        des jeux a changé, sinon simple mise à jour du badge d'état."""
        new_states = {r["id"]: r["state"] for r in results}
        active_count = sum(1 for r in results if r["active"])
        self.status_lbl.configure(
            text=t("DASHBOARD_STATUS_SUMMARY", count=len(results), active=active_count),
            text_color=COL_TEXT_MUTED,
        )

        new_ids = tuple(r["id"] for r in results)
        if new_ids != self._rendered_ids:
            # Un jeu a été ajouté/supprimé (ou l'ordre a changé) depuis le
            # dernier rendu : seule situation qui justifie un rebuild complet.
            self._latest_states = new_states
            self.render_games()
            return

        # Aucun changement structurel : mise à jour ciblée, zéro destruction
        # de widget, zéro flicker, scroll utilisateur préservé intact.
        for game_id, state in new_states.items():
            card = self._cards.get(game_id)
            if card is not None:
                card.set_state(state)
        self._latest_states = new_states

    def scan_steam_library(self) -> None:
        self.scan_btn.configure(state="disabled", text=t("DASHBOARD_BTN_SCAN_STEAM_PROGRESS"))
        self.status_lbl.configure(text=t("DASHBOARD_SCAN_IN_PROGRESS"))
        threading.Thread(target=self._scan_steam_bg, daemon=True).start()

    def _scan_steam_bg(self) -> None:
        try:
            found = self._steam_scanner.scan_installed_games()
            added = self._store.import_steam_games(found)
            self._steam_candidates = found
            error = None
        except Exception as exc:
            logger.exception("Échec scan Steam.")
            found, added, error = [], 0, str(exc)
        self._post_ui(lambda: self._on_scan_done(len(found), added, error))

    def _on_scan_done(self, total_found: int, added: int, error: Optional[str]) -> None:
        self.scan_btn.configure(state="normal", text=t("DASHBOARD_BTN_SCAN_STEAM"))
        if error:
            self.status_lbl.configure(text=t("DASHBOARD_SCAN_ERROR", error=error), text_color=COL_RED)
            return
        self.status_lbl.configure(
            text=t("DASHBOARD_SCAN_RESULT", found=total_found, added=added),
            text_color=COL_GREEN if added else COL_TEXT_MUTED,
        )
        self.render_games()

    def _open_add_modal(self) -> None:
        GameModal(self, store=self._store, obs_loop=self._obs_loop,
                  obs_client_getter=self._get_obs_client, on_saved=self.render_games,
                  steam_candidates=self._steam_candidates)

    def _open_edit_modal(self, game: Game) -> None:
        GameModal(self, store=self._store, obs_loop=self._obs_loop,
                  obs_client_getter=self._get_obs_client, on_saved=self.render_games,
                  steam_candidates=self._steam_candidates, game=game)

    def _delete_game(self, game: Game) -> None:
        if messagebox.askyesno(t("CONFIRM_DIALOG_TITLE"), t("CONFIRM_DELETE_GAME", name=game.name)):
            self._store.delete(game.id)
            self.render_games()
