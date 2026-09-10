"""Fenêtres modales du dashboard : relecture des patchs et fiche de jeu."""
from __future__ import annotations

import uuid
from tkinter import filedialog
from typing import Any, Callable, Optional, Sequence

import cv2
import numpy as np
from PIL import Image

import screen_match
from app_paths import logger
from detection import (_REVIEWS, _capture_screen_bgr, _downscale,
                       _imread_unicode, image_stamp)
from games import Game, GameStore
from i18n import t
from obs_client import AsyncLoopThread, OBSClient
from ui_common import (COL_ACCENT, COL_ACCENT_HOVER, COL_ACCENT_SOFT, COL_BG,
                       COL_BORDER, COL_CARD, COL_CARD_HOVER, COL_GREEN,
                       COL_RED, COL_TEXT_MUTED, COL_YELLOW, ctk, font)


# ============================================================================
# FENÊTRE : CONTRÔLE DU CADRAGE AUTOMATIQUE
# ============================================================================
PREVIEW_MAX_WIDTH = 760

def bordered_menu(parent, **kwargs) -> tuple[Any, Any]:
    """CTkOptionMenu entouré d'un cadre fin. Retourne (cadre, menu).

    CustomTkinter REFUSE `border_width` sur un CTkOptionMenu (ValueError :
    argument non supporté) — le contour ne peut venir que d'un cadre parent.
    Sans lui, un menu déroulant sur fond sombre n'a pour repère que sa flèche :
    rien ne montre où le champ commence et où il s'arrête.
    """
    frame = ctk.CTkFrame(parent, fg_color="transparent", border_width=1,
                         border_color=COL_BORDER, corner_radius=8)
    menu = ctk.CTkOptionMenu(frame, **kwargs)
    menu.pack(fill="both", expand=True, padx=2, pady=2)
    return frame, menu


