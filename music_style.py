"""
music_style.py — Apparence de l'overlay, réglée par lecteur.

Chaque source du widget musique — les huit lecteurs livrés d'avance comme
celles découvertes à l'exécution — a ses propres réglages : couleur de fond,
couleur et présence du contour, opacité, template appliqué, et éventuellement
une image de fond dessinée par l'utilisateur.

Les réglages vivent dans le même fichier que le jeton, sous la clé `styles`.
Une source jamais personnalisée n'y figure pas : elle prend le style par
défaut, donc le fichier reste petit et lisible.

Ce module dessine aussi ce qu'il décrit : `preview_png()` rend une vignette du
style, et `layout_guide_png()` le gabarit coté que l'utilisateur récupère pour
dessiner son propre fond. Les deux sortent de Pillow, déjà présent — aucune
image d'aperçu n'a donc à être livrée, ni regénérée à la main quand un
template change de couleurs.
"""
from __future__ import annotations

import io
import json
import re
import threading

from itertools import product

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Optional

from app_paths import DATA_DIR, logger
from music_catalog import is_valid_key

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:                             # pragma: no cover
    Image = None                              # type: ignore[assignment]
    ImageDraw = None                          # type: ignore[assignment]
    ImageFont = None                          # type: ignore[assignment]

#: Fonds dessinés par l'utilisateur, une image par clé de lecteur.
BACKGROUNDS_DIR = DATA_DIR / "music_backgrounds"

#: Taille de la carte d'overlay servant de référence au gabarit et aux
#: aperçus. Ce n'est pas une contrainte pour OBS — la source se redimensionne —
#: mais un dessin fait à ces proportions tombe juste.
CARD_W, CARD_H = 560, 180

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


#: Géométrie de la carte d'overlay, en pixels.
#:
#: UNE seule source pour deux usages : le CSS de la page est engendré à partir
#: de ces valeurs (`css_variables()`), et la taille de source annoncée à
#: l'utilisateur en est calculée (`overlay_size()`). Les deux ne peuvent donc
#: pas diverger — un chiffre affiché qui ne correspondrait plus à la page
#: serait pire que pas de chiffre du tout.
GEOMETRIE = {
    "pad_y": 18, "pad_l": 18, "pad_r": 26, "gap": 22,
    "cover": 132, "cover_wide": 210,
    # Les maximums de texte sont de VRAIS plafonds en CSS : c'est ce qui rend
    # la taille annoncée garantie, quelle que soit la longueur du titre.
    "title_max": 460, "title_size": 25, "title_line": 30,
    "artist_max": 330, "line_gap": 8, "line_h": 26,
    "wave_h": 26, "wave_gap": 12,
    "bloc_pad": 12, "bloc_gap": 10, "bloc_wave": 160,
    "minimal_pad_y": 12, "minimal_pad_x": 20, "minimal_title": 20,
    "bandeau_pad_x": 30, "bandeau_wave_h": 30, "bandeau_wave_gap": 14,
    "galerie_gap": 10,
    # Briques optionnelles : place qu'elles ajoutent a la carte.
    "dots_h": 26, "progress_h": 43, "controls_w": 132,
    # Marge de securite ajoutee a chaque dimension annoncee. Elle couvre la
    # bordure de la carte, les ombres portees de la pochette — qui debordent
    # du cadre — et les arrondis du moteur de rendu. Mesuree : sans elle, la
    # disposition Galerie depassait de 16 px le calcul theorique.
    "ombre": 34,
}


def css_variables() -> str:
    """Le bloc `:root` que la page consomme. Engendré, jamais recopié."""
    saut = chr(10)
    lignes = [f"    --{nom.replace('_', '-')}: {valeur}px;"
              for nom, valeur in GEOMETRIE.items()]
    return ":root{" + saut + saut.join(lignes) + saut + "  }"


@dataclass(frozen=True)
class Option:
    """Un choix de forme, présenté par sa vignette dans la fenêtre Widget."""

    key: str
    label: str
    hint: str = ""


#: Dispositions d'ensemble. Chacune répond à une place différente sur la scène :
#: une carte posée dans un coin, un bandeau pleine largeur, une colonne étroite.
LAYOUTS: tuple[Option, ...] = (
    Option("compact", "Compact", "Pochette à gauche, texte à droite"),
    Option("blocs", "Blocs", "Trois cases séparées"),
    Option("galerie", "Galerie", "Pochette au-dessus, texte dessous"),
    Option("minimal", "Minimal", "Une seule ligne, sans pochette"),
    Option("bandeau", "Bandeau", "Pleine largeur, onde sur toute la base"),
)

#: Formes de pochette. `auto` garde le comportement qui suit l'image reçue :
#: disque pour une pochette carrée, rectangle pour une miniature de vidéo.
COVERS: tuple[Option, ...] = (
    Option("auto", "Auto", "Vinyle si carré, rectangle si vidéo"),
    Option("vinyle", "Vinyle", "Disque qui tourne"),
    Option("carre", "Carré", "Pochette telle quelle"),
    Option("large", "Large", "Recadrée en 16:9"),
    Option("aucune", "Aucune", "Pas d'image"),
)

LAYOUT_KEYS = frozenset(opt.key for opt in LAYOUTS)
COVER_KEYS = frozenset(opt.key for opt in COVERS)


def _clean_color(value: Any, fallback: str) -> str:
    """Une couleur invalide finirait telle quelle dans du CSS."""
    return value if isinstance(value, str) and _HEX.match(value) else fallback


