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
COL_ACCENT = "#A855F7"
COL_ACCENT_HOVER = "#9333EA"
COL_ACCENT_SOFT = "#7C3AED"
COL_TEXT = "#F3F0FA"
COL_TEXT_MUTED = "#9B93B5"
COL_GREEN = "#22C55E"
COL_YELLOW = "#F1C40F"
COL_RED = "#EF4444"
# Pastille d'état des cartes de jeu : deux couleurs pleines, texte et contour
# en blanc. Fond plein (et non teinte sombre + texte coloré) pour que la
# pastille reste lisible par-dessus n'importe quelle jaquette.
COL_BADGE_BG_INACTIVE = "#D93025"
COL_BADGE_BG_ACTIVE = "#508267"
COL_BADGE_FG = "#FFFFFF"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "Inter"


def font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


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
# STATE_COLORS n'existe plus : texte et contour sont blancs quel que soit l'état.
def badge_text(state: str) -> str:
    """Libellé de la pastille : uniquement « Actif » ou « Inactif ».

    Les états fins (Menu, En jeu) restent calculés — c'est eux qui pilotent la
    bascule de scène OBS — mais ils n'apportent rien sur la carte : ce qu'on
    veut y lire d'un coup d'œil, c'est si le jeu tourne ou non.
    """
    label = state_label("inactive" if state == "inactive" else "active")
    return f"\u25cf {label}"


STATE_BADGE_BG = {
    "inactive": COL_BADGE_BG_INACTIVE, "active": COL_BADGE_BG_ACTIVE,
    "menu": COL_BADGE_BG_ACTIVE, "in_game": COL_BADGE_BG_ACTIVE,
}


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
