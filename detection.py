"""Détection de l'état d'un jeu : processus actif + comparaison visuelle.

Aucune dépendance à l'interface : ce module tourne sans serveur graphique,
ce qui permet de le tester seul. Les libellés et couleurs d'état vivent dans
ui_common.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import psutil
from PIL import Image, ImageGrab

import screen_match
from app_paths import logger
from games import Game


# ============================================================================
# DÉTECTION PROCESSUS + VISUELLE (OpenCV)
# ============================================================================
def _imread_unicode(path: str, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """cv2.imread() échoue silencieusement sur les chemins Windows contenant
    des caractères accentués ou des apostrophes. Contournement fiable :
    lecture des octets bruts via numpy.fromfile puis décodage cv2.imdecode."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except (OSError, ValueError):
        logger.debug("Lecture image échouée: %s", path, exc_info=True)
        return None


def is_game_active(game: Game) -> bool:
    """Steam: correspondance par chemin d'installation (fiable, indépendant du
    nom d'exe). Manuel: correspondance par nom de processus."""
    try:
        if game.source == "steam" and game.active_match:
            target = str(Path(game.active_match)).lower()
            for proc in psutil.process_iter(["exe"]):
                exe = proc.info.get("exe")
                if exe and exe.lower().startswith(target):
                    return True
            return False
        if game.active_match:
            target_name = game.active_match.strip().lower()
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").lower()
                if name == target_name:
                    return True
    except (psutil.Error, OSError):
        logger.debug("Erreur vérification processus pour '%s'.", game.name, exc_info=True)
    return False


def _capture_screen_bgr() -> Optional[np.ndarray]:
    try:
        img: Image.Image = ImageGrab.grab()
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception:
        logger.debug("Capture écran échouée.", exc_info=True)
        return None


# Facteur de réduction appliqué à l'écran ET aux templates avant matchTemplate.
# matchTemplate coûte O(W·H·w·h) : diviser les deux dimensions par 2 divise le
# coût par ~16. La corrélation normalisée reste fiable à cette échelle pour de
# la détection de HUD/menu, qui ne joue pas sur le détail fin.
DETECT_SCALE = 0.5

# Écart minimal entre le score « menu » et le score « en jeu » pour trancher.
# Les séparations réelles mesurées sont de 0,75 à 1,00 : 0,15 ne bloque que les
# quasi-égalités, c'est-à-dire les cas où l'écran ne ressemble franchement ni à
# l'un ni à l'autre. Dans ce cas aucune scène n'est imposée.
DECISION_MARGIN = 0.15


def _downscale(img: np.ndarray, scale: float = DETECT_SCALE) -> np.ndarray:
    if scale >= 1.0:
        return img
    h, w = img.shape[:2]
    nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
    return cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)