class PatchReviewDialog(ctk.CTkToplevel):
    """Montre CE QUE l'agent regarde dans une capture de référence.

    L'agent choisit ses fragments tout seul, mais rien ne permettait de
    vérifier son choix : si un fragment tombait sur le décor plutôt que sur le
    HUD, la détection se dégradait sans que personne puisse le voir.

    Chaque fragment est encadré et NUMÉROTÉ, et ce numéro sert à le refuser.
    C'est indispensable : la sélection est déterministe, donc relancer le même
    calcul sur la même image redonnerait exactement les mêmes cadres. Sans
    exclusion, un bouton « ce n'est pas bon » tournerait en rond indéfiniment.
    """

    def __init__(self, master, image_path: str, kind: str,
                 menu_images: list[str], ingame_images: list[str],
                 excluded: list[tuple[float, ...]],
                 manual: list[tuple[float, ...]],
                 count: Optional[int],
                 on_validated: Callable[[list[tuple[float, ...]],
                                         list[tuple[float, ...]],
                                         Optional[int]], None]) -> None:
        super().__init__(master)
        self.title(t("PATCH_REVIEW_TITLE"))
        self.configure(fg_color=COL_BG)
        self.transient(master)
        self.grab_set()

        self._path = image_path
        self._kind = kind
        self._menu_images = menu_images
        self._ingame_images = ingame_images
        self._excluded = list(excluded)
        self._manual = [tuple(float(v) for v in box) for box in manual]
        self._on_validated = on_validated
        self._initial_count = count
        self._patches: list[screen_match.Patch] = []
        self._checks: list[ctk.BooleanVar] = []
        self._preview_img: Optional[ctk.CTkImage] = None
        self._selected: Optional[int] = None     # index dans _patches, pas dans _manual
        # Nombre de zones affichées. Figé à l'ouverture puis ajusté uniquement
        # par « + » et « Supprimer » : sans lui, transformer une zone
        # automatique en zone manuelle libérait un créneau que la recherche
        # automatique remplissait aussitôt — une zone surgissait alors que
        # l'utilisateur venait simplement d'en déplacer une.
        self._budget: Optional[int] = count
        self._base: Optional[np.ndarray] = None  # image déjà réduite à l'aperçu

        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text=t("PATCH_REVIEW_INTRO"), font=font(12),
                     text_color=COL_TEXT_MUTED, wraplength=PREVIEW_MAX_WIDTH,
                     justify="left").grid(row=0, column=0, sticky="w", padx=18, pady=(16, 6))

        self._preview_lbl = ctk.CTkLabel(self, text="")
        self._preview_lbl.grid(row=1, column=0, padx=18)

        self._verdict_lbl = ctk.CTkLabel(self, text="", font=font(12, "bold"),
                                          wraplength=PREVIEW_MAX_WIDTH, justify="left")
        self._verdict_lbl.grid(row=2, column=0, sticky="w", padx=18, pady=(10, 2))

        self._status_lbl = ctk.CTkLabel(self, text="", font=font(11),
                                         text_color=COL_TEXT_MUTED, anchor="w")
        self._status_lbl.grid(row=3, column=0, sticky="w", padx=18)

        ctk.CTkLabel(self, text=t("PATCH_REVIEW_EXCLUDE_HINT"), font=font(11),
                     text_color=COL_TEXT_MUTED, anchor="w").grid(
            row=4, column=0, sticky="w", padx=18, pady=(8, 2))

        self._checks_row = ctk.CTkFrame(self, fg_color="transparent")
        self._checks_row.grid(row=5, column=0, sticky="w", padx=14)

        # --- Recadrage manuel ------------------------------------------------
        ctk.CTkLabel(self, text=t("PATCH_REVIEW_MANUAL_HINT"), font=font(11),
                     text_color=COL_TEXT_MUTED, anchor="w",
                     wraplength=PREVIEW_MAX_WIDTH, justify="left").grid(
            row=6, column=0, sticky="w", padx=18, pady=(12, 2))

        manual_box = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=10,
                                   border_width=1, border_color=COL_BORDER)
        manual_box.grid(row=7, column=0, sticky="ew", padx=18)
        manual_box.grid_columnconfigure(1, weight=1)

        zone_row = ctk.CTkFrame(manual_box, fg_color="transparent")
        zone_row.grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 6))
        self._zone_var = ctk.StringVar()
        # MENU_FRAME : sans contour, un menu déroulant sur fond sombre se fond
        # dans la fenêtre — rien ne dit où le champ s'arrête tant qu'on ne l'a
        # pas ouvert.
        zone_frame, self._zone_menu = bordered_menu(
            zone_row, values=[""], variable=self._zone_var,
            width=240, fg_color=COL_BG, button_color=COL_ACCENT,
            button_hover_color=COL_ACCENT_HOVER, font=font(11),
            command=self._on_zone_selected)
        zone_frame.pack(side="left")
        ctk.CTkButton(zone_row, text="+", width=38, height=28, font=font(16, "bold"),
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                      text_color="#0F0C1B", command=self._add_zone).pack(side="left", padx=(8, 0))

        self._sliders: dict[str, ctk.CTkSlider] = {}
        self._slider_lbls: dict[str, ctk.CTkLabel] = {}
        defaults = {"x": 0.5, "y": 0.5, "w": 0.25, "h": 0.15}
        for row, (key, label_key) in enumerate(
                (("x", "PATCH_REVIEW_SLIDER_X"), ("y", "PATCH_REVIEW_SLIDER_Y"),
                 ("w", "PATCH_REVIEW_SLIDER_W"), ("h", "PATCH_REVIEW_SLIDER_H")), start=1):
            ctk.CTkLabel(manual_box, text=t(label_key), font=font(11), width=90,
                         anchor="w").grid(row=row, column=0, sticky="w", padx=(12, 6), pady=2)
            slider = ctk.CTkSlider(manual_box, from_=0.03 if key in ("w", "h") else 0.0,
                                    to=1.0, number_of_steps=97 if key in ("w", "h") else 100,
                                    button_color=COL_ACCENT, progress_color=COL_ACCENT_SOFT,
                                    command=lambda _v, k=key: self._on_slider(k))
            slider.set(defaults[key])
            slider.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=2)
            self._sliders[key] = slider
            lbl = ctk.CTkLabel(manual_box, text="", font=font(11), width=52,
                               text_color=COL_TEXT_MUTED)
            lbl.grid(row=row, column=2, padx=(0, 12))
            self._slider_lbls[key] = lbl

        zone_btns = ctk.CTkFrame(manual_box, fg_color="transparent")
        zone_btns.grid(row=5, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 12))
        ctk.CTkButton(zone_btns, text=t("PATCH_REVIEW_BTN_APPLY_ZONE"), width=170, height=30,
                      fg_color=COL_ACCENT_SOFT, hover_color=COL_ACCENT_HOVER, font=font(11),
                      command=self._apply_zone).pack(side="left")
        ctk.CTkButton(zone_btns, text=t("PATCH_REVIEW_BTN_DELETE_ZONE"), width=150, height=30,
                      fg_color="#3A1420", hover_color=COL_RED, font=font(11),
                      command=self._delete_zone).pack(side="left", padx=8)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=8, column=0, sticky="ew", padx=18, pady=16)
        self._recompute_btn = ctk.CTkButton(
            buttons, text=t("PATCH_REVIEW_BTN_RECOMPUTE"), width=180, height=34,
            fg_color=COL_CARD, hover_color=COL_CARD_HOVER, border_width=1,
            border_color=COL_BORDER, font=font(12), command=self._recompute)
        self._recompute_btn.pack(side="left")
        ctk.CTkButton(buttons, text=t("PATCH_REVIEW_BTN_TEST"), width=180, height=34,
                      fg_color=COL_CARD, hover_color=COL_CARD_HOVER, border_width=1,
                      border_color=COL_BORDER, font=font(12),
                      command=self._test_live).pack(side="left", padx=8)
        ctk.CTkButton(buttons, text=t("PATCH_REVIEW_BTN_VALIDATE"), width=160, height=34,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                      text_color="#0F0C1B", font=font(12, "bold"),
                      command=self._validate).pack(side="right")

        self._refresh()

    # -- Calcul et rendu ---------------------------------------------------- #

    def _reference(self) -> Optional[np.ndarray]:
        return _imread_unicode(self._path, cv2.IMREAD_COLOR)

    def _refresh(self, highlight: Optional[int] = None) -> None:
        original = self._reference()
        if original is None:
            self._verdict_lbl.configure(text=t("PATCH_REVIEW_UNREADABLE"), text_color=COL_RED)
            return

        if self._base is None:
            # Réduit UNE fois : les curseurs redessinent à chaque cran, et
            # retailler l'image d'origine à chaque fois saccaderait le réglage.
            h, w = original.shape[:2]
            ratio = min(1.0, PREVIEW_MAX_WIDTH / w)
            self._base = cv2.resize(original, (int(w * ratio), int(h * ratio)),
                                     interpolation=cv2.INTER_AREA)

        small = _downscale(original)
        if self._budget is None:
            self._budget = len(screen_match.build_patches(small)) or 1
        self._patches = screen_match.build_patches(
            small, count=max(self._budget, len(self._manual)),
            exclude=self._excluded, manual=self._manual)
        if not self._patches:
            self._verdict_lbl.configure(text=t("PATCH_REVIEW_NO_PATCHES"), text_color=COL_RED)
            return

        self._show(screen_match.draw_preview(self._base, self._patches,
                                              manual_count=len(self._manual),
                                              highlight=highlight))
        self._build_checks()
        self._refresh_zone_menu()
        self._update_verdict(small)

    def _show(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        self._preview_img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(w, h))
        self._preview_lbl.configure(image=self._preview_img, text="")

    # -- Recadrage manuel aux curseurs -------------------------------------- #

    def _zone_labels(self) -> list[str]:
        """Une entrée par zone AFFICHÉE, numérotée comme sur l'aperçu.

        Les zones automatiques y figurent aussi : les sélectionner permet de
        les repositionner directement, sans avoir à les écarter puis à en
        retracer une par-dessus.
        """
        labels = []
        for index in range(1, len(self._patches) + 1):
            key = ("PATCH_REVIEW_ZONE_MANUAL" if index <= len(self._manual)
                   else "PATCH_REVIEW_ZONE_AUTO")
            labels.append(t(key, n=index))
        return labels or [t("PATCH_REVIEW_ZONE_NONE")]

    def _refresh_zone_menu(self) -> None:
        labels = self._zone_labels()
        self._zone_menu.configure(values=labels)
        if self._selected is not None and self._selected < len(labels):
            self._zone_var.set(labels[self._selected])
        else:
            self._selected = None
            self._zone_var.set(labels[0])
        for key, slider in self._sliders.items():
            self._slider_lbls[key].configure(text=f"{slider.get():.2f}")

    def _current_box(self) -> tuple[float, float, float, float]:
        return tuple(self._sliders[k].get() for k in ("x", "y", "w", "h"))

    def _load_sliders(self, box: Sequence[float]) -> None:
        for key, value in zip(("x", "y", "w", "h"), box):
            self._sliders[key].set(float(value))

    def _on_zone_selected(self, label: str) -> None:
        labels = self._zone_labels()
        self._selected = labels.index(label) if label in labels else None
        if self._selected is not None and self._selected < len(self._patches):
            self._load_sliders(screen_match.patch_box(self._patches[self._selected]))
        self._refresh(highlight=None if self._selected is None else self._selected + 1)

    # Emplacements proposés à une nouvelle zone, dans l'ordre d'essai.
    _NEW_ZONE_SPOTS = tuple((cx, cy) for cy in (0.25, 0.5, 0.75)
                            for cx in (0.25, 0.5, 0.75))
    _NEW_ZONE_SIZE = (0.25, 0.20)

    @staticmethod
    def _overlaps(a: Sequence[float], b: Sequence[float]) -> bool:
        return (abs(a[0] - b[0]) < (a[2] + b[2]) / 2
                and abs(a[1] - b[1]) < (a[3] + b[3]) / 2)

    def _free_spot(self) -> tuple[float, float, float, float]:
        """Emplacement libre pour une nouvelle zone.

        Les créer toutes au centre les empilait au pixel près : on n'en voyait
        qu'une seule, et déplacer les curseurs donnait l'impression que les
        zones étaient liées entre elles. On cherche donc une place qui ne
        recouvre aucune zone manuelle existante.
        """
        bw, bh = self._NEW_ZONE_SIZE
        for cx, cy in self._NEW_ZONE_SPOTS:
            box = (cx, cy, bw, bh)
            if not any(self._overlaps(box, other) for other in self._manual):
                return box
        # Toutes les places prises : on décale en cascade plutôt que d'empiler.
        offset = 0.03 * (len(self._manual) % 8)
        return (0.25 + offset, 0.25 + offset, bw, bh)

    def _add_zone(self) -> None:
        """Le « + » : nouvelle zone à un endroit libre, prête à être réglée."""
        self._manual.append(self._free_spot())
        self._budget = (self._budget or 0) + 1
        self._selected = len(self._manual) - 1
        self._load_sliders(self._manual[self._selected])
        self._refresh(highlight=self._selected + 1)

    def _on_slider(self, _key: str) -> None:
        """Aperçu en direct : la zone en cours de réglage est redessinée."""
        for key, slider in self._sliders.items():
            self._slider_lbls[key].configure(text=f"{slider.get():.2f}")
        if self._selected is None:
            return          # rien de sélectionné : les curseurs ne visent rien
        box = self._current_box()
        preview_boxes = list(self._manual)
        if self._selected < len(preview_boxes):
            preview_boxes[self._selected] = box
            highlight = self._selected + 1
        else:
            # Zone automatique en cours de repositionnement : on la montre
            # comme si elle était déjà manuelle, ce qu'Appliquer confirmera.
            preview_boxes.append(box)
            highlight = len(preview_boxes)
        self._draw_with(preview_boxes, highlight)

    def _draw_with(self, manual_boxes: list[tuple[float, ...]], highlight: int) -> None:
        original = self._reference()
        if original is None or self._base is None:
            return
        patches = screen_match.build_patches(_downscale(original), exclude=self._excluded,
                                             manual=manual_boxes)
        if patches:
            self._show(screen_match.draw_preview(self._base, patches,
                                                  manual_count=len(manual_boxes),
                                                  highlight=highlight))

    def _apply_zone(self) -> None:
        if self._selected is None or self._selected >= len(self._patches):
            self._status_lbl.configure(text=t("PATCH_REVIEW_NO_ZONE_SELECTED"),
                                        text_color=COL_YELLOW)
            return
        box = self._current_box()
        if self._selected < len(self._manual):
            self._manual[self._selected] = box
        else:
            # Repositionner une zone automatique la fige : on écarte l'endroit
            # d'origine, sinon la recherche automatique la remettrait au même
            # endroit au prochain calcul et le déplacement serait annulé.
            self._excluded.append(screen_match.patch_box(self._patches[self._selected]))
            self._manual.append(box)
            self._selected = len(self._manual) - 1
        self._status_lbl.configure(text="", text_color=COL_TEXT_MUTED)
        self._refresh(highlight=self._selected + 1)

    def _delete_zone(self) -> None:
        if self._selected is None or self._selected >= len(self._patches):
            self._status_lbl.configure(text=t("PATCH_REVIEW_NO_ZONE_SELECTED"),
                                        text_color=COL_YELLOW)
            return
        if self._selected < len(self._manual):
            self._manual.pop(self._selected)
        else:
            self._excluded.append(screen_match.patch_box(self._patches[self._selected]))
        self._budget = max(0, (self._budget or 1) - 1)
        self._selected = None
        self._refresh()

    def _build_checks(self) -> None:
        for widget in self._checks_row.winfo_children():
            widget.destroy()
        self._checks = []
        for index in range(1, len(self._patches) + 1):
            var = ctk.BooleanVar(value=False)
            self._checks.append(var)
            ctk.CTkCheckBox(self._checks_row, text=str(index), variable=var, width=52,
                            font=font(12), fg_color=COL_ACCENT,
                            hover_color=COL_ACCENT_HOVER).pack(side="left", padx=4, pady=4)

    def _update_verdict(self, small_reference: np.ndarray) -> None:
        """Verdict croisé : les deux captures savent-elles se distinguer ?"""
        menu_path = self._menu_images[0] if self._menu_images else ""
        game_path = self._ingame_images[0] if self._ingame_images else ""
        if not menu_path or not game_path:
            self._verdict_lbl.configure(text=t("PATCH_REVIEW_NEED_BOTH"),
                                         text_color=COL_TEXT_MUTED)
            return

        menu_raw, game_raw = _imread_unicode(menu_path, cv2.IMREAD_COLOR), \
                             _imread_unicode(game_path, cv2.IMREAD_COLOR)
        if menu_raw is None or game_raw is None:
            self._verdict_lbl.configure(text=t("PATCH_REVIEW_UNREADABLE"), text_color=COL_RED)
            return

        menu_small, game_small = _downscale(menu_raw), _downscale(game_raw)
        sep = screen_match.cross_check(
            menu_small, self._patches_for(menu_path, menu_small),
            game_small, self._patches_for(game_path, game_small))

        key = "PATCH_REVIEW_SEPARATION_OK" if sep.ok else "PATCH_REVIEW_SEPARATION_BAD"
        self._verdict_lbl.configure(
            text=t(key, margin=f"{sep.margin:.2f}"),
            text_color=COL_GREEN if sep.ok else COL_RED)
        self._status_lbl.configure(text=t(
            "PATCH_REVIEW_SEPARATION_DETAIL",
            mm=f"{sep.menu_on_menu:.2f}", gg=f"{sep.game_on_game:.2f}",
            mg=f"{sep.menu_on_game:.2f}", gm=f"{sep.game_on_menu:.2f}"))

    def _patches_for(self, path: str, small: np.ndarray) -> list[screen_match.Patch]:
        """Fragments d'une référence : les réglages en cours pour l'image
        affichée, ceux déjà enregistrés pour l'autre."""
        if path == self._path:
            return screen_match.build_patches(small, exclude=self._excluded,
                                              manual=self._manual)
        return screen_match.build_patches(small, exclude=_REVIEWS.excluded_for(path),
                                          manual=_REVIEWS.manual_for(path))

    # -- Actions ------------------------------------------------------------ #

    def _recompute(self) -> None:
        """Écarte les fragments cochés et en choisit d'autres à leur place."""
        # On mémorise la BOÎTE du fragment, pas son centre : un fragment
        # fusionné couvre parfois une barre entière, et le refuser doit
        # écarter toute la zone, pas seulement la case du milieu.
        newly = [screen_match.patch_box(self._patches[i])
                 for i, var in enumerate(self._checks) if var.get()]
        if not newly:
            self._status_lbl.configure(text=t("PATCH_REVIEW_NOTHING_TICKED"),
                                        text_color=COL_YELLOW)
            return
        self._excluded.extend(newly)
        self._refresh()

    def _test_live(self) -> None:
        """Compare les fragments à l'écran RÉEL, jeu lancé."""
        raw = _capture_screen_bgr()
        if raw is None:
            self._status_lbl.configure(text=t("PATCH_REVIEW_TEST_NO_SCREEN"), text_color=COL_RED)
            return
        score = screen_match.score_patches(_downscale(raw), self._patches)
        self._status_lbl.configure(
            text=t("PATCH_REVIEW_TEST_RESULT", score=f"{score:.2f}"),
            text_color=COL_GREEN if score >= 0.8 else COL_YELLOW)

    def _validate(self) -> None:
        self._on_validated(list(self._excluded), list(self._manual), self._budget)
        self.destroy()