@dataclass(frozen=True)
class Style:
    """L'apparence d'un overlay. Les valeurs par défaut sont le thème violet."""

    bg: str = "#0F0C1B"
    border: str = "#A855F7"
    border_on: bool = True
    #: Contour en tirets plutôt qu'un trait plein.
    border_dashed: bool = False
    #: Couleur des barres de la forme d'onde. Par défaut celle du contour —
    #: voir `sanitised()` : un accent qui reste violet sur un preset brun
    #: jurerait, alors que suivre le contour tombe juste dans presque tous
    #: les cas, et reste réglable quand ce n'est pas le cas.
    accent: str = ""
    #: Opacité du FOND, en pourcentage. Le texte et la pochette restent
    #: opaques : les rendre transparents les rendrait illisibles sur une scène
    #: claire, ce qui n'est jamais ce qu'on cherche.
    opacity: int = 82

    # -- Briques ---------------------------------------------------------- #
    # Le widget est découpé en morceaux indépendants : la disposition
    # d'ensemble, la forme de la pochette, et ce qu'on garde ou non. Chacun se
    # règle séparément, parce que « vinyle qui tourne » et « barre fine en bas
    # de l'écran » ne sont pas le même besoin.

    #: Disposition d'ensemble, parmi `LAYOUTS`.
    layout: str = "compact"
    #: Forme de la pochette, parmi `COVERS`.
    cover: str = "auto"
    #: Forme d'onde sous le texte.
    show_wave: bool = True
    #: Nom de l'artiste.
    show_artist: bool = True
    #: Pastille de l'application (SPOTIFY, DEEZER…).
    show_app: bool = True
    #: Pastilles de fenêtre façon macOS, en haut à gauche. Purement décoratif.
    show_dots: bool = False
    #: Barre de progression et temps écoulé / total. Vraies données SMTC.
    show_progress: bool = False
    #: Boutons de transport. DÉCORATIFS : une source navigateur OBS n'est pas
    #: cliquable à l'écran. Ils montrent l'état réel, ils ne commandent rien.
    show_controls: bool = False

    template: str = "violet"
    background_image: bool = False

    def sanitised(self) -> "Style":
        """Version sûre à sérialiser et à injecter dans une page.

        Le fichier de réglages est éditable à la main : rien ne garantit qu'il
        contienne encore des couleurs valides ni une opacité dans les bornes.
        """
        opacite = self.opacity if isinstance(self.opacity, (int, float)) else 82
        contour = _clean_color(self.border, "#A855F7")
        return replace(
            self,
            bg=_clean_color(self.bg, "#0F0C1B"),
            border=contour,
            border_on=bool(self.border_on),
            border_dashed=bool(self.border_dashed),
            accent=_clean_color(self.accent, contour),
            opacity=max(0, min(100, int(opacite))),
            layout=self.layout if self.layout in LAYOUT_KEYS else "compact",
            cover=self.cover if self.cover in COVER_KEYS else "auto",
            show_wave=bool(self.show_wave),
            show_artist=bool(self.show_artist),
            show_app=bool(self.show_app),
            show_dots=bool(self.show_dots),
            show_progress=bool(self.show_progress),
            show_controls=bool(self.show_controls),
            template=self.template if isinstance(self.template, str) else "violet",
            background_image=bool(self.background_image),
        )

    def as_dict(self) -> dict[str, Any]:
        """Le style, plus les couleurs de texte qu'il impose.

        L'encre est DÉDUITE de la luminance du fond, jamais fixée en dur : la
        page écrivait son titre en blanc quoi qu'il arrive, donc un preset à
        fond clair donnait du blanc sur blanc.

        Les champs ajoutés ici ne font pas partie de la dataclasse : la
        relecture les ignore (voir `StyleStore.get`), ils ne servent qu'à la
        page.
        """
        donnees = asdict(self.sanitised())
        clair = is_light(self)
        donnees["ink"] = "#1C1C1E" if clair else "#F3F0FA"
        donnees["muted"] = "#5F5A66" if clair else "#A39BBD"
        return donnees

    @property
    def rgba(self) -> tuple[int, int, int, int]:
        """Fond en RVBA, opacité comprise — pour Pillow comme pour le CSS."""
        couleur = _clean_color(self.bg, "#0F0C1B")
        r, g, b = (int(couleur[i:i + 2], 16) for i in (1, 3, 5))
        return r, g, b, round(max(0, min(100, self.opacity)) * 255 / 100)


@dataclass(frozen=True)
class Template:
    """Un style prêt à l'emploi, présenté avec son aperçu dans la fenêtre."""

    key: str
    label: str
    style: Style
    #: Vrai quand la DISPOSITION fait partie du modèle, pas seulement son
    #: habillage. Les presets macOS sont dans ce cas : leur identité tient à
    #: l'agencement autant qu'aux couleurs. Les autres laissent l'utilisateur
    #: garder sa mise en page.
    full: bool = False


#: Les templates livrés. En ajouter un ne demande qu'une ligne ici : son aperçu
#: se dessine à partir de ses propres valeurs, il n'y a pas d'image à fournir
#: ni à refaire quand les couleurs changent.
TEMPLATES: tuple[Template, ...] = (
    Template("violet", "Violet", Style(template="violet")),
    Template("neon", "Cassette néon",
             Style(bg="#150A21", border="#FF3CAC", opacity=88, template="neon")),
    Template("ardoise", "Ardoise",
             Style(bg="#1A1530", border="#2A2145", opacity=94, template="ardoise")),
    Template("clair", "Clair",
             Style(bg="#F3F0FA", border="#1A1530", opacity=92, template="clair")),
    Template("nu", "Sans cadre",
             Style(bg="#000000", border="#000000", border_on=False, opacity=0,
                   template="nu")),
    # Brun café, contour en tirets, accent orange — hors de la palette violette
    # du reste de l'application, c'est voulu : ce preset vit sur la scène OBS,
    # pas dans l'interface.
    Template("mocha", "Mocha",
             Style(bg="#2B1D16", border="#C98B5E", border_dashed=True,
                   accent="#FF8A3D", opacity=92, template="mocha")),
    # Les deux presets façon lecteur macOS. Ils portent leur disposition :
    # pastilles de fenêtre, pochette carrée, boutons de transport et barre de
    # progression font partie du dessin, pas de l'habillage.
    Template("macos_sombre", "macOS sombre",
             Style(bg="#1C1C1E", border="#3A3A3C", accent="#F2F2F7",
                   opacity=100, layout="compact", cover="carre",
                   show_wave=False, show_app=False, show_dots=True,
                   show_progress=True, show_controls=True,
                   template="macos_sombre"), full=True),
    Template("macos_clair", "macOS clair",
             Style(bg="#F1F1F3", border="#D6D6DA", accent="#1C1C1E",
                   opacity=100, layout="compact", cover="carre",
                   show_wave=False, show_app=False, show_dots=True,
                   show_progress=True, show_controls=True,
                   template="macos_clair"), full=True),
)

_BY_KEY = {tpl.key: tpl for tpl in TEMPLATES}


def template(key: str) -> Optional[Template]:
    return _BY_KEY.get(key)


def is_light(style: Style) -> bool:
    """Le fond est-il clair ? Décide de la couleur du texte de l'aperçu."""
    r, g, b, _ = style.rgba
    return (r * 299 + g * 587 + b * 114) / 1000 > 140