class _TemplateCache:
    """Garde en mémoire les templates déjà décodés ET déjà réduits.

    Avant, _best_match_score rappelait _imread_unicode à CHAQUE cycle de scan
    pour CHAQUE image de référence : relecture disque + décodage JPEG/PNG
    toutes les 2 secondes, pour des fichiers qui ne changent jamais.
    L'entrée est invalidée sur (mtime, taille) pour que remplacer une image
    de référence soit pris en compte sans redémarrer l'application.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[tuple[float, int], Optional[np.ndarray]]] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> Optional[np.ndarray]:
        try:
            st = os.stat(path)
            stamp = (st.st_mtime, st.st_size)
        except OSError:
            with self._lock:
                self._entries.pop(path, None)
            return None

        with self._lock:
            hit = self._entries.get(path)
            if hit is not None and hit[0] == stamp:
                return hit[1]

        raw = _imread_unicode(path, cv2.IMREAD_COLOR)
        tpl = _downscale(raw) if raw is not None else None
        with self._lock:
            self._entries[path] = (stamp, tpl)
        # L'image a changé sur le disque : ses fragments et son échelle
        # calibrée ne valent plus rien, et les garder ferait échouer la
        # détection en silence.
        _SCALES.forget(path)
        _PATCHES.forget(path)
        return tpl

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
        _SCALES.clear()
        _PATCHES.clear()


_TEMPLATES = _TemplateCache()


# Échelles essayées lors de la calibration. Une image de référence capturée en
# 1920x1080 puis rejouée en 1440p ou en 720p ne correspond plus du tout à
# l'échelle 1 : score mesuré entre 0,28 et 0,60, très en dessous du seuil de
# 0,8, donc détection qui ne se déclenche jamais. Ces facteurs couvrent les
# rapports courants entre résolutions, du 720p au 4K.
_MATCH_SCALES = (0.5, 0.6, 0.67, 0.75, 0.83, 1.0, 1.15, 1.33, 1.5, 1.75, 2.0)


def _rescale_template(template: np.ndarray, factor: float) -> Optional[np.ndarray]:
    if factor == 1.0:
        return template
    th, tw = template.shape[:2]
    nh, nw = int(th * factor), int(tw * factor)
    if nh < 8 or nw < 8:
        return None
    interp = cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(template, (nw, nh), interpolation=interp)


def _match_one(screen_small: np.ndarray, template: Optional[np.ndarray]) -> float:
    if template is None:
        return 0.0
    sh, sw = screen_small.shape[:2]
    th, tw = template.shape[:2]
    if th > sh or tw > sw:
        return 0.0
    try:
        result = cv2.matchTemplate(screen_small, template, cv2.TM_CCOEFF_NORMED)
        return float(cv2.minMaxLoc(result)[1])
    except cv2.error:
        logger.debug("matchTemplate échoué.", exc_info=True)
        return 0.0


class _ScaleCalibration:
    """Retient à quelle échelle chaque image de référence correspond.

    Balayer les 11 échelles coûte quelques centaines de ms par image : le
    refaire à chaque cycle mangerait tout l'intervalle de scan. Or la
    résolution de jeu ne change pas en cours de partie — l'échelle est donc
    cherchée une fois puis réutilisée.

    PIÈGE ÉVITÉ : une calibration n'est retenue que si elle a effectivement
    trouvé la référence à l'écran. Sinon, la toute première calibration se
    faisait contre n'importe quel écran affiché à ce moment-là — typiquement
    le menu quand on calibre la référence « en jeu » — et verrouillait une
    échelle absurde. La référence marquait alors 0,09 même face à une copie
    conforme d'elle-même, définitivement.

    Une calibration non concluante est réessayée, mais seulement tous les
    RETRY_EVERY cycles : entre-temps l'échelle 1 est utilisée, ce qui ne coûte
    qu'un matchTemplate. Tout est remis à zéro si la taille de l'écran change
    (résolution, passage en fenêtré) ou si le fichier de référence est modifié.
    """

    CONFIRM_SCORE = 0.5     # en dessous, la calibration ne prouve rien
    RETRY_EVERY = 10        # cycles avant de retenter une calibration douteuse

    def __init__(self) -> None:
        # path -> (échelle, calibration concluante, cycles avant nouvel essai)
        self._scales: dict[str, tuple[float, bool, int]] = {}
        self._screen_shape: Optional[tuple[int, int]] = None
        self._lock = threading.Lock()

    def scale_to_use(self, path: str, screen_shape: tuple[int, int]
                     ) -> Optional[tuple[float, bool]]:
        """(échelle, provisoire) à appliquer, ou None s'il faut recalibrer.

        « provisoire » signale une échelle de repli non validée : si elle donne
        finalement un bon score, l'appelant doit la confirmer via remember(),
        ce qui évite un balayage complet inutile quelques cycles plus tard.
        """
        with self._lock:
            if screen_shape != self._screen_shape:
                self._screen_shape = screen_shape
                self._scales.clear()
            entry = self._scales.get(path)
            if entry is None:
                return None
            scale, confirmed, retry_in = entry
            if confirmed:
                return (scale, False)
            if retry_in <= 0:
                return None
            self._scales[path] = (scale, False, retry_in - 1)
            return (1.0, True)

    def remember(self, path: str, scale: float, score: float) -> None:
        confirmed = score >= self.CONFIRM_SCORE
        with self._lock:
            self._scales[path] = (scale, confirmed, 0 if confirmed else self.RETRY_EVERY)

    def is_confirmed(self, path: str) -> bool:
        with self._lock:
            entry = self._scales.get(path)
            return bool(entry and entry[1])

    def forget(self, path: str) -> None:
        with self._lock:
            self._scales.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._scales.clear()
            self._screen_shape = None


_SCALES = _ScaleCalibration()


def image_stamp(path: str) -> str:
    """Empreinte d'un fichier de référence : date de modification + taille.

    Sert à savoir si l'image a changé dans le dossier depuis la dernière
    validation de l'utilisateur. Même critère que _TemplateCache, donc les deux
    s'invalident ensemble.
    """
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return f"{st.st_mtime}:{st.st_size}"


class _PatchReviews:
    """Fragments écartés par l'utilisateur, par image de référence.

    Le cache de fragments est global (une même image peut servir à deux jeux),
    donc les exclusions le sont aussi : elles portent sur l'IMAGE, pas sur le
    jeu qui l'utilise. Le registre est rechargé depuis games.json au démarrage
    et à chaque enregistrement.
    """

    def __init__(self) -> None:
        self._excluded: dict[str, list[tuple[float, ...]]] = {}
        self._manual: dict[str, list[tuple[float, ...]]] = {}
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def excluded_for(self, path: str) -> list[tuple[float, ...]]:
        with self._lock:
            return list(self._excluded.get(path, ()))

    def manual_for(self, path: str) -> list[tuple[float, ...]]:
        with self._lock:
            return list(self._manual.get(path, ()))

    def count_for(self, path: str) -> Optional[int]:
        with self._lock:
            return self._counts.get(path)

    def set(self, path: str, excluded: list[tuple[float, ...]],
            manual: Optional[list[tuple[float, ...]]] = None,
            count: Optional[int] = None) -> None:
        changed = False
        with self._lock:
            if count is not None and self._counts.get(path) != count:
                self._counts[path] = count
                changed = True
            if list(self._excluded.get(path, [])) != list(excluded):
                self._excluded[path] = list(excluded)
                changed = True
            if manual is not None and list(self._manual.get(path, [])) != list(manual):
                self._manual[path] = list(manual)
                changed = True
        if changed:
            # Les fragments retenus dépendent de ces réglages : les recalculer,
            # et repartir de zéro sur l'échelle qui en découlait.
            _PATCHES.forget(path)
            _SCALES.forget(path)

    def load_from_games(self, games: list[Game]) -> None:
        for game in games:
            for path, review in (game.patch_reviews or {}).items():
                boxes = lambda key: [tuple(float(v) for v in box)
                                     for box in review.get(key, []) or []]
                stored = review.get("count")
                self.set(str(path), boxes("excluded"), boxes("manual"),
                         int(stored) if stored else None)

    def clear(self) -> None:
        with self._lock:
            self._excluded.clear()
            self._manual.clear()
            self._counts.clear()


class _PatchCache:
    """Fragments distinctifs extraits de chaque image de référence.

    L'extraction parcourt toute l'image (variance du laplacien sur une grille)
    et ne se justifie pas à chaque cycle de scan : le résultat ne dépend que du
    fichier, invalidé par _TemplateCache quand il change sur le disque.
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[screen_match.Patch]] = {}
        self._lock = threading.Lock()

    def get(self, path: str, template: np.ndarray) -> list[screen_match.Patch]:
        with self._lock:
            hit = self._entries.get(path)
        if hit is not None:
            return hit
        manual = _REVIEWS.manual_for(path)
        count = _REVIEWS.count_for(path)
        patches = screen_match.build_patches(
            template, count=max(count, len(manual)) if count else screen_match.PATCH_COUNT,
            exclude=_REVIEWS.excluded_for(path), manual=manual)
        with self._lock:
            self._entries[path] = patches
        logger.debug("%d fragments extraits de %s.", len(patches), path)
        return patches

    def forget(self, path: str) -> None:
        with self._lock:
            self._entries.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_PATCHES = _PatchCache()
