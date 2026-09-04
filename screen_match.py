"""
screen_match.py — Comparaison écran / image de référence par fragments.

POURQUOI DES FRAGMENTS
    Comparer une capture de référence ENTIÈRE à l'écran ne marche pas en jeu :
    la référence contient surtout du décor, et le décor bouge en permanence.
    Le score de corrélation oscille alors autour du seuil et l'état bascule
    sans arrêt entre « menu » et « en jeu ».

    Ce module découpe la référence en fragments (« patches ») et ne garde que
    les plus DISTINCTIFS — ceux qui contiennent du détail : bords de HUD,
    texte, icônes, boutons de menu. Le décor uniforme est écarté d'office.
    Chaque fragment est ensuite cherché indépendamment sur l'écran, et le
    score final agrège les meilleurs. Un fragment masqué par un effet ou un
    personnage ne fait plus chuter tout le score.

STABILITÉ
    `Stabilizer` exige plusieurs lectures identiques consécutives avant de
    confirmer un changement d'état. C'est ce qui empêche l'aller-retour entre
    deux scènes sur une image ambiguë.
"""
from __future__ import annotations

import logging
import threading
from typing import NamedTuple, Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger("obs_dynamics.screen_match")

# Nombre de fragments retenus par image de référence. Au-delà, le gain de
# robustesse plafonne et le coût CPU grimpe linéairement.
PATCH_COUNT = 6

# Côté d'un fragment, en fraction du plus petit côté de la référence. Trop
# petit, un fragment correspond n'importe où par hasard ; trop grand, il
# ré-attrape le décor mouvant qu'on cherche justement à éviter.
PATCH_FRAC = 0.16

# Un fragment doit contenir du détail pour être exploitable. Seuil sur la
# variance du laplacien (mesure d'énergie de contours) : en dessous, la zone
# est quasi unie et correspondrait à toute autre zone unie de l'écran.
MIN_DETAIL = 12.0

# Échelles essayées à la calibration : une référence capturée en 1080p doit
# rester reconnue si le jeu tourne en 720p ou en 4K.
MATCH_SCALES = (0.5, 0.6, 0.67, 0.75, 0.83, 1.0, 1.15, 1.33, 1.5, 1.75, 2.0)

# Part des meilleurs fragments moyennée pour donner le score final. Garder la
# moitié haute rend le résultat insensible à quelques fragments masqués tout
# en punissant une image qui ne correspond franchement pas.
TOP_FRACTION = 0.5

# Un fragment est cherché AUTOUR de la position qu'il occupait dans la
# référence, pas sur tout l'écran. Un HUD ne se déplace pas : ratisser l'écran
# entier coûtait ~9x plus cher (515 ms par cycle, mesuré, pour un intervalle de
# scan de 500 ms) et permettait en prime des correspondances fortuites
# ailleurs. La marge tolère les différences de mise en page entre résolutions.
SEARCH_MARGIN = 0.12

# Le ciblage positionnel n'a de sens que si la référence est une capture PLEIN
# ÉCRAN : les positions y sont alors celles de l'écran. Si l'utilisateur
# fournit un recadrage (juste le HUD, juste un logo), ces positions ne
# désignent plus rien et il faut chercher partout.
#
# Le critère est le RAPPORT LARGEUR/HAUTEUR, volontairement invariant à
# l'échelle : une capture 720p reste une capture plein écran face à un écran
# 1440p. Une règle basée sur la taille faussait la calibration — à petite
# échelle la référence semblait être un recadrage, la recherche passait en
# mode global, et une correspondance fortuite pouvait battre la bonne échelle.
ASPECT_TOLERANCE = 0.05


class Patch(NamedTuple):
    """Fragment de référence, sa position relative (centre, 0..1) et la taille
    de la référence dont il provient — nécessaire pour savoir si cette position
    est exploitable face à un écran donné."""
    image: np.ndarray
    fx: float
    fy: float
    ref_h: int
    ref_w: int