class StyleStore:
    """Les réglages d'apparence, par clé de lecteur.

    Partage le fichier du jeton : deux fichiers pour une fonctionnalité qui se
    supprime d'un bloc n'auraient pas de sens, et ces réglages n'existent que
    pour ces sources.
    """

    def __init__(self, path: Path, backgrounds: Optional[Path] = None) -> None:
        self._path = path
        self._backgrounds = backgrounds or BACKGROUNDS_DIR
        # UN verrou pour tout le fichier, jeton compris : le jeton et les
        # styles s'écrivaient par deux chemins différents, chacun avec son
        # verrou, et le dernier arrivé écrasait l'autre.
        self._lock = threading.Lock()

    # -- lecture ----------------------------------------------------------- #

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _read_for_update(self) -> dict[str, Any]:
        """Contenu à modifier puis réécrire. Appelée sous `self._lock`.

        Un fichier ILLISIBLE n'est pas un fichier vide. Le réécrire tel quel
        effaçait tous les styles, et surtout le jeton : chaque lien d'overlay
        déjà collé dans OBS cessait de répondre, sans rien qui l'explique.
        Le fichier abîmé est donc mis de côté, et le jeton en est extrait
        s'il est encore lisible — une écriture interrompue laisse
        généralement le début du document intact, et le jeton y est en tête.
        """
        try:
            texte = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        # Toute autre OSError remonte : un fichier qu'on ne peut pas lire ne
        # doit pas être écrasé à l'aveugle.
        try:
            data = json.loads(texte)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

        copie = self._path.with_name(self._path.name + ".corrompu")
        try:
            copie.write_text(texte, encoding="utf-8")
        except OSError:
            logger.warning("Copie du fichier abîmé impossible (%s).", copie)
        logger.warning("Fichier du widget musique illisible, mis de côté dans "
                       "%s. Les styles repartent de zéro.", copie.name)
        trouve = re.search(r'"overlay_token"\s*:\s*"([A-Za-z0-9_-]{16,})"', texte)
        return {"overlay_token": trouve.group(1)} if trouve else {}

    def _write(self, data: dict[str, Any]) -> None:
        """Écriture atomique : un fichier temporaire, puis un remplacement.

        `write_text` directement sur le fichier le tronquait avant d'écrire :
        un plantage ou une coupure à ce moment laissait un document à moitié
        vide, c'est-à-dire la situation que `_read_for_update` doit réparer.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    # -- jeton ------------------------------------------------------------- #

    def ensure_token(self, fabriquer) -> str:
        """Le jeton persisté, créé par `fabriquer()` s'il n'y en a pas.

        Écrit dans le document existant, jamais à sa place : c'est ce qui
        garde les styles déjà enregistrés. Lève `OSError` si le jeton neuf ne
        peut pas être persisté — l'appelant doit alors prévenir que le lien
        changera au prochain démarrage.
        """
        with self._lock:
            data = self._read_for_update()
            jeton = data.get("overlay_token")
            if isinstance(jeton, str) and jeton:
                return jeton
            jeton = fabriquer()
            data["overlay_token"] = jeton
            self._write(data)
            return jeton

    def get(self, key: str) -> Style:
        """Réglages d'un lecteur, ou le style par défaut s'il n'en a pas.

        `background_image` ne vient pas du fichier mais de la présence réelle
        de l'image : supprimer le PNG à la main doit suffire à revenir au fond
        uni, sans laisser un réglage qui prétend le contraire.
        """
        brut = self._read().get("styles", {})
        valeurs = brut.get(key) if isinstance(brut, dict) else None
        if not isinstance(valeurs, dict):
            return replace(Style(),
                           background_image=self.background_path(key) is not None)
        connus = {champ: valeurs[champ] for champ in Style.__dataclass_fields__
                  if champ in valeurs}
        try:
            style = Style(**connus)
        except TypeError:
            style = Style()
        return replace(style.sanitised(),
                       background_image=self.background_path(key) is not None)

    def all_keys(self) -> list[str]:
        brut = self._read().get("styles", {})
        return sorted(brut) if isinstance(brut, dict) else []

    # -- écriture ---------------------------------------------------------- #

    def save(self, key: str, style: Style) -> None:
        """Écrit les réglages d'un lecteur sans toucher au reste du fichier.

        Le jeton vit dans le même document : relire, modifier une branche puis
        réécrire évite qu'un enregistrement de style l'efface — ce qui
        casserait toutes les sources navigateur déjà configurées dans OBS.
        """
        with self._lock:
            try:
                data = self._read_for_update()
                styles = data.get("styles")
                if not isinstance(styles, dict):
                    styles = {}
                styles[key] = style.as_dict()
                data["styles"] = styles
                self._write(data)
            except OSError:
                logger.warning("Réglages du widget musique non enregistrés (%s).",
                               self._path)

    def reset(self, key: str) -> None:
        with self._lock:
            try:
                data = self._read_for_update()
                styles = data.get("styles")
                if isinstance(styles, dict) and styles.pop(key, None) is not None:
                    data["styles"] = styles
                    self._write(data)
            except OSError:
                logger.warning("Réinitialisation non enregistrée (%s).",
                               self._path)

    # -- image de fond ----------------------------------------------------- #

    def background_path(self, key: str) -> Optional[Path]:
        # La clé peut venir d'une URL (`/musicbg/<jeton>?source=`) : sans ce
        # contrôle, `?source=C:/…/photo` désignait n'importe quel PNG du
        # disque, et la route le servait.
        if not is_valid_key(key):
            return None
        chemin = self._backgrounds / f"{key}.png"
        try:
            return chemin if chemin.is_file() else None
        except OSError:
            return None

    def set_background(self, key: str, source: Path) -> bool:
        """Recopie l'image choisie dans les données de l'application.

        Copier plutôt que pointer vers le fichier d'origine : un dessin rangé
        sur le bureau finit déplacé ou supprimé, et l'overlay tomberait en
        panne pendant une diffusion sans que rien ne l'explique.
        """
        if Image is None or not is_valid_key(key):
            return False
        try:
            self._backgrounds.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as brut:
                brut.convert("RGBA").save(self._backgrounds / f"{key}.png")
            return True
        except Exception:
            logger.exception("Image de fond refusée : %s", source)
            return False

    def clear_background(self, key: str) -> None:
        chemin = self.background_path(key)
        if chemin is not None:
            try:
                chemin.unlink()
            except OSError:
                logger.warning("Image de fond non supprimée : %s", chemin)

    def background_bytes(self, key: str) -> Optional[bytes]:
        chemin = self.background_path(key)
        if chemin is None:
            return None
        try:
            return chemin.read_bytes()
        except OSError:
            return None


# ----------------------------------------------------------------------------
# Rendus
# ----------------------------------------------------------------------------
def _pointille(dessin, largeur: int, hauteur: int, rayon: int,
               couleur: str, tiret: int = 7, trou: int = 5,
               epaisseur: int = 2) -> None:
    """Contour en tirets, dessiné segment par segment.

    Pillow ne sait pas tracer un trait pointillé : `rounded_rectangle` n'a pas
    d'équivalent de `border-style: dashed`. On parcourt donc les quatre côtés
    en alternant tiret et trou, en s'arrêtant avant les coins arrondis.
    """
    pas = tiret + trou
    for x in range(rayon, largeur - rayon, pas):
        fin = min(x + tiret, largeur - rayon)
        dessin.line([x, 1, fin, 1], fill=couleur, width=epaisseur)
        dessin.line([x, hauteur - epaisseur, fin, hauteur - epaisseur],
                    fill=couleur, width=epaisseur)
    for y in range(rayon, hauteur - rayon, pas):
        fin = min(y + tiret, hauteur - rayon)
        dessin.line([1, y, 1, fin], fill=couleur, width=epaisseur)
        dessin.line([largeur - epaisseur, y, largeur - epaisseur, fin],
                    fill=couleur, width=epaisseur)


def _damier(largeur: int, hauteur: int, case: int = 10):
    """Fond en damier, pour que l'opacité se VOIE dans un aperçu.

    Sur un aplat, un fond à 40 % et un fond à 90 % se ressemblent ; sur un
    damier, on lit tout de suite ce que la scène OBS laissera passer.
    """
    fond = Image.new("RGBA", (largeur, hauteur), (46, 40, 66, 255))
    dessin = ImageDraw.Draw(fond)
    for y in range(0, hauteur, case):
        for x in range(0, largeur, case):
            if (x // case + y // case) % 2:
                dessin.rectangle([x, y, x + case - 1, y + case - 1],
                                 fill=(60, 53, 84, 255))
    return fond


#: Contenu d'exemple des vignettes. Montrer le rendu FINAL, pas un schéma :
#: c'est le seul moyen de choisir une disposition sans l'appliquer d'abord.
SAMPLE_TITLE = "Nom de la musique"
SAMPLE_ARTIST = "Auteur"
SAMPLE_APP = "APPLI"

#: Marque de troncature, quand un titre ne tient pas dans la vignette.
POINTS = "…"

#: Hauteurs de barres figées, pour que deux vignettes se comparent. Une forme
#: d'onde tirée au hasard changerait à chaque rendu et brouillerait la
#: comparaison entre deux dispositions.
_SAMPLE_WAVE = (0.25, 0.5, 0.8, 0.45, 0.95, 0.6, 0.35, 0.75, 0.55, 0.9,
                0.4, 0.7, 0.3, 0.85, 0.5, 0.65, 0.45, 0.8, 0.35, 0.6)

#: Facteur de suréchantillonnage. Pillow ne lisse ni les cercles ni les coins
#: arrondis : dessiner à quatre fois la taille puis réduire en Lanczos donne
#: l'anticrénelage qui manque, et la vignette cesse de sortir en escaliers.
#: C'est aussi ce qui la garde nette quand Windows affiche à 125 % ou 150 %.
_SS = 4


def _police(taille: float):
    """Police d'écriture des vignettes, avec repli progressif.

    Le nom de fichier suffit sous Windows ; ailleurs — et si la police manque —
    on retombe sur celle de Pillow plutôt que de rendre une vignette muette.
    """
    for nom in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(nom, int(taille))
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=int(taille))
    except Exception:
        return ImageFont.load_default()


def _police_grasse(taille: float):
    for nom in ("segoeuisb.ttf", "segoeuib.ttf", "arialbd.ttf",
                "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(nom, int(taille))
        except Exception:
            continue
    return _police(taille)


def _tronque(dessin, texte: str, police, largeur: float) -> str:
    """Coupe le texte pour qu'il tienne, avec des points de suspension.

    Sans cela, un titre trop long sortirait du cadre : la vignette montrerait
    un rendu que l'overlay ne produit jamais, puisque lui coupe le texte en CSS.
    """
    if dessin.textlength(texte, font=police) <= largeur:
        return texte
    for taille in range(len(texte) - 1, 0, -1):
        court = texte[:taille].rstrip() + POINTS
        if dessin.textlength(court, font=police) <= largeur:
            return court
    return ""


# ----------------------------------------------------------------------------
# Zones
# ----------------------------------------------------------------------------
#: Ce que chaque disposition affiche, et comment.
#:
#: La même table décrit la vignette ET les zones de la grille CSS de la page.
#: C'est le point important : l'aperçu et l'overlay ne peuvent plus diverger.
#: Un bloc « forme d'onde » vu dans l'aperçu existe donc aussi dans le widget,
#: et réciproquement — ce qui n'était pas le cas quand les deux rendus étaient
#: écrits séparément.
LAYOUT_ZONES = {
    "compact": {"colonnes": False, "cadres": False, "pochette": True},
    "blocs": {"colonnes": True, "cadres": True, "pochette": True},
    "galerie": {"colonnes": False, "cadres": False, "pochette": True,
                "vertical": True},
    "minimal": {"colonnes": False, "cadres": False, "pochette": False},
    "bandeau": {"colonnes": False, "cadres": False, "pochette": True,
                "onde_pleine": True},
}


def overlay_size(style: "Style") -> tuple[int, int]:
    """Taille de source navigateur couvrant CETTE combinaison, en pixels.

    C'est un maximum garanti, pas une estimation : la largeur du texte est
    plafonnée en CSS, donc aucun titre ne peut faire déborder la carte.

    Trop grand ne coûte rien — le fond est transparent et la carte est calée
    en haut à gauche. Trop petit rogne. Le chiffre annoncé est donc majoré.
    """
    g = GEOMETRIE
    style = style.sanitised()
    zones = zones_visibles(style)
    pochette = "pochette" in zones
    onde = "onde" in zones

    if not pochette:
        largeur_p = hauteur_p = 0
    elif style.cover == "large":
        largeur_p = g["cover_wide"]
        hauteur_p = round(largeur_p * 9 / 16)
    else:
        largeur_p = hauteur_p = g["cover"]

    if style.layout == "minimal":
        pad_y = g["minimal_pad_y"]
        pad_l = pad_r = g["minimal_pad_x"]
        titre_h = round(g["minimal_title"] * 1.2)
        onde_h, onde_gap = g["wave_h"], g["wave_gap"]
        largeur_p = hauteur_p = 0
        pochette = False
    elif style.layout == "bandeau":
        pad_y = g["pad_y"]
        pad_l = pad_r = g["bandeau_pad_x"]
        titre_h = g["title_line"]
        onde_h, onde_gap = g["bandeau_wave_h"], g["bandeau_wave_gap"]
    else:
        pad_y, pad_l, pad_r = g["pad_y"], g["pad_l"], g["pad_r"]
        titre_h = g["title_line"]
        onde_h, onde_gap = g["wave_h"], g["wave_gap"]

    montre_ligne = style.show_artist or style.show_app
    texte_h = titre_h + ((g["line_gap"] + g["line_h"]) if montre_ligne else 0)
    pile_h = texte_h + ((onde_gap + onde_h) if onde else 0)
    if style.show_progress:
        pile_h += g["progress_h"]
    texte_w = g["title_max"]

    if style.layout == "galerie":
        largeur = pad_l + max(largeur_p, texte_w) + pad_r
        hauteur = pad_y * 2 + pile_h
        if pochette:
            hauteur += hauteur_p + g["galerie_gap"]
    elif style.layout == "blocs":
        cases = ([largeur_p] if pochette else []) + [texte_w]
        if onde:
            cases.append(g["bloc_wave"])
        cases = [case + 2 * g["bloc_pad"] for case in cases]
        largeur = pad_l + sum(cases) + g["bloc_gap"] * (len(cases) - 1) + pad_r
        hauteur = pad_y * 2 + 2 * g["bloc_pad"] + max(hauteur_p, texte_h)
    else:
        largeur = pad_l + texte_w + pad_r
        if pochette:
            largeur += largeur_p + g["gap"]
        hauteur = pad_y * 2 + max(hauteur_p, pile_h)

    # Les pastilles de fenetre repoussent tout le contenu vers le bas, les
    # boutons de transport prennent leur place a droite.
    if style.show_dots:
        hauteur += g["dots_h"]
    if style.show_controls:
        largeur += g["controls_w"]

    return int(largeur + g["ombre"]), int(hauteur + g["ombre"])


def max_overlay_size() -> tuple[int, int]:
    """La taille qui couvre TOUTES les combinaisons.

    Le chiffre à donner à qui ne veut pas réfléchir : il ne peut jamais
    rogner, quelle que soit la disposition choisie ensuite.
    """
    return _plus_grande(LAYOUT_KEYS)


def max_overlay_size_for(layout: str) -> tuple[int, int]:
    """La taille qui couvre toutes les combinaisons D'UNE disposition."""
    return _plus_grande([layout])