_REVIEWS = _PatchReviews()


def _best_match_score(screen_small: np.ndarray, template_paths: list[str]) -> float:
    """Score de correspondance entre l'écran et un jeu d'images de référence.

    Chaque référence est réduite à ses fragments les plus distinctifs (HUD,
    texte, icônes) et c'est sur EUX que porte la comparaison. Comparer la
    capture entière échouait en jeu : la référence est majoritairement du
    décor, le décor bouge, et le score oscillait autour du seuil — d'où des
    bascules de scène incessantes.

    `screen_small` doit DÉJÀ être réduit par _downscale ; les références le
    sont aussi via le cache, sinon les échelles ne correspondraient pas.

    Le score renvoyé est toujours le VRAI meilleur score : aucun court-circuit
    au franchissement du seuil, car detect_game_state compare ensuite le score
    « menu » au score « en jeu » et un score tronqué fausserait l'arbitrage.
    """
    best = 0.0
    shape = (screen_small.shape[0], screen_small.shape[1])
    for path in template_paths:
        template = _TEMPLATES.get(path)
        if template is None:
            continue
        patches = _PATCHES.get(path, template)
        if not patches:
            continue

        known = _SCALES.scale_to_use(path, shape)
        if known is not None:
            scale, provisional = known
            score = screen_match.score_patches(screen_small, patches, scale)
            if provisional and score >= _ScaleCalibration.CONFIRM_SCORE:
                _SCALES.remember(path, scale, score)   # l'échelle de repli suffit
            best = max(best, score)
            continue

        # Calibration : cherchée une fois, conservée seulement si concluante.
        scale, score = screen_match.calibrate(screen_small, patches)
        _SCALES.remember(path, scale, score)
        best = max(best, score)
    return best


