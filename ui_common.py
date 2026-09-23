"""Palette, police, libellés d'état et glisser-déposer.

Briques partagées par toutes les vues. Importe app_paths AVANT customtkinter :
l'ordre est porteur, l'éveil DPI doit précéder le chargement de CTk.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Callable

from app_paths import enable_dpi_awareness, logger

enable_dpi_awareness()

import customtkinter as ctk  # noqa: E402  (voir enable_dpi_awareness ci-dessus)

from i18n import t  # noqa: E402


# ============================================================================
# PALETTE — thème AAA violet sombre (Steam/Discord/Spotify inspired)
# ============================================================================
# ============================================================================
# PALETTE — thème AAA violet sombre (Steam/Discord/Spotify inspired)
# ============================================================================
COL_BG = "#0F0C1B"
COL_BG_GRADIENT_TOP = "#151024"
COL_SIDEBAR = "#120E20"
COL_CARD = "#1A1530"
COL_CARD_HOVER = "#221B3D"
COL_BORDER = "#2A2145"
COL_BORDER_ACCENT = "#A855F7"
# Un cran plus clair que le liseré : #A855F7 en texte sur une carte tombait à
# 4,45:1, sous le seuil de lisibilité (4,5:1). #AF62F8 y fait 4,96:1.
COL_ACCENT = "#AF62F8"
COL_ACCENT_HOVER = "#9333EA"
COL_ACCENT_SOFT = "#7C3AED"
COL_TEXT = "#F3F0FA"
COL_TEXT_MUTED = "#9B93B5"
COL_GREEN = "#22C55E"
COL_YELLOW = "#F1C40F"
COL_RED = "#EF4444"

# --- État des cartes de jeu (actif / inactif) -------------------------------
# Le statut se lit sur DEUX signaux volontairement discrets plutôt qu'avec un
# gros rectangle rouge : le liseré de la carte et une pastille translucide
# incrustée dans la jaquette (voir ui_dashboard). Le rouge est réservé aux
# erreurs — un jeu qui ne tourne pas n'est pas une erreur, d'où le gris ardoise.
COL_RING_ACTIVE = "#3FBF87"     # liseré de la carte, jeu lancé
COL_RING_IDLE = "#342B4F"       # liseré de la carte, jeu arrêté
COL_DOT_ACTIVE = "#4ADE80"      # point de la pastille, jeu lancé
COL_DOT_IDLE = "#8E86A8"        # point de la pastille, jeu arrêté
COL_BADGE_FG = "#F3F0FA"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "Inter"


def font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def fit_to_screen(fenetre: Any, largeur: int, hauteur: int,
                  min_largeur: int, min_hauteur: int) -> None:
    """Taille de fenêtre en unités CustomTkinter, bornée à l'écran.

    CustomTkinter multiplie `geometry()` et `minsize()` par la mise à
    l'échelle de Windows, alors que `winfo_screen*()` rend des pixels
    physiques. Sans cette division, 1180x720 à 150 % donnait 1770x1080 :
    plus haut qu'un écran 1080p, et le bas de la fenêtre hors d'atteinte.
    """
    try:
        echelle = ctk.ScalingTracker.get_window_scaling(fenetre) or 1.0
    except Exception:
        echelle = 1.0
    # Marges : barre des tâches en bas, barre de titre de la fenêtre.
    max_l = int(fenetre.winfo_screenwidth() / echelle) - 40
    max_h = int(fenetre.winfo_screenheight() / echelle) - 110
    fenetre.minsize(min(min_largeur, max_l), min(min_hauteur, max_h))
    fenetre.geometry(f"{min(largeur, max_l)}x{min(hauteur, max_h)}")


# ============================================================================
# LIBELLÉS ET COULEURS D'ÉTAT DES CARTES DE JEU
# ============================================================================
def state_label(state: str) -> str:
    """Remplace l'ancien dict STATE_LABELS statique par un lookup i18n
    dynamique — recalculé à chaque appel donc valide après un changement
    de langue à chaud, avec mapping explicite pour éviter toute clé invalide."""
    mapping = {
        "inactive": "GAME_STATE_INACTIVE",
        "active": "GAME_STATE_ACTIVE",
        "menu": "GAME_STATE_MENU",
        "in_game": "GAME_STATE_IN_GAME",
    }
    return t(mapping.get(state, "GAME_STATE_INACTIVE"))


# Le texte de la pastille dit déjà l'état précis (Menu, En jeu, Actif) : la
# couleur ne distingue donc plus que « le jeu tourne » de « il ne tourne pas ».
def badge_text(state: str) -> str:
    """Libellé de la pastille : uniquement « Actif » ou « Inactif ».

    Les états fins (Menu, En jeu) restent calculés — c'est eux qui pilotent la
    bascule de scène OBS — mais ils n'apportent rien sur la carte : ce qu'on
    veut y lire d'un coup d'œil, c'est si le jeu tourne ou non. Le point est
    dessiné dans l'image de la pastille, pas collé au texte : un glyphe « ● »
    concaténé ici n'aurait ni la bonne taille ni la bonne couleur.
    """
    return state_label("inactive" if state == "inactive" else "active")


def is_running(state: str) -> bool:
    """Seule distinction visible sur une carte : le jeu tourne, ou non."""
    return state != "inactive"


STATE_RING = {True: COL_RING_ACTIVE, False: COL_RING_IDLE}
STATE_DOT = {True: COL_DOT_ACTIVE, False: COL_DOT_IDLE}


# ============================================================================
# GLISSER-DÉPOSER (optionnel)
# ============================================================================
# ============================================================================
# GLISSER-DÉPOSER (optionnel)
# ============================================================================
def parse_dropped_files(raw: str) -> list[str]:
    """Découpe la liste de chemins livrée par tkdnd.

    Tcl renvoie une liste : les chemins contenant un espace sont entourés
    d'accolades, ex. "{C:/mon dossier/a.png} C:/b.png".
    """
    paths = re.findall(r"\{([^}]*)\}|(\S+)", str(raw or ""))
    return [a or b for a, b in paths if (a or b)]


def _try_enable_dnd(widget: Any, on_files: Callable[[list[str]], None]) -> bool:
    """Active le glisser-déposer de fichiers sur un widget ET sa descendance.

    Deux pièges, tous deux vécus :

    1. `tkinterdnd2` doit être installé — sinon la zone ne fait rien d'autre
       qu'ouvrir le sélecteur de fichier. Il est désormais dans
       requirements.txt, mais son absence reste non fatale.
    2. Les cibles de dépôt de tkdnd sont enregistrées PAR FENÊTRE et ne
       remontent PAS au parent. Un CTkButton est en réalité un cadre qui
       contient un canvas et un label : n'enregistrer que le cadre laissait le
       curseur survoler un enfant non enregistré, et Windows refusait le
       dépôt — la zone semblait morte. On enregistre donc tout le sous-arbre.
    """
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError:
        logger.info("tkinterdnd2 absent — glisser-déposer désactivé, "
                    "le bouton Parcourir reste disponible.")
        return False

    def _on_drop(event: Any) -> None:
        files = parse_dropped_files(getattr(event, "data", ""))
        if files:
            on_files(files)

    try:
        # tkinterdnd2 exige que la racine Tk connaisse l'extension Tcl ;
        # _require() l'y charge après coup, ce qui évite de remplacer la
        # classe racine (ctk.CTk) par TkinterDnD.Tk.
        TkinterDnD._require(widget.winfo_toplevel())
    except Exception:
        logger.debug("Chargement de l'extension tkdnd impossible.", exc_info=True)
        return False

    registered = 0

    def _register(target: Any) -> None:
        nonlocal registered
        try:
            target.drop_target_register(DND_FILES)
            target.dnd_bind("<<Drop>>", _on_drop)
            registered += 1
        except Exception:
            logger.debug("Cible de dépôt refusée sur %r.", target, exc_info=True)
        for child in target.winfo_children():
            _register(child)

    _register(widget)
    return registered > 0