#: Briques optionnelles : chacune pousse la carte, donc chacune compte dans le
#: maximum. Les oublier annoncait une source trop petite, qui rogne dans OBS.
_BRIQUES = ("show_wave", "show_dots", "show_progress", "show_controls")


def _plus_grande(dispositions) -> tuple[int, int]:
    tailles = [
        overlay_size(replace(Style(), layout=disposition, cover=couv,
                             **dict(zip(_BRIQUES, etats))))
        for disposition in dispositions
        for couv in COVER_KEYS
        for etats in product((False, True), repeat=len(_BRIQUES))]
    return max(t[0] for t in tailles), max(t[1] for t in tailles)


def zones_visibles(style: "Style") -> list[str]:
    """Les briques réellement dessinées, dans l'ordre.

    Une brique coupée ne laisse pas de place vide, et une disposition qui ne
    porte pas de pochette n'en réserve pas : c'est exactement ce qui manquait
    quand l'aperçu montrait un cadre que le widget final n'avait pas.
    """
    regles = LAYOUT_ZONES.get(style.layout, LAYOUT_ZONES["compact"])
    zones = []
    if regles["pochette"] and style.cover != "aucune":
        zones.append("pochette")
    zones.append("texte")
    if style.show_wave:
        zones.append("onde")
    return zones