# ============================================================================
# COMPOSANT : SECTION D'ÉTAT (images de référence + scène OBS)
# ============================================================================
class StateSection(ctk.CTkFrame):
    """Un état de détection dans la fiche de jeu.

    Menu, En jeu et chaque état supplémentaire partagent ce bloc : liste
    d'images à laquelle on AJOUTE (l'ancien sélecteur remplaçait la liste
    entière à chaque clic), cadrage réglable image par image, et scène OBS.
    """

    NAME_MAX = 34          # troncature du nom de fichier affiché

    def __init__(self, master, modal: "GameModal", key: str, title: str,
                 images: Sequence[str], scene: str, scene_names: list[str],
                 editable_name: bool, state_id: str = "") -> None:
        super().__init__(master, fg_color=COL_CARD, corner_radius=10,
                         border_width=1, border_color=COL_BORDER)
        self._modal = modal
        self.key = key
        self.state_id = state_id
        self.images: list[str] = list(images)
        self.name_var = ctk.StringVar(value=title)
        self.scene_var = ctk.StringVar(value=scene)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        header.grid_columnconfigure(0, weight=1)
        if editable_name:
            ctk.CTkEntry(header, textvariable=self.name_var, height=30,
                         placeholder_text=t("GAME_MODAL_EXTRA_NAME"),
                         fg_color=COL_BG, border_color=COL_BORDER,
                         font=font(12, "bold")).grid(row=0, column=0, sticky="ew")
            ctk.CTkButton(header, text=t("GAME_MODAL_BTN_REMOVE_STATE"), width=90, height=30,
                          fg_color=COL_BG, hover_color=COL_RED, font=font(11),
                          border_width=1, border_color=COL_BORDER,
                          command=self._remove_self).grid(row=0, column=1, padx=(8, 0))
        else:
            ctk.CTkLabel(header, text=title, font=font(12, "bold"),
                         anchor="w").grid(row=0, column=0, sticky="w")

        self._rows_box = ctk.CTkFrame(self, fg_color="transparent")
        self._rows_box.grid(row=1, column=0, sticky="ew", padx=12)
        self._rows_box.grid_columnconfigure(0, weight=1)

        ctk.CTkButton(self, text=t("GAME_MODAL_BTN_ADD_IMAGES"), height=30,
                      fg_color=COL_BG, hover_color=COL_CARD_HOVER, font=font(11),
                      border_width=1, border_color=COL_BORDER,
                      command=self.add_images).grid(row=2, column=0, sticky="ew",
                                                    padx=12, pady=(6, 8))

        ctk.CTkLabel(self, text=t("GAME_MODAL_LABEL_SCENE"), font=font(11),
                     text_color=COL_TEXT_MUTED, anchor="w").grid(row=3, column=0,
                                                                 sticky="w", padx=12)
        scene_frame, _menu = bordered_menu(
            self, values=scene_names or [t("GAME_MODAL_SCENE_NOT_CONNECTED")],
            variable=self.scene_var, fg_color=COL_BG, button_color=COL_ACCENT,
            button_hover_color=COL_ACCENT_HOVER)
        scene_frame.grid(row=4, column=0, sticky="ew", padx=12, pady=(2, 12))
        self.refresh_rows()

    def name_now(self) -> str:
        return self.name_var.get().strip()

    # -- Images ------------------------------------------------------------- #

    def add_images(self) -> None:
        """AJOUTE des images à la liste. Les doublons sont ignorés : rechoisir
        un fichier déjà présent le ferait apparaître deux fois."""
        paths = filedialog.askopenfilenames(
            title=t("GAME_MODAL_FILEDIALOG_TITLE"),
            filetypes=[(t("GAME_MODAL_FILEDIALOG_FILTER_LABEL"), "*.png")])
        added = [path for path in paths if path not in self.images]
        if not added:
            return
        self.images.extend(added)
        self.refresh_rows()
        # Contrôle du cadrage sur la première image ajoutée, juste après le
        # choix : c'est le seul moment où l'utilisateur peut corriger sans être
        # interrompu en pleine partie.
        self._modal.open_patch_review(added[0], self)

    def remove_image(self, path: str) -> None:
        self.images = [kept for kept in self.images if kept != path]
        self.refresh_rows()

    def refresh_rows(self) -> None:
        """Redessine une ligne par image : nom, cadrage, retrait.

        Un simple compteur (« 3 image(s) ») ne disait pas LESQUELLES, et ne
        permettait ni d'en retirer une seule ni de régler son cadrage.
        """
        for child in self._rows_box.winfo_children():
            child.destroy()
        if not self.images:
            ctk.CTkLabel(self._rows_box, text=t("GAME_MODAL_IMAGES_NONE"), font=font(11),
                         text_color=COL_TEXT_MUTED, anchor="w").grid(row=0, column=0,
                                                                     sticky="w", pady=2)
            return
        for index, path in enumerate(self.images):
            row = ctk.CTkFrame(self._rows_box, fg_color="transparent")
            row.grid(row=index, column=0, sticky="ew", pady=2)
            row.grid_columnconfigure(0, weight=1)
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            if len(name) > self.NAME_MAX:
                name = name[:self.NAME_MAX - 1] + "\u2026"
            ctk.CTkLabel(row, text=name, font=font(11), text_color=COL_TEXT_MUTED,
                         anchor="w").grid(row=0, column=0, sticky="ew")
            stale = self._modal.review_is_stale(path)
            ctk.CTkButton(row, text=t("GAME_MODAL_BTN_REVIEW_STALE") if stale
                          else t("GAME_MODAL_BTN_REVIEW"),
                          width=140, height=26, font=font(11), fg_color=COL_BG,
                          hover_color=COL_CARD_HOVER, border_width=1,
                          border_color=COL_YELLOW if stale else COL_BORDER,
                          command=lambda p=path: self._modal.open_patch_review(p, self),
                          ).grid(row=0, column=1, padx=(8, 4))
            ctk.CTkButton(row, text="\u00d7", width=28, height=26, font=font(13),
                          fg_color=COL_BG, hover_color=COL_RED, border_width=1,
                          border_color=COL_BORDER,
                          command=lambda p=path: self.remove_image(p),
                          ).grid(row=0, column=2)

    def _remove_self(self) -> None:
        self._modal.remove_state_section(self)


