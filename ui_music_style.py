"""
ui_music_style.py — Fenêtre « Widget » : l'apparence d'un overlay.

Ouverte depuis le bouton Widget d'une carte, elle règle UNE source : couleur de
fond, couleur et présence du contour, opacité, template, image de fond. Dans
cet ordre : d'abord un style tout prêt, puis la disposition, la pochette, les
couleurs, et enfin un dessin qui remplace le tout. Même présentation que la
fenêtre Style des sous-titres : une carte par famille de réglages, l'aperçu et
les boutons restent visibles pendant qu'on fait défiler.

Le sélecteur de couleur est celui de Windows (`tkinter.colorchooser`) : il
offre déjà le dégradé de teintes, la saisie exacte d'une valeur et les
couleurs personnalisées, et il est traduit avec le système. En redessiner un
dans CustomTkinter donnerait moins bien pour beaucoup plus de code.

Rien n'est écrit tant que « Enregistrer » n'est pas pressé — sauf l'image de
fond, qui doit être recopiée pour pouvoir s'afficher dans l'aperçu.
"""
from __future__ import annotations

import io

from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from tkinter import colorchooser, filedialog
from typing import Callable, Optional

from i18n import t
from music_style import (COVERS, LAYOUTS, TEMPLATES, Style, StyleStore,
                         cover_preview_png, layout_guide_png,
                         overlay_size, preview_png, template)
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_BG, COL_BORDER,
                       COL_BORDER_ACCENT, COL_CARD, COL_CARD_HOVER, COL_RED,
                       COL_TEXT, COL_TEXT_MUTED, SmoothScroll, ctk,
                       fit_to_screen, font)

try:
    from PIL import Image
except Exception:                             # pragma: no cover
    Image = None                              # type: ignore[assignment]

#: Taille de l'aperçu en haut de la fenêtre, et de ceux des templates.
_PREVIEW = (300, 96)
_TPL_PREVIEW = (228, 76)

#: Vignettes des dispositions : trois par rangée, assez grandes pour LIRE
#: l'agencement. À 128 px le titre d'exemple finissait en « Nom de la mu… ».
_LAYOUT_PREVIEW = (228, 96)

#: Vignettes des pochettes. Plus petites : il y en a cinq par rangee, et ce
#: qui doit se lire est la FORME, pas le texte d exemple.
_OPT_PREVIEW = (128, 54)


#: Les vignettes sont rendues a ce multiple de leur taille d affichage.
#:
#: CustomTkinter redimensionne l image selon la mise a l echelle de Windows
#: (125 %, 150 %...). Lui donner une source a la taille exacte revenait a lui
#: faire AGRANDIR une petite image : d ou l aspect pixelise. Avec une source
#: plus fine, il reduit, ce qui reste net dans tous les cas.
_FINESSE = 2


def _apercu(style, size: tuple[int, int], background=None) -> Optional[ctk.CTkImage]:
    """Vignette d un style, rendue plus fine que sa taille d affichage."""
    if background is None:
        return _photo(_png_vignette(_cle(style), size), size)
    return _photo(preview_png(style, width=size[0], height=size[1],
                              background=background, echelle=_FINESSE), size)


def _cle(style: Style) -> Style:
    """Le style sans ce qui ne change pas le dessin.

    Le nom du template s efface a la premiere retouche : le garder dans la
    cle du cache refaisait toutes les vignettes pour un rendu identique.
    """
    return replace(style, template="", background_image=False)


@lru_cache(maxsize=256)
def _png_vignette(style: Style, size: tuple[int, int]) -> bytes:
    """Rendu d une vignette, garde d une ouverture de fenetre a l autre.

    Les huit templates et les dix vignettes de forme coutaient ~360 ms a
    chaque ouverture ; ils ne changent qu avec le style.
    """
    return preview_png(style, width=size[0], height=size[1], echelle=_FINESSE)


@lru_cache(maxsize=128)
def _png_pochette(style: Style, forme: str) -> bytes:
    return cover_preview_png(style, forme, width=_OPT_PREVIEW[0],
                             height=_OPT_PREVIEW[1], echelle=_FINESSE)