def _forme_pochette(style: "Style") -> str:
    """Traduit le réglage en forme concrète pour la vignette.

    « Auto » suit l'image reçue à l'exécution ; dans une vignette il n'y a pas
    d'image, donc on montre le cas de loin le plus fréquent : la pochette
    carrée d'un album, rendue en vinyle.
    """
    if style.cover in ("large", "carre"):
        return style.cover
    return "vinyle"


# ----------------------------------------------------------------------------
# Briques
# ----------------------------------------------------------------------------
def _pochette(dessin, boite, forme: str, accent: str, e: float) -> None:
    """Pochette d'exemple : un disque, un carré ou un rectangle 16:9.

    Un rond clair tient lieu d'image : dans une vignette, personne n'a la
    pochette du morceau à venir, et ce qui doit se lire ici c'est la FORME.
    """
    x0, y0, x1, y1 = boite
    if forme == "vinyle":
        dessin.ellipse([x0, y0, x1, y1], fill=(12, 10, 18, 255))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rayon = (x1 - x0) * 0.34
        dessin.ellipse([cx - rayon, cy - rayon, cx + rayon, cy + rayon],
                       fill=(242, 242, 248, 255))
        trou = 1.6 * e
        dessin.ellipse([cx - trou, cy - trou, cx + trou, cy + trou],
                       fill=(12, 10, 18, 255))
        return
    # Contour, pas de bandeau : la page ne dessine aucun liseré sous la
    # pochette, et en montrer un ici afficherait une brique qui n'existe pas
    # sur le widget final. Le contour, lui, sert la lisibilité — sur une carte
    # claire, un carré blanc sans trait ne se voit pas.
    dessin.rounded_rectangle([x0, y0, x1, y1], radius=3 * e,
                             fill=(242, 242, 248, 255),
                             outline=accent, width=max(1, int(e / 2)))


def _onde(dessin, x0: float, bas: float, largeur: float, hauteur: float,
          accent: str, e: float) -> None:
    """Barres de la forme d'onde, alignées sur leur base."""
    # Le nombre de barres suit la largeur : sur une onde pleine largeur, vingt
    # barres etalees devenaient des perles espacees au lieu d une forme d onde.
    barres = max(len(_SAMPLE_WAVE), int(largeur / (3.2 * e)))
    pas = largeur / barres
    # Epaisseur bornee : au-dela, la barre cesse d etre une barre.
    epaisseur = min(max(1.2 * e, pas * 0.5), 3.0 * e)
    rayon = min(epaisseur / 2, 1.2 * e)
    for index in range(barres):
        valeur = _SAMPLE_WAVE[index % len(_SAMPLE_WAVE)]
        x = x0 + index * pas
        haut = max(1.5 * e, hauteur * valeur)
        dessin.rounded_rectangle([x, bas - haut, x + epaisseur, bas],
                                 radius=rayon, fill=accent)