def detail_score(cell: np.ndarray) -> float:
    """Énergie de contours d'une zone — sert à repérer ce qui est distinctif."""
    if cell.size == 0:
        return 0.0
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY) if cell.ndim == 3 else cell
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# Taille maximale d'un fragment fusionné, en fraction de la référence. Sans
# plafond, une zone détaillée continue finirait par couvrir la moitié de
# l'image et on retomberait sur la comparaison plein cadre — celle dont on sait
# qu'elle s'effondre dès que le décor bouge.
MAX_PATCH_W = 0.55
MAX_PATCH_H = 0.40


def _excluded_predicate(exclude: Sequence[Sequence[float]], cell_w: float, cell_h: float):
    """Fabrique le test « cette case a-t-elle été refusée ? ».

    Une exclusion est une BOÎTE (fx, fy, largeur, hauteur) et non un point :
    un fragment fusionné peut couvrir une barre de vie entière, et le refuser
    doit écarter toute la barre, pas seulement la case de son centre. Le format
    à deux valeurs des enregistrements antérieurs reste accepté et se comporte
    comme une boîte d'une case.
    """
    boxes: list[tuple[float, float, float, float]] = []
    for item in exclude:
        values = list(item)
        if len(values) >= 4:
            boxes.append((values[0], values[1], values[2], values[3]))
        elif len(values) == 2:
            boxes.append((values[0], values[1], cell_w, cell_h))

    def is_excluded(fx: float, fy: float) -> bool:
        return any(abs(fx - bx) <= bw / 2 and abs(fy - by) <= bh / 2
                   for bx, by, bw, bh in boxes)

    return is_excluded


def extract_patches(image: np.ndarray, count: int = PATCH_COUNT,
                    frac: float = PATCH_FRAC,
                    exclude: Sequence[Sequence[float]] = (),
                    allow_fallback: bool = True) -> list[Patch]:
    """Renvoie les zones distinctives de l'image, épousant leur forme réelle.

    L'image est parcourue par une grille de cases, chacune notée sur son
    énergie de contours. Les cases retenues qui se touchent sont ensuite
    FUSIONNÉES en une seule zone, et le fragment prend la boîte englobante du
    groupe. Une barre de vie donne donc un rectangle large, une icône un petit
    carré, une bannière de menu un grand rectangle — au lieu de découper
    chaque élément en carrés identiques sans rapport avec sa forme.

    Deux cases voisines appartenant au même élément forment ainsi UN fragment,
    comparé d'un bloc : plus discriminant qu'une moitié d'élément, qui pourrait
    correspondre par hasard ailleurs.

    `exclude` liste les boîtes refusées par l'utilisateur. La sélection étant
    déterministe, relancer le calcul sans cette liste redonnerait exactement
    les mêmes fragments : c'est elle qui rend un refus utile.
    """
    if image is None or image.size == 0:
        return []
    h, w = image.shape[:2]
    side = max(16, int(min(h, w) * frac))
    if side > h or side > w:
        side = min(h, w)

    rows, cols = h // side, w // side
    if rows == 0 or cols == 0:
        return [Patch(image, 0.5, 0.5, h, w)] if allow_fallback else []

    is_excluded = _excluded_predicate(exclude, side / w, side / h)

    scores = np.zeros((rows, cols), dtype=np.float64)
    for r in range(rows):
        for c in range(cols):
            fx, fy = (c * side + side / 2) / w, (r * side + side / 2) / h
            if is_excluded(fx, fy):
                continue
            cell = image[r * side:(r + 1) * side, c * side:(c + 1) * side]
            scores[r, c] = detail_score(cell)

    keep = scores >= MIN_DETAIL
    if not keep.any():
        # Image entièrement plate (écran de chargement uni, fond noir) : aucun
        # fragment ne serait discriminant. On rend l'image entière plutôt que
        # rien, le comportement dégrade alors vers l'ancien plein-cadre.
        #
        # Ce repli ne vaut QUE pour une image vierge. Sinon, écarter la
        # dernière zone d'une capture faisait ressurgir l'image entière sous
        # la forme d'une zone « automatique » fantôme, en 1.0 x 1.0.
        return [Patch(image, 0.5, 0.5, h, w)] if allow_fallback else []

    groups = _connected_groups(keep)
    ranked = sorted(groups, key=lambda g: -sum(scores[r, c] for r, c in g))

    patches: list[Patch] = []
    for group in ranked[:count]:
        r0, r1 = min(r for r, _ in group), max(r for r, _ in group)
        c0, c1 = min(c for _, c in group), max(c for _, c in group)
        best_r, best_c = max(group, key=lambda rc: scores[rc[0], rc[1]])
        r0, r1 = _clamp_span(r0, r1, best_r, int(MAX_PATCH_H * rows) or 1)
        c0, c1 = _clamp_span(c0, c1, best_c, int(MAX_PATCH_W * cols) or 1)

        y0, y1 = r0 * side, (r1 + 1) * side
        x0, x1 = c0 * side, (c1 + 1) * side
        patches.append(Patch(image[y0:y1, x0:x1],
                             (x0 + x1) / 2 / w, (y0 + y1) / 2 / h, h, w))
    return patches