def _photo(png: bytes, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
    """Enveloppe une image PNG pour CustomTkinter.

    La source n est PAS redimensionnee ici : `size` ne dit que la taille
    d affichage, et garder les pixels d origine laisse a CTkImage de quoi
    rester net sur un ecran a forte densite.
    """
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(png)) as brut:
            # RGBA, pas RGB : les coins arrondis des vignettes doivent laisser
            # voir le bouton derriere, pas un aplat noir.
            image = brut.convert("RGBA")
        return ctk.CTkImage(light_image=image, dark_image=image, size=size)
    except Exception:
        return None


class GuideDialog(ctk.CTkToplevel):
    """Le gabarit coté, et le choix d'une image dessinée par l'utilisateur.

    Le gabarit est montré en grand avant tout bouton : c'est lui qui répond à
    la question « où est-ce que je peux dessiner sans passer sous un texte ? ».
    """

    def __init__(self, master, key: str, store: StyleStore,
                 on_change: Callable[[], None]) -> None:
        super().__init__(master, fg_color=COL_BG)
        self._key = key
        self._store = store
        self._on_change = on_change

        self.title(t("MUSIC_STYLE_IMAGE_TITLE"))
        self.resizable(False, False)
        self.transient(master.winfo_toplevel())
        self.grid_columnconfigure(0, weight=1)

        self._guide = _photo(layout_guide_png(), (560, 180))  # deja a la bonne taille
        cadre = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14,
                             border_width=1, border_color=COL_BORDER)
        cadre.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        ctk.CTkLabel(cadre, text="", image=self._guide).pack(padx=16, pady=16)

        ctk.CTkLabel(self, text=t("MUSIC_STYLE_IMAGE_HINT"), font=font(11),
                     text_color=COL_TEXT_MUTED, justify="left", anchor="w",
                     wraplength=560).grid(row=1, column=0, sticky="ew",
                                          padx=20, pady=(0, 12))

        boutons = ctk.CTkFrame(self, fg_color="transparent")
        boutons.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        ctk.CTkButton(boutons, text=t("MUSIC_STYLE_SAVE_GUIDE"), height=36,
                      corner_radius=9, fg_color="transparent", border_width=1,
                      border_color=COL_BORDER, hover_color=COL_CARD,
                      text_color=COL_TEXT, font=font(12),
                      command=self._export_guide).pack(side="left")
        ctk.CTkButton(boutons, text=t("MUSIC_STYLE_PICK_IMAGE"), height=36,
                      corner_radius=9, fg_color=COL_ACCENT,
                      hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                      font=font(12, "bold"),
                      command=self._pick).pack(side="left", padx=(10, 0))
        self._remove_btn = ctk.CTkButton(
            boutons, text=t("MUSIC_STYLE_REMOVE_IMAGE"), height=36,
            corner_radius=9, fg_color="transparent", border_width=1,
            border_color=COL_RED, hover_color=COL_CARD, text_color=COL_RED,
            font=font(12), command=self._remove)
        self._remove_btn.pack(side="right")
        self._sync_remove()

        # Le verrou de saisie vient APRÈS la construction : le poser sur une
        # fenêtre pas encore dessinée échoue sous Windows, et la fenêtre
        # parente resterait cliquable.
        self.after(120, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
        except Exception:
            pass

    def _sync_remove(self) -> None:
        present = self._store.background_path(self._key) is not None
        self._remove_btn.configure(state="normal" if present else "disabled")

    def _export_guide(self) -> None:
        chemin = filedialog.asksaveasfilename(
            parent=self, defaultextension=".png",
            initialfile=f"gabarit-{self._key}.png",
            filetypes=[("PNG", "*.png")])
        if not chemin:
            return
        try:
            Path(chemin).write_bytes(layout_guide_png())
        except OSError:
            pass

    def _pick(self) -> None:
        chemin = filedialog.askopenfilename(
            parent=self, filetypes=[(t("MUSIC_STYLE_IMAGE_FILTER"),
                                     "*.png *.jpg *.jpeg *.webp")])
        if not chemin:
            return
        if self._store.set_background(self._key, Path(chemin)):
            self._sync_remove()
            self._on_change()

    def _remove(self) -> None:
        self._store.clear_background(self._key)
        self._sync_remove()
        self._on_change()


class StyleDialog(ctk.CTkToplevel):
    """Réglages d'apparence d'une source, avec aperçu permanent."""

    def __init__(self, master, key: str, label: str, store: StyleStore,
                 on_saved: Callable[[str, Style], None]) -> None:
        super().__init__(master, fg_color=COL_BG)
        self._key = key
        self._store = store
        self._on_saved = on_saved
        self._style = store.get(key)
        self._preview_img: Optional[ctk.CTkImage] = None
        self._tpl_imgs: list[ctk.CTkImage] = []
        self._opt_imgs: list[ctk.CTkImage] = []
        self._opt_btns: dict[str, dict[str, ctk.CTkButton]] = {}
        self._tpl_btns: dict[str, ctk.CTkButton] = {}
        self._cartes: list[ctk.CTkFrame] = []
        # Rendus reportés pendant qu'on fait glisser le curseur d'opacité.
        self._apercu_prevu: Optional[str] = None
        self._vignettes_prevues: Optional[str] = None

        self.title(t("MUSIC_STYLE_TITLE", app=label))
        self.transient(master.winfo_toplevel())

        # Seules les cartes defilent : l apercu (en haut) et Enregistrer (en
        # bas) restent a l ecran. Avant, tout defilait ensemble : on reglait
        # une couleur sans voir le resultat, et sur un portable le bouton
        # Enregistrer etait sous le bord de l ecran. La hauteur suit l ecran,
        # jamais le contenu — mise a l echelle de Windows comprise.
        fit_to_screen(self, 860, 960, 720, 480)
        # Collee en haut de l ecran : placee par Windows en cascade sous la
        # fenetre principale, elle depassait en bas et cachait Enregistrer.
        self.geometry(f"+{max(0, master.winfo_toplevel().winfo_rootx() + 60)}+20")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._build_preview()
        self._corps = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._corps.grid(row=1, column=0, sticky="nsew", padx=12)
        self._corps.grid_columnconfigure(0, weight=1)
        self._scroller = SmoothScroll(self._corps)

        self._build_templates()
        self._build_shapes()
        self._build_colors()
        self._build_image()
        self._build_actions()

        self._refresh()
        self.after(120, self._grab)

    def _grab(self) -> None:
        try:
            self.grab_set()
        except Exception:
            pass

    # -- briques de mise en page ------------------------------------------- #

    def _card(self, titre: str) -> ctk.CTkFrame:
        """Une carte titrée, à la suite des précédentes."""
        carte = ctk.CTkFrame(self._corps, fg_color=COL_CARD, corner_radius=12,
                             border_width=1, border_color=COL_BORDER)
        carte.grid(row=len(self._cartes), column=0, sticky="ew", padx=8, pady=6)
        carte.grid_columnconfigure(0, weight=1)
        self._cartes.append(carte)
        ctk.CTkLabel(carte, text=titre, font=font(13, "bold"),
                     text_color=COL_TEXT, anchor="w").grid(
                         row=0, column=0, columnspan=3, sticky="w",
                         padx=16, pady=(12, 6))
        return carte

    def _tiles(self, carte: ctk.CTkFrame, row: int, options: list, cols: int,
               hauteur: int, commande: Callable[[str], None]
               ) -> dict[str, ctk.CTkButton]:
        """Grille de vignettes cliquables qui se partagent la largeur.

        `options` : (clé, libellé, image ou None). Les colonnes sont de même
        largeur et étirées : plus de rangée qui s'arrête au milieu de la
        fenêtre.
        """
        grille = ctk.CTkFrame(carte, fg_color="transparent")
        grille.grid(row=row, column=0, columnspan=3, sticky="ew", padx=10,
                    pady=(0, 10))
        for colonne in range(cols):
            grille.grid_columnconfigure(colonne, weight=1, uniform="vignette")
        boutons: dict[str, ctk.CTkButton] = {}
        for index, (cle, libelle, image) in enumerate(options):
            bouton = ctk.CTkButton(
                grille, text=libelle, image=image, compound="top",
                height=hauteur + 34, corner_radius=10, fg_color=COL_BG,
                hover_color=COL_CARD_HOVER, border_width=2,
                border_color=COL_BG, text_color=COL_TEXT, font=font(11),
                command=lambda k=cle: commande(k))
            bouton.grid(row=index // cols, column=index % cols, sticky="ew",
                        padx=4, pady=4)
            boutons[cle] = bouton
        return boutons

    def _field(self, carte: ctk.CTkFrame, row: int, cle: str) -> None:
        """Libellé d'une ligne de réglage, à gauche de son contrôle."""
        ctk.CTkLabel(carte, text=t(cle), font=font(12),
                     text_color=COL_TEXT_MUTED, anchor="w").grid(
                         row=row, column=0, sticky="w", padx=(16, 12), pady=6)

    # -- aperçu ------------------------------------------------------------ #

    def _build_preview(self) -> None:
        cadre = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14,
                             border_width=1, border_color=COL_BORDER)
        cadre.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        self._preview_lbl = ctk.CTkLabel(cadre, text="")
        self._preview_lbl.pack(padx=16, pady=(14, 6))
        # La taille de source a donner a OBS pour CETTE combinaison. Elle suit
        # les reglages en direct : c'est ici qu'on les change.
        self._size_lbl = ctk.CTkLabel(cadre, text="", font=font(11),
                                      text_color=COL_ACCENT)
        self._size_lbl.pack(padx=16, pady=(0, 12))

    def _refresh(self) -> None:
        """Redessine l'aperçu et les vignettes à partir des réglages en cours."""
        self._refresh_apercu()
        self._marquer_selection()

    def _refresh_apercu(self) -> None:
        """L'aperçu du haut et les libellés, sans les grilles de vignettes."""
        fond = self._store.background_path(self._key)
        self._preview_img = _apercu(self._style, _PREVIEW, background=fond)
        self._preview_lbl.configure(image=self._preview_img)
        self._bg_swatch.configure(fg_color=self._style.bg,
                                  hover_color=self._style.bg)
        self._border_swatch.configure(fg_color=self._style.border,
                                      hover_color=self._style.border,
                                      state="normal" if self._style.border_on
                                      else "disabled")
        self._opacity_lbl.configure(text=f"{self._style.opacity} %")
        largeur, hauteur = overlay_size(self._style)
        self._size_lbl.configure(text=t("MUSIC_SOURCE_SIZE", w=largeur,
                                        h=hauteur))
        for champ, var in self._part_vars.items():
            var.set(getattr(self._style, champ))

    # -- templates --------------------------------------------------------- #

    def _build_templates(self) -> None:
        """En premier : un style tout prêt est le point de départ le plus
        court, les cartes suivantes servent à le retoucher."""
        carte = self._card(t("MUSIC_STYLE_TEMPLATES"))
        options = []
        for tpl in TEMPLATES:
            image = _apercu(tpl.style, _TPL_PREVIEW)
            if image is not None:
                self._tpl_imgs.append(image)
            options.append((tpl.key, tpl.label, image))
        self._tpl_btns = self._tiles(carte, 1, options, 3, _TPL_PREVIEW[1],
                                     self._apply)

    def _apply(self, key: str) -> None:
        """Un clic applique le modèle. L'enregistrement reste un geste à part."""
        tpl = template(key)
        if tpl is None:
            return
        if tpl.full:
            # Certains modeles portent leur disposition : pastilles de
            # fenetre, pochette carree et boutons de transport font partie du
            # dessin. Les appliquer a moitie ne donnerait pas le modele.
            self._style = tpl.style
        else:
            # Les autres ne portent que l habillage : appliquer une couleur ne
            # doit pas reprendre a l utilisateur sa mise en page.
            self._style = replace(tpl.style, layout=self._style.layout,
                                  cover=self._style.cover,
                                  show_wave=self._style.show_wave,
                                  show_artist=self._style.show_artist,
                                  show_app=self._style.show_app,
                                  show_dots=self._style.show_dots,
                                  show_progress=self._style.show_progress,
                                  show_controls=self._style.show_controls)
        self._border_var.set(self._style.border_on)
        self._opacity_slider.set(self._style.opacity)
        self._refresh()

    # -- formes ------------------------------------------------------------ #

    def _build_shapes(self) -> None:
        """Disposition d ensemble et ses briques, puis forme de la pochette.

        Chaque vignette est le rendu reel de l option, avec un titre et un
        artiste d exemple et un rond clair a la place de la pochette : on
        choisit en VOYANT le resultat, sans avoir a l appliquer d abord.
        Sans image a la construction : `_refresh`, appele juste apres, dessine
        toutes les vignettes. Les rendre deux fois doublait le temps
        d ouverture de la fenetre.
        """
        carte = self._card(t("MUSIC_STYLE_LAYOUTS").capitalize())
        self._opt_btns["layout"] = self._tiles(
            carte, 1, [(o.key, o.label, None) for o in LAYOUTS], 3,
            _LAYOUT_PREVIEW[1], lambda cle: self._choisir("layout", cle))
        self._build_parts(carte, 2)

        carte = self._card(t("MUSIC_STYLE_COVERS").capitalize())
        self._opt_btns["cover"] = self._tiles(
            carte, 1, [(o.key, o.label, None) for o in COVERS], len(COVERS),
            _OPT_PREVIEW[1], lambda cle: self._choisir("cover", cle))

    def _choisir(self, champ: str, cle: str) -> None:
        self._set(**{champ: cle})
        self._refresh()

    def _marquer_selection(self) -> None:
        """Redessine les vignettes des grilles et souligne l option active.

        Les vignettes suivent les couleurs en cours d edition : sans ce
        redessin, changer le fond ou l accent laisserait les grilles sur
        l ancien habillage, donc sur un rendu faux.
        """
        self._opt_imgs = []
        for champ, boutons in self._opt_btns.items():
            actif = getattr(self._style, champ)
            for cle, bouton in boutons.items():
                if champ == "cover":
                    # La grille des pochettes montre LA FORME, en grand. Sur
                    # une carte complete, la pochette n est qu un detail de
                    # coin : « Auto » et « Vinyle » y paraissaient identiques.
                    apercu = _photo(_png_pochette(_cle(self._style), cle),
                                    _OPT_PREVIEW)
                else:
                    apercu = _apercu(replace(self._style, **{champ: cle}),
                                     _LAYOUT_PREVIEW)
                if apercu is not None:
                    self._opt_imgs.append(apercu)
                bouton.configure(image=apercu,
                                 border_color=COL_BORDER_ACCENT if cle == actif
                                 else COL_BG)
        # Le modèle reste souligné tant qu'aucune retouche ne l'a quitté.
        for cle, bouton in self._tpl_btns.items():
            bouton.configure(border_color=COL_BORDER_ACCENT
                             if cle == self._style.template else COL_BG)

    # -- briques ------------------------------------------------------------ #

    def _build_parts(self, carte: ctk.CTkFrame, row: int) -> None:
        """Ce qu on garde ou non. Masquer une brique libere sa place."""
        ligne = ctk.CTkFrame(carte, fg_color="transparent")
        ligne.grid(row=row, column=0, columnspan=3, sticky="ew", padx=16,
                   pady=(2, 12))

        self._part_vars: dict[str, ctk.BooleanVar] = {}
        for index, (champ, cle) in enumerate((
                ("show_wave", "MUSIC_STYLE_WAVE"),
                ("show_artist", "MUSIC_STYLE_ARTIST"),
                ("show_app", "MUSIC_STYLE_APP"),
                ("show_dots", "MUSIC_STYLE_DOTS"),
                ("show_progress", "MUSIC_STYLE_PROGRESS"),
                ("show_controls", "MUSIC_STYLE_CONTROLS"))):
            ligne.grid_columnconfigure(index % 3, weight=1, uniform="brique")
            var = ctk.BooleanVar(value=getattr(self._style, champ))
            self._part_vars[champ] = var
            ctk.CTkSwitch(ligne, text=t(cle), font=font(12), variable=var,
                          onvalue=True, offvalue=False,
                          progress_color=COL_ACCENT, text_color=COL_TEXT,
                          command=lambda c=champ: self._basculer(c)).grid(
                              row=index // 3, column=index % 3, sticky="w",
                              pady=5)

    def _basculer(self, champ: str) -> None:
        self._set(**{champ: bool(self._part_vars[champ].get())})
        self._refresh()

    # -- couleurs ---------------------------------------------------------- #

    def _swatch(self, parent, couleur: str,
                commande: Callable[[], None]) -> ctk.CTkButton:
        """Pastille cliquable : elle MONTRE la couleur, et l'ouvre au clic."""
        return ctk.CTkButton(parent, text="", width=64, height=26,
                             corner_radius=6, fg_color=couleur,
                             hover_color=couleur, border_width=1,
                             border_color=COL_BORDER, command=commande)

    def _build_colors(self) -> None:
        carte = self._card(t("SUBS_STY_COLORS"))
        carte.grid_columnconfigure(0, weight=0)
        carte.grid_columnconfigure(1, weight=1)

        self._field(carte, 1, "MUSIC_STYLE_BG")
        self._bg_swatch = self._swatch(carte, self._style.bg, self._pick_bg)
        self._bg_swatch.grid(row=1, column=1, sticky="w", pady=6)

        self._field(carte, 2, "MUSIC_STYLE_BORDER_ON")
        self._border_var = ctk.BooleanVar(value=self._style.border_on)
        ctk.CTkSwitch(carte, text="", variable=self._border_var, onvalue=True,
                      offvalue=False, progress_color=COL_ACCENT,
                      command=self._toggle_border).grid(
                          row=2, column=1, sticky="w", pady=6)

        self._field(carte, 3, "MUSIC_STYLE_BORDER")
        self._border_swatch = self._swatch(carte, self._style.border,
                                           self._pick_border)
        self._border_swatch.grid(row=3, column=1, sticky="w", pady=6)

        self._field(carte, 4, "MUSIC_STYLE_OPACITY")
        ligne = ctk.CTkFrame(carte, fg_color="transparent")
        ligne.grid(row=4, column=1, sticky="ew", padx=(0, 16), pady=(6, 14))
        ligne.grid_columnconfigure(0, weight=1)
        self._opacity_slider = ctk.CTkSlider(
            ligne, from_=0, to=100, number_of_steps=100,
            progress_color=COL_ACCENT, button_color=COL_ACCENT,
            button_hover_color=COL_ACCENT_HOVER, command=self._set_opacity)
        self._opacity_slider.set(self._style.opacity)
        self._opacity_slider.grid(row=0, column=0, sticky="ew")
        self._opacity_lbl = ctk.CTkLabel(ligne, text="", width=54,
                                         font=font(11), text_color=COL_TEXT,
                                         anchor="e")
        self._opacity_lbl.grid(row=0, column=1, padx=(10, 0))

    def _choose(self, actuelle: str) -> Optional[str]:
        """Panneau de couleurs du système, ouvert sur la valeur courante."""
        try:
            _rgb, hexa = colorchooser.askcolor(color=actuelle, parent=self)
        except Exception:
            return None
        return hexa.upper() if hexa else None

    def _pick_bg(self) -> None:
        couleur = self._choose(self._style.bg)
        if couleur:
            self._set(bg=couleur)
            self._refresh()

    def _pick_border(self) -> None:
        couleur = self._choose(self._style.border)
        if couleur:
            self._set(border=couleur)
            self._refresh()

    def _toggle_border(self) -> None:
        self._set(border_on=bool(self._border_var.get()))
        self._refresh()

    def _set(self, **champs) -> None:
        """Applique une retouche manuelle.

        Le nom du template est effacé au passage : le style ne correspond plus
        à un modèle, et continuer à afficher le sien mentirait sur ce qui est
        réglé.
        """
        self._style = replace(self._style, template="", **champs)

    # -- opacité ----------------------------------------------------------- #

    def _set_opacity(self, valeur: float) -> None:
        """Suit le curseur sans figer la fenêtre.

        Le curseur envoie un évènement par cran. Tout redessiner à chaque fois
        coûtait ~190 ms par cran : la fenêtre ne suivait plus la souris.
        L'aperçu suit au plus toutes les 60 ms ; les vignettes attendent que
        le curseur s'arrête.
        """
        opacite = int(round(valeur))
        if opacite == self._style.opacity:
            return
        self._set(opacity=opacite)
        self._opacity_lbl.configure(text=f"{opacite} %")
        if self._apercu_prevu is None:
            self._apercu_prevu = self.after(60, self._apercu_differe)
        if self._vignettes_prevues is not None:
            self.after_cancel(self._vignettes_prevues)
        self._vignettes_prevues = self.after(250, self._vignettes_differees)

    def _apercu_differe(self) -> None:
        self._apercu_prevu = None
        if self.winfo_exists():
            self._refresh_apercu()

    def _vignettes_differees(self) -> None:
        self._vignettes_prevues = None
        if self.winfo_exists():
            self._marquer_selection()

    # -- image de fond ----------------------------------------------------- #

    def _build_image(self) -> None:
        carte = self._card(t("MUSIC_STYLE_IMAGE_TITLE"))
        ctk.CTkButton(carte, text=t("MUSIC_STYLE_IMAGE"), height=36,
                      corner_radius=9, fg_color="transparent", border_width=1,
                      border_color=COL_BORDER_ACCENT, hover_color=COL_CARD_HOVER,
                      text_color=COL_ACCENT, font=font(12, "bold"),
                      command=self._open_guide).grid(
                          row=1, column=0, sticky="w", padx=16, pady=(0, 14))

    def _open_guide(self) -> None:
        GuideDialog(self, self._key, self._store, self._refresh)

    # -- actions ----------------------------------------------------------- #

    def _build_actions(self) -> None:
        ligne = ctk.CTkFrame(self, fg_color="transparent")
        ligne.grid(row=2, column=0, sticky="ew", padx=24, pady=14)
        ctk.CTkButton(ligne, text=t("MUSIC_STYLE_RESET"), height=36, width=150,
                      corner_radius=9, fg_color="transparent", border_width=1,
                      border_color=COL_BORDER, hover_color=COL_CARD,
                      text_color=COL_TEXT_MUTED, font=font(12),
                      command=self._reset).pack(side="left")
        ctk.CTkButton(ligne, text=t("MUSIC_STYLE_SAVE"), height=36, width=140,
                      corner_radius=9, fg_color=COL_ACCENT,
                      hover_color=COL_ACCENT_HOVER, text_color=COL_BG,
                      font=font(12, "bold"),
                      command=self._save).pack(side="right")
        ctk.CTkButton(ligne, text=t("MUSIC_STYLE_CANCEL"), height=36, width=110,
                      corner_radius=9, fg_color="transparent", border_width=1,
                      border_color=COL_BORDER, hover_color=COL_CARD,
                      text_color=COL_TEXT, font=font(12),
                      command=self.destroy).pack(side="right", padx=(0, 10))

    def _reset(self) -> None:
        self._store.reset(self._key)
        self._store.clear_background(self._key)
        self._style = self._store.get(self._key)
        self._border_var.set(self._style.border_on)
        self._opacity_slider.set(self._style.opacity)
        self._refresh()
        self._on_saved(self._key, self._style)

    def _save(self) -> None:
        # `background_image` se déduit du disque, jamais de l'édition en cours.
        present = self._store.background_path(self._key) is not None
        style = replace(self._style, background_image=present)
        self._store.save(self._key, style)
        self._on_saved(self._key, style)
        self.destroy()