def detect_game_state(game: Game, threshold: float,
                      screen_small: Optional[np.ndarray] = None) -> str:
    """'inactive' | 'active' (process seul, sans images de référence) |
    'menu' | 'in_game' | 'extra:<id>' (correspondance visuelle OpenCV).

    `screen_small` est la capture d'écran DÉJÀ réduite, partagée par tous les
    jeux d'un même cycle de scan. Avant, chaque jeu déclenchait son propre
    ImageGrab.grab() plein écran : 10 jeux configurés = 10 captures toutes les
    2 secondes. Laisser le paramètre à None reste possible (la capture est
    alors faite ici) pour les appels isolés et les tests.
    """
    if not is_game_active(game):
        return "inactive"
    states = [(key, images) for key, images, _scene in game.detection_states() if images]
    if not states:
        return "active"
    if screen_small is None:
        raw = _capture_screen_bgr()
        if raw is None:
            return "active"
        screen_small = _downscale(raw)

    scores = sorted(((_best_match_score(screen_small, images), key) for key, images in states),
                    reverse=True)

    # L'état sort UNIQUEMENT de ce qui est à l'écran : le meilleur score doit
    # franchir le seuil, ET devancer le suivant d'une marge nette. Sans cette
    # marge, menu=0,82 contre jeu=0,83 suffisait à basculer — une décision
    # prise sur du bruit, qui donnait l'impression d'un va-et-vient régulier
    # entre les deux scènes. En cas d'égalité, on renvoie "active" : aucune
    # scène n'y est associée, donc OBS n'est pas touché et l'affichage reste
    # sur ce qu'il montrait. La règle vaut pour deux états comme pour dix.
    best, best_key = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0.0
    if best < threshold or best - runner_up < DECISION_MARGIN:
        return "active"
    return best_key