def _connected_groups(keep: np.ndarray) -> list[list[tuple[int, int]]]:
    """Groupes de cases retenues qui se touchent (voisinage à 4)."""
    rows, cols = keep.shape
    seen = np.zeros_like(keep, dtype=bool)
    groups: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(cols):
            if not keep[r, c] or seen[r, c]:
                continue
            stack, group = [(r, c)], []
            seen[r, c] = True
            while stack:
                cr, cc = stack.pop()
                group.append((cr, cc))
                for nr, nc in ((cr - 1, cc), (cr + 1, cc), (cr, cc - 1), (cr, cc + 1)):
                    if 0 <= nr < rows and 0 <= nc < cols and keep[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
            groups.append(group)
    return groups


def _clamp_span(start: int, end: int, anchor: int, limit: int) -> tuple[int, int]:
    """Réduit un intervalle de cases à `limit`, en le recentrant sur `anchor`.

    Un groupe trop étendu est resserré autour de sa case la plus détaillée
    plutôt que rogné arbitrairement d'un côté.
    """
    if end - start + 1 <= limit:
        return start, end
    half = limit // 2
    new_start = max(start, min(anchor - half, end - limit + 1))
    return new_start, new_start + limit - 1


def patch_from_box(image: np.ndarray, box: Sequence[float]) -> Optional[Patch]:
    """Fabrique un fragment à partir d'une boîte relative (cx, cy, largeur,
    hauteur) tracée à la main. Renvoie None si la zone est trop petite pour
    être comparée de façon fiable."""
    if image is None or image.size == 0 or len(list(box)) < 4:
        return None
    h, w = image.shape[:2]
    cx, cy, bw, bh = (float(v) for v in list(box)[:4])
    x0 = int(round((cx - bw / 2) * w))
    y0 = int(round((cy - bh / 2) * h))
    x1 = int(round((cx + bw / 2) * w))
    y1 = int(round((cy + bh / 2) * h))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return Patch(image[y0:y1, x0:x1], (x0 + x1) / 2 / w, (y0 + y1) / 2 / h, h, w)


def build_patches(image: np.ndarray, count: int = PATCH_COUNT,
                  frac: float = PATCH_FRAC,
                  exclude: Sequence[Sequence[float]] = (),
                  manual: Sequence[Sequence[float]] = ()) -> list[Patch]:
    """Fragments finalement comparés à l'écran : zones tracées à la main
    d'abord, puis zones trouvées automatiquement pour compléter.

    Les zones manuelles sont prioritaires et ne sont jamais remplacées ; elles
    sont aussi retirées de la recherche automatique, sinon la même région
    ressortirait deux fois. Ne rien tracer laisse le comportement automatique
    inchangé.
    """
    chosen = [patch for patch in (patch_from_box(image, box) for box in manual)
              if patch is not None]
    remaining = max(0, count - len(chosen))
    if remaining:
        # Le repli plein cadre n'a de sens que sur une image vierge : dès qu'il
        # existe une zone manuelle ou une exclusion, l'utilisateur a désigné ce
        # qu'il veut et un fragment couvrant tout l'écran serait un parasite.
        chosen.extend(extract_patches(image, remaining, frac,
                                      exclude=list(exclude) + list(manual),
                                      allow_fallback=not manual and not exclude))
    return chosen


def _match_one(screen: np.ndarray, patch: Optional[np.ndarray],
               fx: Optional[float] = None, fy: Optional[float] = None) -> float:
    """Cherche `patch` dans `screen`, autour de (fx, fy) si fournis."""
    if patch is None or patch.size == 0:
        return 0.0
    sh, sw = screen.shape[:2]
    ph, pw = patch.shape[:2]
    if ph > sh or pw > sw or ph < 8 or pw < 8:
        return 0.0

    region = screen
    if fx is not None and fy is not None:
        mx, my = int(sw * SEARCH_MARGIN), int(sh * SEARCH_MARGIN)
        cx, cy = int(sw * fx), int(sh * fy)
        x0 = max(0, min(sw - pw, cx - pw // 2 - mx))
        y0 = max(0, min(sh - ph, cy - ph // 2 - my))
        x1 = min(sw, max(x0 + pw, cx + pw // 2 + mx))
        y1 = min(sh, max(y0 + ph, cy + ph // 2 + my))
        candidate = screen[y0:y1, x0:x1]
        if candidate.shape[0] >= ph and candidate.shape[1] >= pw:
            region = candidate

    try:
        return float(cv2.minMaxLoc(cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED))[1])
    except cv2.error:
        logger.debug("matchTemplate échoué sur un fragment.", exc_info=True)
        return 0.0


def _rescale(patch: np.ndarray, factor: float) -> Optional[np.ndarray]:
    if factor == 1.0:
        return patch
    ph, pw = patch.shape[:2]
    nh, nw = int(ph * factor), int(pw * factor)
    if nh < 8 or nw < 8:
        return None
    interp = cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(patch, (nw, nh), interpolation=interp)


def aggregate(scores: list[float]) -> float:
    """Score final d'une référence à partir de ceux de ses fragments."""
    if not scores:
        return 0.0
    ordered = sorted(scores, reverse=True)
    keep = max(1, int(round(len(ordered) * TOP_FRACTION)))
    return float(sum(ordered[:keep]) / keep)


def is_fullscreen_reference(patch: Patch, screen: np.ndarray) -> bool:
    """La référence est-elle une capture plein écran (positions exploitables) ?"""
    sh, sw = screen.shape[:2]
    if sh == 0 or sw == 0 or patch.ref_h == 0:
        return False
    screen_ratio = sw / sh
    return abs(patch.ref_w / patch.ref_h - screen_ratio) <= ASPECT_TOLERANCE * screen_ratio


def score_patches(screen: np.ndarray, patches: list[Patch], scale: float = 1.0) -> float:
    scores = []
    for patch in patches:
        resized = _rescale(patch.image, scale)
        if is_fullscreen_reference(patch, screen):
            scores.append(_match_one(screen, resized, patch.fx, patch.fy))
        else:
            # Recadrage fourni par l'utilisateur : on ne sait pas où il se
            # trouve sur l'écran, on cherche donc partout.
            scores.append(_match_one(screen, resized))
    return aggregate(scores)


def calibrate(screen: np.ndarray, patches: list[Patch]) -> tuple[float, float]:
    """Cherche l'échelle qui fait le mieux correspondre les fragments.

    Tous les fragments d'une même référence viennent de la même capture, donc
    partagent la même échelle : on la cherche UNE fois pour toute l'image.
    """
    best_scale, best_score = 1.0, -1.0
    for factor in MATCH_SCALES:
        score = score_patches(screen, patches, factor)
        if score > best_score:
            best_scale, best_score = factor, score
    return best_scale, max(0.0, best_score)


class Stabilizer:
    """Exige N lectures identiques d'affilée avant de confirmer un changement.

    Sans ça, une image ambiguë (transition, cinématique, écran de chargement)
    fait osciller l'état à chaque cycle et OBS bascule sans arrêt entre les
    deux scènes. La valeur confirmée n'est renvoyée qu'une fois la série
    atteinte ; entre-temps, l'état précédent est maintenu.
    """

    def __init__(self, confirmations: int = 2) -> None:
        self._needed = max(1, confirmations)
        self._confirmed: dict[str, str] = {}
        self._pending: dict[str, tuple[str, int]] = {}
        self._lock = threading.Lock()

    def update(self, key: str, observed: str) -> str:
        with self._lock:
            current = self._confirmed.get(key)
            if observed == current:
                self._pending.pop(key, None)
                return observed

            candidate, seen = self._pending.get(key, (observed, 0))
            seen = seen + 1 if candidate == observed else 1
            if current is None or seen >= self._needed:
                self._confirmed[key] = observed
                self._pending.pop(key, None)
                return observed

            self._pending[key] = (observed, seen)
            return current

    def forget(self, key: str) -> None:
        with self._lock:
            self._confirmed.pop(key, None)
            self._pending.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._confirmed.clear()
            self._pending.clear()


# ============================================================================
# APERÇU — montrer à l'utilisateur CE QUE l'agent regarde
# ============================================================================

# Couleurs BGR des cadres dessinés sur l'aperçu.
# Couleurs en BGR (convention OpenCV), pas en RGB : (235, 180, 60) donnerait
# du cyan alors que l'interface annonce de l'orange pour les zones manuelles.
_KEPT_COLOR = (90, 220, 90)        # vert   : trouvé automatiquement
_EXCLUDED_COLOR = (70, 70, 235)    # rouge  : écarté par l'utilisateur
_MANUAL_COLOR = (60, 180, 235)     # orange : tracé à la main


def relative_size(patch: Patch) -> tuple[float, float]:
    """(largeur, hauteur) du fragment en fraction de sa référence.

    Les fragments sont découpés dans l'image RÉDUITE, alors que l'aperçu est
    dessiné sur l'image d'origine : passer par des fractions évite d'avoir à
    connaître le facteur de réduction ici. Largeur et hauteur sont distinctes
    depuis que les fragments épousent la forme de l'élément détecté.
    """
    if patch.ref_h == 0 or patch.ref_w == 0:
        return 0.0, 0.0
    return patch.image.shape[1] / patch.ref_w, patch.image.shape[0] / patch.ref_h


def patch_box(patch: Patch) -> tuple[float, float, float, float]:
    """Boîte relative (centre x, centre y, largeur, hauteur) — sert d'exclusion."""
    rw, rh = relative_size(patch)
    return patch.fx, patch.fy, rw, rh


def _badge_spot(box: tuple[int, int, int, int], bw: int, bh: int,
                w: int, h: int) -> tuple[int, int]:
    """Coin haut-gauche de la pastille du numéro, TOUJOURS hors du cadre.

    Posée à l'intérieur, elle masquait justement le détail que le fragment est
    censé reconnaître. On essaie au-dessus, puis en dessous, puis sur les
    côtés, en gardant la pastille dans l'image ; si rien ne tient (cadre
    occupant toute la référence), on retombe à l'intérieur.
    """
    x0, y0, x1, y1 = box
    for bx, by in ((x0, y0 - bh), (x0, y1), (x0 - bw, y0), (x1, y0)):
        if 0 <= bx and bx + bw <= w and 0 <= by and by + bh <= h:
            return bx, by
    return max(0, min(x0, w - bw)), max(0, min(y0, h - bh))


def draw_preview(image: np.ndarray, patches: Sequence[Patch],
                 excluded: Sequence[int] = (), manual_count: int = 0,
                 highlight: Optional[int] = None, dim_outside: bool = True) -> np.ndarray:
    """Dessine les fragments numérotés sur une copie de l'image de référence.

    Tout ce qui n'est PAS retenu est assombri, comme dans un outil de
    recadrage : un simple liseré de deux pixels sur une capture de jeu chargée
    ne se remarquait pas — mesuré à 1,3 % des pixels modifiés quand on déplace
    une zone, donc un réglage aux curseurs semblait ne rien faire.

    Les numéros ne sont pas décoratifs : ce sont eux que l'utilisateur désigne
    pour écarter un fragment mal tombé. Les `manual_count` premiers sont des
    zones tracées à la main et reçoivent une couleur distincte ; `highlight`
    épaissit le cadre en cours de réglage aux curseurs.
    """
    h, w = image.shape[:2]
    boxes: list[tuple[int, int, int, int]] = []
    for patch in patches:
        rel_w, rel_h = relative_size(patch)
        cx, cy = patch.fx * w, patch.fy * h
        boxes.append((int(max(0, cx - rel_w * w / 2)), int(max(0, cy - rel_h * h / 2)),
                      int(min(w, cx + rel_w * w / 2)), int(min(h, cy + rel_h * h / 2))))

    if dim_outside and boxes:
        canvas = (image * 0.35).astype(np.uint8)
        for x0, y0, x1, y1 in boxes:
            canvas[y0:y1, x0:x1] = image[y0:y1, x0:x1]
    else:
        canvas = image.copy()

    # Traits FINS : l'épaisseur était proportionnelle sans plafond, ce qui
    # donnait un cadre de 3 px sur une image de 100 px de haut — soit 3 % de sa
    # hauteur, illisible quand la référence est une simple bande de menu.
    thickness = max(1, min(3, round(min(h, w) / 320)))
    halo = thickness + 2
    font_scale = max(0.35, min(0.8, min(h, w) / 700))
    font_weight = max(1, thickness)
    dropped = set(excluded)

    for index, (x0, y0, x1, y1) in enumerate(boxes, start=1):
        if index in dropped:
            color = _EXCLUDED_COLOR
        elif index <= manual_count:
            color = _MANUAL_COLOR
        else:
            color = _KEPT_COLOR
        width = thickness + 1 if index == highlight else thickness

        # Halo sombre sous la couleur : sans lui, un cadre vert posé sur un
        # HUD clair disparaît complètement.
        cv2.rectangle(canvas, (x0, y0), (x1, y1), (15, 15, 25), width + halo)
        cv2.rectangle(canvas, (x0, y0), (x1, y1), color, width)

        label = str(index)
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX,
                                      font_scale, font_weight)
        bw, bh = tw + 8, th + 8
        bx, by = _badge_spot((x0, y0, x1, y1), bw, bh, w, h)
        cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh), color, -1)
        cv2.putText(canvas, label, (bx + 4, by + bh - 5), cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale, (15, 15, 25), font_weight, cv2.LINE_AA)

    return canvas


class Separation(NamedTuple):
    """Verdict croisé entre les deux références d'un même jeu."""
    menu_on_menu: float
    game_on_game: float
    menu_on_game: float
    game_on_menu: float

    @property
    def ok(self) -> bool:
        """Chaque référence doit reconnaître SON écran et rejeter l'autre.

        Sans cette marge, aucun réglage de cadrage ne sauvera la détection :
        le problème est dans le choix des captures, pas dans les fragments.
        """
        return (min(self.menu_on_menu, self.game_on_game) >= 0.8
                and max(self.menu_on_game, self.game_on_menu) <= 0.6)

    @property
    def margin(self) -> float:
        return min(self.menu_on_menu, self.game_on_game) - max(self.menu_on_game, self.game_on_menu)


def cross_check(menu_screen: np.ndarray, menu_patches: Sequence[Patch],
                game_screen: np.ndarray, game_patches: Sequence[Patch]) -> Separation:
    """Les deux captures fournies savent-elles se distinguer l'une de l'autre ?"""
    return Separation(
        menu_on_menu=score_patches(menu_screen, list(menu_patches)),
        game_on_game=score_patches(game_screen, list(game_patches)),
        menu_on_game=score_patches(game_screen, list(menu_patches)),
        game_on_menu=score_patches(menu_screen, list(game_patches)),
    )