# ============================================================================
# MODAL : AJOUTER / MODIFIER UN JEU
# ============================================================================
class GameModal(ctk.CTkToplevel):
    def __init__(self, master, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 on_saved: Callable[[], None], steam_candidates: list[dict[str, str]],
                 game: Optional[Game] = None) -> None:
        super().__init__(master)
        self.title(t("GAME_MODAL_TITLE_EDIT") if game else t("GAME_MODAL_TITLE_ADD"))
        self.geometry("480x640")
        self.configure(fg_color=COL_BG)
        self.transient(master)
        self.grab_set()

        self._store = store
        self._obs_loop = obs_loop
        self._get_obs_client = obs_client_getter
        self._on_saved = on_saved
        self._game = game
        self._steam_candidates = steam_candidates
        initial_menu = list(game.menu_images) if game else []
        initial_ingame = list(game.ingame_images) if game else []
        self._patch_reviews: dict[str, dict[str, Any]] = dict(game.patch_reviews) if game else {}
        self._sections: list[StateSection] = []

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=16)
        scroll.grid_columnconfigure(0, weight=1)

        # Compteur de lignes : les numéros de grille étaient codés en dur
        # (row=0..15), ce qui rendait toute insertion de champ risquée et
        # avait déjà conduit à empiler un label et son champ dans la MÊME
        # cellule, chevauchement rattrapé à coups de padding.
        self._row = 0

        # --- Source ---
        self.source_var = ctk.StringVar(value=(game.source if game else "manual"))
        source_row = ctk.CTkFrame(scroll, fg_color="transparent")
        source_row.grid(row=self._next_row(), column=0, sticky="ew", pady=(0, 10))
        ctk.CTkRadioButton(source_row, text=t("GAME_MODAL_SOURCE_STEAM"), variable=self.source_var,
                            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                            value="steam", command=self._toggle_source).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(source_row, text=t("GAME_MODAL_SOURCE_MANUAL"), variable=self.source_var,
                            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                            value="manual", command=self._toggle_source).pack(side="left")

        # --- Steam: dropdown des jeux détectés non encore ajoutés ---
        self.steam_var = ctk.StringVar()
        steam_names = [f"{g['name']} (appid {g['appid']})" for g in steam_candidates] or [t("GAME_MODAL_STEAM_NONE_DETECTED")]
        steam_frame, self.steam_menu = bordered_menu(
            scroll, values=steam_names, variable=self.steam_var, fg_color=COL_BG,
            button_color=COL_ACCENT, button_hover_color=COL_ACCENT_HOVER)
        steam_frame.grid(row=self._next_row(), column=0, sticky="ew", pady=(0, 10))

        # --- Manuel: nom + exe ---
        self.name_var = ctk.StringVar(value=game.name if game else "")
        self.exe_var = ctk.StringVar(value=game.active_match if (game and game.source == "manual") else "")
        self.name_entry = self._labeled_entry(scroll, t("GAME_MODAL_LABEL_NAME"), self.name_var)
        self.exe_entry = self._labeled_entry(scroll, t("GAME_MODAL_LABEL_EXE"), self.exe_var)

        # --- États de détection : Menu, En jeu, puis ceux de l'utilisateur ---
        self._scene_names = self._fetch_scene_names()
        self._states_box = ctk.CTkFrame(scroll, fg_color="transparent")
        self._states_box.grid(row=self._next_row(), column=0, sticky="ew", pady=(8, 0))

        self._add_section("menu", t("GAME_MODAL_LABEL_IMAGES_MENU"),
                          initial_menu, game.obs_scene_menu if game else "",
                          editable_name=False)
        self._add_section("in_game", t("GAME_MODAL_LABEL_IMAGES_INGAME"),
                          initial_ingame, game.obs_scene_ingame if game else "",
                          editable_name=False)
        for extra in (game.extra_states if game else []):
            self._add_section(f"{Game.EXTRA_PREFIX}{extra.get('id', '')}",
                              str(extra.get("name", "")),
                              [str(path) for path in (extra.get("images") or [])],
                              str(extra.get("scene", "")), editable_name=True,
                              state_id=str(extra.get("id", "")))

        ctk.CTkLabel(scroll, text=t("GAME_MODAL_EXTRA_HINT"), font=font(10),
                     text_color=COL_TEXT_MUTED, anchor="w", wraplength=420,
                     justify="left").grid(row=self._next_row(), column=0,
                                          sticky="w", pady=(8, 2))
        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_ADD_STATE"), height=32,
                       fg_color=COL_CARD, hover_color=COL_CARD_HOVER, font=font(12),
                       border_width=1, border_color=COL_BORDER,
                       command=self._add_extra_state).grid(row=self._next_row(), column=0,
                                                           sticky="ew", pady=(0, 6))

        # --- Création automatique des scènes dans OBS ---
        self.create_scenes_var = ctk.BooleanVar(value=False)
        self.create_scenes_cb = ctk.CTkCheckBox(
            scroll, text=t("GAME_MODAL_CHK_CREATE_SCENES"), variable=self.create_scenes_var,
            font=font(12), fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
            text_color=COL_TEXT_MUTED)
        self.create_scenes_cb.grid(row=self._next_row(), column=0, sticky="w", pady=(12, 2))
        self.create_scenes_hint = ctk.CTkLabel(
            scroll, text=t("GAME_MODAL_CHK_CREATE_SCENES_HINT"), font=font(10),
            text_color=COL_TEXT_MUTED, anchor="w", wraplength=420, justify="left")
        self.create_scenes_hint.grid(row=self._next_row(), column=0, sticky="w", pady=(0, 6))

        self.msg_lbl = ctk.CTkLabel(scroll, text="", font=font(11))
        self.msg_lbl.grid(row=self._next_row(), column=0, sticky="w", pady=(10, 0))

        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_SAVE"), height=38, fg_color=COL_ACCENT,
                       hover_color=COL_ACCENT_HOVER, text_color="#0F0C1B",
                       font=font(13, "bold"), command=self._save).grid(
            row=self._next_row(), column=0, sticky="ew", pady=(16, 0))

        self._toggle_source()

    def _next_row(self) -> int:
        row = self._row
        self._row += 1
        return row

    @staticmethod
    def _images_summary(paths: list[str]) -> str:
        return t("GAME_MODAL_IMAGES_COUNT", count=len(paths)) if paths else t("GAME_MODAL_IMAGES_NONE")

    def _labeled_entry(self, parent, label: str, var: ctk.StringVar) -> ctk.CTkEntry:
        """Le label et le champ occupent DEUX lignes distinctes.

        Avant, les deux étaient placés dans la même cellule (row identique,
        column=0) et ne se chevauchaient pas seulement grâce à un pady=(20,8)
        calibré à la main : n'importe quel changement de police, d'échelle DPI
        ou de langue faisait repasser le texte du label sous le champ.
        """
        ctk.CTkLabel(parent, text=label, font=font(12), text_color=COL_TEXT_MUTED,
                     anchor="w").grid(row=self._next_row(), column=0, sticky="w", pady=(6, 2))
        entry = ctk.CTkEntry(parent, textvariable=var, fg_color=COL_BG, border_color=COL_BORDER)
        entry.grid(row=self._next_row(), column=0, sticky="ew", pady=(0, 8))
        return entry

    def _fetch_scene_names(self) -> list[str]:
        client = self._get_obs_client()
        if client is None or not client.is_connected:
            return []
        try:
            future = self._obs_loop.run_coro(client.get_scene_list())
            return future.result(timeout=5)
        except Exception:
            logger.debug("Impossible de récupérer la liste des scènes OBS.", exc_info=True)
            return []

    def _toggle_source(self) -> None:
        """Active exactement le jeu de champs correspondant à la source.

        Avant, seul le menu Steam était grisé : en mode Steam les champs Nom
        et Exécutable restaient éditables alors que _save() les ignorait
        totalement — l'utilisateur pouvait saisir un nom qui était
        silencieusement jeté.
        """
        is_steam = self.source_var.get() == "steam"
        self.steam_menu.configure(state="normal" if is_steam else "disabled")
        for entry in (self.name_entry, self.exe_entry):
            entry.configure(state="disabled" if is_steam else "normal")

    # -- Sections d'état ----------------------------------------------------- #

    # Les images de Menu et En jeu vivent maintenant dans leur section. Ces deux
    # propriétés gardent le nom d'origine pour tout ce qui pilote le modal de
    # l'extérieur, et rafraîchissent l'affichage à l'écriture.
    @property
    def _menu_images(self) -> list[str]:
        return self._sections[0].images

    @_menu_images.setter
    def _menu_images(self, paths: Sequence[str]) -> None:
        self._sections[0].images = list(paths)
        self._sections[0].refresh_rows()

    @property
    def _ingame_images(self) -> list[str]:
        return self._sections[1].images

    @_ingame_images.setter
    def _ingame_images(self, paths: Sequence[str]) -> None:
        self._sections[1].images = list(paths)
        self._sections[1].refresh_rows()

    @property
    def scene_menu_var(self) -> ctk.StringVar:
        return self._sections[0].scene_var

    @property
    def scene_ingame_var(self) -> ctk.StringVar:
        return self._sections[1].scene_var

    def _add_section(self, key: str, title: str, images: Sequence[str], scene: str,
                     editable_name: bool, state_id: str = "") -> StateSection:
        section = StateSection(self._states_box, self, key=key, title=title, images=images,
                               scene=scene, scene_names=self._scene_names,
                               editable_name=editable_name, state_id=state_id)
        section.pack(fill="x", pady=(0, 10))
        self._sections.append(section)
        return section

    def _add_extra_state(self) -> StateSection:
        """Ajoute un état vide. L'id est tiré ici : c'est lui qui relie l'état à
        sa scène OBS et aux cadrages enregistrés, il doit exister avant même que
        l'utilisateur ait tapé un nom."""
        state_id = uuid.uuid4().hex
        return self._add_section(f"{Game.EXTRA_PREFIX}{state_id}",
                                 t("GAME_MODAL_EXTRA_DEFAULT_NAME"), [], "",
                                 editable_name=True, state_id=state_id)

    def remove_state_section(self, section: StateSection) -> None:
        if section not in self._sections[2:]:
            return          # Menu et En jeu ne se suppriment pas
        self._sections.remove(section)
        section.destroy()

    # -- Contrôle du cadrage automatique ------------------------------------ #

    def review_is_stale(self, path: str) -> bool:
        """L'image a-t-elle changé depuis la dernière validation ?

        C'est exactement le déclencheur demandé : on ne redemande rien tant que
        le fichier ne bouge pas dans le dossier, et on redemande dès qu'il bouge.
        """
        if not path:
            return False
        review = self._patch_reviews.get(path)
        return not review or review.get("stamp") != image_stamp(path)

    def open_patch_review(self, path: str, section: StateSection) -> None:
        """Ouvre le cadrage d'UNE image précise.

        Le contrôle ne portait que sur la première image de chaque état : les
        suivantes gardaient un cadrage que personne n'avait jamais vu.
        """
        if not path:
            return
        # Les enregistrements antérieurs ne stockaient qu'un centre (2 valeurs) ;
        # extract_patches accepte les deux formats.
        review = self._patch_reviews.get(path, {})

        def boxes(key: str) -> list[tuple[float, ...]]:
            return [tuple(float(v) for v in box) for box in (review.get(key) or [])]

        # Le verdict croisé compare l'état de CETTE image à tous les autres :
        # avec des états supplémentaires, « menu contre en jeu » ne suffit plus.
        others = [p for other in self._sections if other is not section
                  for p in other.images]
        stored = review.get("count")
        PatchReviewDialog(
            self, image_path=path, kind=section.key,
            menu_images=list(section.images), ingame_images=others,
            excluded=boxes("excluded"), manual=boxes("manual"),
            count=int(stored) if stored else None,
            on_validated=lambda ex, man, n, p=path: self._on_review_validated(p, ex, man, n))

    def _on_review_validated(self, path: str, excluded: list[tuple[float, ...]],
                             manual: list[tuple[float, ...]],
                             count: Optional[int]) -> None:
        self._patch_reviews[path] = {
            "stamp": image_stamp(path),
            "excluded": [list(box) for box in excluded],
            "manual": [list(box) for box in manual],
            # Le nombre de zones validé est enregistré pour que le scan utilise
            # EXACTEMENT ce qui a été montré dans l'aperçu.
            "count": count,
        }
        _REVIEWS.set(path, excluded, manual, count)
        for section in self._sections:
            section.refresh_rows()   # le bouton « à revalider » redevient neutre

    def _create_obs_scenes(self, name: str, active_match: str) -> Optional[tuple[str, str]]:
        """Crée les scènes du jeu dans OBS. Retourne (menu, en_jeu) ou None si
        OBS n'est pas joignable — dans ce cas le message d'erreur est affiché
        et l'enregistrement est interrompu pour ne pas écrire des noms de
        scènes qui n'existent pas."""
        client = self._get_obs_client()
        if client is None or not client.is_connected:
            self.msg_lbl.configure(text=t("GAME_MODAL_ERR_SCENES_NEED_OBS"), text_color=COL_RED)
            return None
        try:
            future = self._obs_loop.run_coro(client.setup_game_scenes(name, active_match))
            return future.result(timeout=15)
        except Exception as exc:
            logger.exception("Création des scènes OBS échouée.")
            self.msg_lbl.configure(text=t("GAME_MODAL_ERR_SCENES_FAILED", error=str(exc)[:60]),
                                   text_color=COL_RED)
            return None

    def _selected_scene(self, var: ctk.StringVar) -> str:
        """Scène retenue dans un menu déroulant, "" si aucune.

        Quand OBS n'est pas joignable, le menu ne contient qu'un libellé
        d'information : CTkOptionMenu le pousse dans la variable, et il finissait
        enregistré comme s'il s'agissait d'un vrai nom de scène.
        """
        value = var.get().strip()
        placeholders = {t("GAME_MODAL_SCENE_NOT_CONNECTED"), t("GAME_MODAL_STEAM_NONE_DETECTED")}
        return "" if value in placeholders else value

    def _save(self) -> None:
        editing = self._game is not None
        if self.source_var.get() == "steam":
            selection = self.steam_var.get()
            match = next((g for g in self._steam_candidates
                          if f"{g['name']} (appid {g['appid']})" == selection), None)
            if match is not None:
                name, source, active_match, appid = (match["name"], "steam",
                                                     match["install_dir"], match["appid"])
            elif editing:
                # Le menu Steam ne liste que les jeux DÉTECTÉS et pas encore
                # ajoutés : celui qu'on est en train d'éditer n'y figure donc
                # jamais. L'ancien code prenait alors le premier candidat de la
                # liste — l'identité du jeu (nom, appid, dossier) était
                # remplacée par celle d'un AUTRE jeu, et le scan Steam suivant
                # recréait l'original en doublon. Une édition ne change pas
                # l'identité : on la conserve telle quelle.
                name, source = self._game.name, self._game.source
                active_match, appid = self._game.active_match, self._game.appid
            else:
                self.msg_lbl.configure(text=t("GAME_MODAL_ERR_INVALID_STEAM_SELECTION"), text_color=COL_RED)
                return
        else:
            name = self.name_var.get().strip()
            exe = self.exe_var.get().strip()
            if not name or not exe:
                self.msg_lbl.configure(text=t("GAME_MODAL_ERR_MISSING_NAME_EXE"), text_color=COL_RED)
                return
            source, active_match, appid = "manual", exe, ""

        menu_section, ingame_section = self._sections[0], self._sections[1]
        scene_menu = self._selected_scene(menu_section.scene_var)
        scene_ingame = self._selected_scene(ingame_section.scene_var)

        if self.create_scenes_var.get():
            created = self._create_obs_scenes(name, active_match)
            if created is None:
                return  # message d'erreur déjà affiché
            # Le choix des menus déroulants PRIME. Créer les scènes ne doit pas
            # réécrire une sélection explicite : les noms fraîchement créés ne
            # servent qu'à remplir un champ resté vide.
            scene_menu = scene_menu or created[0]
            scene_ingame = scene_ingame or created[1]
            menu_section.scene_var.set(scene_menu)
            ingame_section.scene_var.set(scene_ingame)

        extra_states = [{
            "id": section.state_id,
            "name": section.name_now() or t("GAME_MODAL_EXTRA_DEFAULT_NAME"),
            "images": list(section.images),
            "scene": self._selected_scene(section.scene_var),
        } for section in self._sections[2:]]
        known_images = {path for section in self._sections for path in section.images}

        game = Game(
            id=self._game.id if self._game else uuid.uuid4().hex,
            name=name, source=source, active_match=active_match, appid=appid,
            menu_images=list(menu_section.images), ingame_images=list(ingame_section.images),
            obs_scene_menu=scene_menu, obs_scene_ingame=scene_ingame,
            extra_states=extra_states,
            # On ne garde que les validations des images encore référencées,
            # sinon games.json accumulerait indéfiniment des chemins morts.
            patch_reviews={path: review for path, review in self._patch_reviews.items()
                           if path in known_images},
        )
        if self._store.upsert(game):
            self._on_saved()
            self.destroy()
        else:
            self.msg_lbl.configure(text=t("GAME_MODAL_ERR_SAVE_FAILED"), text_color=COL_RED)