def _texte(dessin, x: float, y: float, style: "Style", largeur: float,
           e: float, petit: bool = False) -> float:
    """Titre, artiste et pastille. Retourne l'ordonnée atteinte."""
    encre = (24, 20, 44, 255) if is_light(style) else (244, 241, 251, 255)
    attenue = (92, 86, 122, 255) if is_light(style) else (168, 160, 194, 255)
    accent = style.accent or style.border

    police_titre = _police_grasse((11 if petit else 13) * e)
    dessin.text((x, y), _tronque(dessin, SAMPLE_TITLE, police_titre, largeur),
                font=police_titre, fill=encre)
    y += (14 if petit else 17) * e

    police_artiste = _police((9 if petit else 10) * e)
    fin = x
    if style.show_artist:
        artiste = _tronque(dessin, SAMPLE_ARTIST, police_artiste, largeur)
        dessin.text((x, y + 1 * e), artiste, font=police_artiste, fill=attenue)
        fin = x + dessin.textlength(artiste, font=police_artiste) + 7 * e
    if style.show_app:
        police = _police_grasse(7 * e)
        large = dessin.textlength(SAMPLE_APP, font=police) + 9 * e
        # La pastille ne s'affiche que si elle tient : à cheval sur le bord,
        # elle mentirait sur le rendu final.
        if fin + large <= x + largeur:
            dessin.rounded_rectangle([fin, y, fin + large, y + 11 * e],
                                     radius=5.5 * e, outline=accent,
                                     width=max(1, int(e / 2)))
            dessin.text((fin + 4.5 * e, y + 1.5 * e), SAMPLE_APP, font=police,
                        fill=accent)
    return y + (12 if petit else 13) * e


def _pastilles(dessin, x: float, y: float, e: float) -> None:
    """Les trois pastilles de fenetre facon macOS."""
    for indice, couleur in enumerate(((255, 95, 87), (254, 188, 46),
                                      (40, 200, 64))):
        gauche = x + indice * 9 * e
        dessin.ellipse([gauche, y, gauche + 6 * e, y + 6 * e], fill=couleur)


def _transport(dessin, droite: float, milieu: float, encre, e: float) -> float:
    """Boutons de transport, DECORATIFS. Retourne leur bord gauche."""
    taille = 7 * e
    ecart = 11 * e
    x = droite - taille
    # Suivant
    dessin.polygon([(x, milieu - taille / 2), (x, milieu + taille / 2),
                    (x + taille * 0.7, milieu)], fill=encre)
    # Pause : deux barres
    x -= ecart
    for decalage in (0, 3.5 * e):
        dessin.rectangle([x + decalage, milieu - taille / 2,
                          x + decalage + 2 * e, milieu + taille / 2], fill=encre)
    # Precedent
    x -= ecart
    dessin.polygon([(x + taille * 0.7, milieu - taille / 2),
                    (x + taille * 0.7, milieu + taille / 2),
                    (x, milieu)], fill=encre)
    return x - 4 * e


def _progression(dessin, x: float, y: float, largeur: float, encre, attenue,
                 accent: str, e: float) -> None:
    """Barre de progression et temps. Position d'exemple : un tiers."""
    hauteur = 2.5 * e
    dessin.rounded_rectangle([x, y, x + largeur, y + hauteur],
                             radius=hauteur / 2, fill=accent)
    dessin.rounded_rectangle([x, y, x + largeur * 0.42, y + hauteur],
                             radius=hauteur / 2, fill=encre)
    police = _police(7 * e)
    dessin.text((x, y + 5 * e), "1:42", font=police, fill=attenue)
    fin = dessin.textlength("3:58", font=police)
    dessin.text((x + largeur - fin, y + 5 * e), "3:58", font=police,
                fill=attenue)


def _cadre(dessin, boite, style: "Style", e: float) -> None:
    """Cadre d'une case, en mode Blocs uniquement."""
    dessin.rounded_rectangle(boite, radius=5 * e,
                             outline=style.accent or style.border,
                             width=max(1, int(e / 2)))


# ----------------------------------------------------------------------------
# Rendu
# ----------------------------------------------------------------------------
#: Largeur maximale de la carte, en proportion de sa hauteur, par disposition.
#:
#: 1.0 signifie « toute la largeur du cadre ». Galerie empile la pochette et le
#: texte : le widget reel y devient etroit et haut, donc sa vignette aussi.
_PROPORTION_CARTE = {"galerie": 1.15}


def _arrondir(plate, rayon: float):
    """Decoupe les coins d'une vignette au rayon de la carte.

    Sans ca, le damier restait un rectangle a angles droits derriere une carte
    arrondie : quatre equerres dans chaque vignette de la fenetre Widget.
    """
    masque = Image.new("L", plate.size, 0)
    ImageDraw.Draw(masque).rounded_rectangle(
        [0, 0, plate.size[0] - 1, plate.size[1] - 1], radius=rayon, fill=255)
    plate.putalpha(masque)
    return plate


def preview_png(style: Style, width: int = 240, height: int = 78,
                background: Optional[Path] = None,
                echelle: int = 1) -> bytes:
    """Vignette du rendu final : disposition, pochette, texte, forme d'onde.

    `width` et `height` décrivent la CARTE ; `echelle` ne change que la
    résolution de l'image rendue, pas ses proportions. C'est ce qui permet
    de fournir une image plus fine à l'interface sans que le texte
    rétrécisse par rapport à la pochette.

    Le dessin se fait à `_SS` fois cette résolution, puis est réduit :
    Pillow ne lisse rien, et un disque tracé à la taille finale sort en
    escalier.
    """
    style = style.sanitised()
    e = float(_SS * max(1, echelle))
    W, H = int(width * e), int(height * e)

    # La carte ne remplit pas forcement le cadre : en Galerie, le widget reel
    # devient etroit et haut, et l etirer sur toute la largeur donnait un
    # apercu que la page ne produit jamais. On dessine donc la carte a sa
    # forme, puis on la pose au centre du cadre.
    proportion = _PROPORTION_CARTE.get(style.layout)
    CW = int(min(W, H * proportion)) if proportion else W
    CH = H

    fond = _damier(W, H, case=int(10 * e))
    carte = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(carte)
    rayon = 11 * e

    pose = False
    if background is not None:
        try:
            with Image.open(background) as brut:
                carte.paste(brut.convert("RGBA").resize((CW, CH)), (0, 0))
            pose = True
        except Exception:
            pose = False
    if not pose:
        dessin.rounded_rectangle([0, 0, CW - 1, CH - 1], radius=rayon,
                                 fill=style.rgba)
    if style.border_on:
        if style.border_dashed:
            _pointille(dessin, CW, CH, int(rayon), style.border,
                       tiret=int(7 * e), trou=int(5 * e), epaisseur=int(2 * e))
        else:
            dessin.rounded_rectangle([0, 0, CW - 1, CH - 1], radius=rayon,
                                     outline=style.border, width=int(2 * e))

    _disposer(dessin, style, CW, CH, 9 * e, e)

    planche = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    planche.paste(carte, ((W - CW) // 2, 0))
    plate = _arrondir(Image.alpha_composite(fond, planche), rayon)

    sortie = io.BytesIO()
    cible = (width * max(1, echelle), height * max(1, echelle))
    plate.resize(cible, Image.LANCZOS).save(sortie, format="PNG")
    return sortie.getvalue()


def _disposer(dessin, style: "Style", W: int, H: int, marge: float,
              e: float) -> None:
    """Place les briques réellement visibles, sans en réserver d'autres."""
    regles = LAYOUT_ZONES.get(style.layout, LAYOUT_ZONES["compact"])
    zones = zones_visibles(style)
    forme = _forme_pochette(style)
    accent = style.accent or style.border
    petit = H < 70 * e

    if regles.get("vertical"):
        _galerie(dessin, style, W, H, marge, e, zones, forme, accent, petit)
    elif regles["colonnes"]:
        _colonnes(dessin, style, W, H, marge, e, zones, forme, accent)
    else:
        _ligne(dessin, style, W, H, marge, e, zones, forme, accent, petit,
               bool(regles.get("onde_pleine")))


def _taille_pochette(forme: str, cote: float,
                     dispo: float) -> tuple[float, float]:
    """Largeur et hauteur d'une pochette selon sa forme.

    Le 16:9 garde son rapport en RÉDUISANT la hauteur : plafonner la largeur
    seule le rendait carré, donc indistinguable du réglage « Carré ».
    """
    if forme != "large":
        return cote, cote
    largeur = min(cote * 16 / 9, dispo)
    return largeur, largeur * 9 / 16


def _ligne(dessin, style, W, H, marge, e, zones, forme, accent, petit,
           onde_pleine) -> None:
    """Compact, Minimal, Bandeau : pochette à gauche, texte empilé à droite."""
    x = marge
    haut_onde = 9 * e
    # Le bandeau porte une onde plus haute : c'est ce qui le distingue, pas
    # une carte plus large.
    reserve = haut_onde * 1.4 if ("onde" in zones and onde_pleine) else 0

    encre = (24, 20, 44, 255) if is_light(style) else (244, 241, 251, 255)
    attenue = (92, 86, 122, 255) if is_light(style) else (168, 160, 194, 255)

    # Les pastilles poussent tout le contenu vers le bas.
    haut_contenu = marge
    if style.show_dots:
        _pastilles(dessin, marge, marge - 2 * e, e)
        haut_contenu = marge + 10 * e

    if "pochette" in zones and H - haut_contenu - marge - reserve > 2:
        cote = H - haut_contenu - marge - reserve
        largeur, hauteur = _taille_pochette(forme, cote, W * 0.42)
        haut = haut_contenu + (H - haut_contenu - marge - reserve - hauteur) / 2
        _pochette(dessin, (x, haut, x + largeur, haut + hauteur), forme,
                  accent, e)
        x += largeur + 10 * e

    droite = W - marge
    if style.show_controls:
        droite = _transport(dessin, droite, (haut_contenu + H - marge) / 2,
                            encre, e)

    dispo = droite - x
    bas_texte = _texte(dessin, x, haut_contenu + 3 * e, style, dispo, e, petit)

    if style.show_progress:
        # Calee sur le BAS de la carte : posee juste sous le texte, elle le
        # traversait des que la vignette etait courte.
        hauteur_bloc = 14 * e
        y = max(bas_texte + 2 * e, H - marge - hauteur_bloc)
        _progression(dessin, x, y, dispo, encre, attenue, accent, e)
        return

    if "onde" not in zones:
        return
    # L'onde part TOUJOURS du bord gauche du texte, jamais de celui de la
    # carte : sinon elle passerait sous la pochette, ce qui se voit surtout
    # avec une pochette large.
    #
    # Elle est calee sur le BAS de la carte, et sa hauteur est bornee par la
    # place restante sous le texte : une onde plus haute ne doit pas remonter
    # dans la ligne de l'artiste.
    bas = H - marge
    voulue = haut_onde * (2.2 if onde_pleine else 1.2)
    hauteur = max(4 * e, min(voulue, bas - bas_texte - 3 * e))
    _onde(dessin, x, bas, dispo, hauteur, accent, e)


def _galerie(dessin, style, W, H, marge, e, zones, forme, accent,
             petit) -> None:
    """Pochette au-dessus, texte dessous, onde en pied."""
    # La pochette est le sujet de cette disposition : elle passe en premier,
    # et c est le texte qui se serre. L inverse la reduisait a un point.
    haut_texte = 24 * e
    haut_onde = 9 * e if "onde" in zones else 0
    haut = marge

    if "pochette" in zones:
        cote = min(H - 2 * marge - haut_texte - haut_onde, W * 0.34)
        if cote >= 8 * e:
            largeur, hauteur = _taille_pochette(forme, cote, W * 0.5)
            _pochette(dessin, ((W - largeur) / 2, haut, (W + largeur) / 2,
                               haut + hauteur), forme, accent, e)
            haut += hauteur + 4 * e

    _texte(dessin, marge, haut, style, W - 2 * marge, e, petit=True)
    if "onde" in zones:
        _onde(dessin, marge, H - marge + 2 * e, W - 2 * marge, 8 * e, accent, e)


def _colonnes(dessin, style, W, H, marge, e, zones, forme, accent) -> None:
    """Blocs : une case encadrée PAR brique visible, et aucune de plus.

    Le nombre de colonnes se déduit des briques réellement affichées. Couper
    la forme d'onde ou la pochette retire sa case, au lieu de laisser un cadre
    vide — c'est le défaut que montrait la version précédente.
    """
    ecart = 6 * e
    hauteur = H - 2 * marge
    # Les cases ne se partagent pas la largeur a parts egales : dans la page,
    # la case texte se dimensionne sur son contenu et prend donc plus de place
    # que la pochette. Des colonnes egales rendaient le titre illisible dans
    # l apercu alors que le widget, lui, l affichait en entier.
    poids = {"pochette": 1.0, "texte": 2.3, "onde": 1.5}
    total = sum(poids[zone] for zone in zones)
    dispo = W - 2 * marge - (len(zones) - 1) * ecart
    x = marge
    for zone in zones:
        largeur = dispo * poids[zone] / total
        _cadre(dessin, (x, marge, x + largeur, H - marge), style, e)
        if zone == "pochette":
            cote = min(largeur - 12 * e, hauteur - 12 * e)
            if cote > 2:
                lg, ht = _taille_pochette(forme, cote, largeur - 10 * e)
                _pochette(dessin, (x + (largeur - lg) / 2,
                                   marge + (hauteur - ht) / 2,
                                   x + (largeur + lg) / 2,
                                   marge + (hauteur + ht) / 2), forme, accent, e)
        elif zone == "texte":
            _texte(dessin, x + 8 * e, marge + 8 * e, style, largeur - 16 * e, e,
                   petit=True)
        else:
            _onde(dessin, x + 8 * e, H - marge - 9 * e, largeur - 16 * e,
                  hauteur - 22 * e, accent, e)
        x += largeur + ecart


def cover_preview_png(style: Style, forme: str, width: int = 128,
                      height: int = 54, echelle: int = 1) -> bytes:
    """Vignette d un choix de pochette : la forme seule, au centre, en grand.

    La vignette de disposition montre la carte entiere ; ici ce serait le
    contraire de ce qu on demande. Sur une carte complete, la pochette n est
    qu un detail de coin : « Auto » et « Vinyle » y paraissaient identiques, et
    on ne pouvait pas dire laquelle etait laquelle. Ce qui doit se lire dans
    cette grille, c est LA FORME.
    """
    style = style.sanitised()
    e = float(_SS * max(1, echelle))
    W, H = int(width * e), int(height * e)
    accent = style.accent or style.border

    fond = _damier(W, H, case=int(10 * e))
    carte = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dessin = ImageDraw.Draw(carte)
    rayon = 11 * e
    dessin.rounded_rectangle([0, 0, W - 1, H - 1], radius=rayon, fill=style.rgba)
    if style.border_on:
        dessin.rounded_rectangle([0, 0, W - 1, H - 1], radius=rayon,
                                 outline=style.border, width=int(2 * e))

    cx, cy = W / 2, H / 2
    cote = H - 14 * e

    if forme == "aucune":
        # Un emplacement barre : le seul choix qui se lit par son absence.
        demi = cote / 2
        boite = (cx - demi, cy - demi, cx + demi, cy + demi)
        _pointille_carre(dessin, boite, accent, int(e))
        dessin.line([cx - demi * 0.55, cy - demi * 0.55,
                     cx + demi * 0.55, cy + demi * 0.55],
                    fill=accent, width=int(2 * e))
        dessin.line([cx + demi * 0.55, cy - demi * 0.55,
                     cx - demi * 0.55, cy + demi * 0.55],
                    fill=accent, width=int(2 * e))
    elif forme == "auto":
        # Les deux formes cote a cote : « selon ce que l application publie ».
        petit = cote * 0.78
        lg = petit * 16 / 9
        gauche = cx - (lg + petit) / 2 - 2 * e
        _pochette(dessin, (gauche, cy - petit * 9 / 32, gauche + lg,
                           cy + petit * 9 / 32), "large", accent, e)
        droite = gauche + lg + 4 * e
        _pochette(dessin, (droite, cy - petit / 2, droite + petit,
                           cy + petit / 2), "vinyle", accent, e)
    elif forme == "large":
        lg = min(cote * 16 / 9, W - 16 * e)
        ht = lg * 9 / 16
        _pochette(dessin, (cx - lg / 2, cy - ht / 2, cx + lg / 2, cy + ht / 2),
                  "large", accent, e)
    else:
        demi = cote / 2
        _pochette(dessin, (cx - demi, cy - demi, cx + demi, cy + demi),
                  "vinyle" if forme == "vinyle" else "carre", accent, e)

    plate = _arrondir(Image.alpha_composite(fond, carte), rayon)
    sortie = io.BytesIO()
    cible = (width * max(1, echelle), height * max(1, echelle))
    plate.resize(cible, Image.LANCZOS).save(sortie, format="PNG")
    return sortie.getvalue()


def _pointille_carre(dessin, boite, couleur: str, e: int) -> None:
    """Carre en tirets. Pillow ne trace pas de pointille : on le compose."""
    x0, y0, x1, y1 = boite
    tiret, trou = 5 * e, 4 * e
    pas = tiret + trou
    x = x0
    while x < x1:
        fin = min(x + tiret, x1)
        dessin.line([x, y0, fin, y0], fill=couleur, width=e)
        dessin.line([x, y1, fin, y1], fill=couleur, width=e)
        x += pas
    y = y0
    while y < y1:
        fin = min(y + tiret, y1)
        dessin.line([x0, y, x0, fin], fill=couleur, width=e)
        dessin.line([x1, y, x1, fin], fill=couleur, width=e)
        y += pas


def layout_guide_png() -> bytes:
    """Gabarit coté, à récupérer pour dessiner son propre fond.

    Il donne la taille de référence de la carte et l'emplacement exact des
    trois zones occupées par l'application — vinyle, titre, artiste — pour
    qu'un dessin ne passe pas sous un texte.
    """
    marge = 24
    image = Image.new("RGBA", (CARD_W, CARD_H), (18, 14, 32, 255))
    dessin = ImageDraw.Draw(image)
    dessin.rectangle([0, 0, CARD_W - 1, CARD_H - 1], outline=(168, 85, 247), width=2)

    disque = CARD_H - 2 * marge
    zones = (
        ("VINYLE / POCHETTE", (marge, marge, marge + disque, marge + disque)),
        ("TITRE", (marge * 2 + disque, 52, CARD_W - marge, 92)),
        ("ARTISTE + APPLICATION", (marge * 2 + disque, 104, CARD_W - marge, 134)),
    )
    for libelle, (x0, y0, x1, y1) in zones:
        dessin.rectangle([x0, y0, x1, y1], outline=(120, 200, 255), width=2)
        dessin.text((x0 + 8, y0 + 6), libelle, fill=(200, 226, 255))
        # Cotes alignées à DROITE de la zone : une boîte de 30 px de haut ne
        # laisse pas la place de les écrire sous le libellé sans chevauchement.
        cotes = f"{int(x1 - x0)} x {int(y1 - y0)} px"
        dessin.text((x1 - 8 - 7 * len(cotes), y1 - 16), cotes, fill=(130, 150, 180))

    dessin.text((marge, CARD_H - 18), f"Carte de reference : {CARD_W} x {CARD_H} px",
                fill=(155, 147, 181))
    sortie = io.BytesIO()
    image.convert("RGB").save(sortie, format="PNG")
    return sortie.getvalue()


def write_guide(destination: Path) -> Optional[Path]:
    """Écrit le gabarit sur disque, pour l'ouvrir dans un logiciel de dessin."""
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(layout_guide_png())
        return destination
    except OSError:
        logger.warning("Gabarit non écrit : %s", destination)
        return None
