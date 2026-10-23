"""Sous-titres CC : ce qu'on envoie à OBS, quand, et sous quelle forme.

OBS glisse le texte reçu par `SendStreamCaption` dans le flux vidéo (CEA-608) ;
chaque spectateur l'affiche ou non avec le bouton CC de Twitch ou YouTube, et
choisit lui-même police, couleur et taille. Le streamer ne règle que le texte.

Contraintes du format, d'où ce module :
  - 32 caractères par ligne au plus, peu de lignes : une phrase longue part en
    plusieurs morceaux, chacun laissé à l'écran le temps d'être lu ;
  - un morceau envoyé ne se corrige plus : on n'envoie que du texte figé. En
    mode Précis, on attend la correction du second passage (`REVISE_WAIT_S`),
    puis on envoie la phrase telle quelle si elle n'est pas venue.

Tout est piloté par `tick()`, appelé par l'interface toutes les 200 ms, avec
une horloge injectable : la logique se teste sans attendre.
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app_paths import DATA_DIR

CC_PATH = DATA_DIR / "subtitles_cc.json"
#: 32 colonnes : limite du CEA-608, que lisent Twitch et YouTube. Au-delà, le
#: lecteur coupe ou renvoie à la ligne lui-même. Pour afficher plus de texte,
#: on ajoute des lignes (4 × 32 = 128 caractères), pas des colonnes.
SCREEN_COLUMNS = 32
WIDTH_RANGE = (20, SCREEN_COLUMNS)
LINE_CHOICES = (1, 2, 3, 4)
REVISE_WAIT_S = 1.2           # au-delà, la phrase part sans sa correction
READ_CHARS_PER_S = 17         # vitesse de lecture visée
SHOW_MIN_S, SHOW_MAX_S = 1.5, 4.0
BACKLOG_MAX = 3               # morceaux en attente au-delà desquels on accélère

# Liste courte et volontairement prudente : les gros mots les plus courants,
# en français et en anglais. Remplacés par « *** », jamais devinés.
_PROFANITY = frozenset({
    "putain", "merde", "connard", "connasse", "salope", "encule", "enculé", "enculer",
    "batard", "bâtard", "pute", "nique", "niquer", "fdp", "ntm", "bordel", "chiant",
    "fuck", "fucking", "shit", "bitch", "asshole", "bastard", "dick", "cunt",
})


@dataclass(frozen=True)
class CaptionSettings:
    enabled: bool = False
    width: int = 32
    lines: int = 3
    uppercase: bool = False
    filter_profanity: bool = True
    keep_accents: bool = True
    center: bool = True


def load_settings(path: Path = CC_PATH) -> CaptionSettings:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CaptionSettings()
    if not isinstance(data, dict):
        return CaptionSettings()
    d = CaptionSettings()

    def flag(key: str, default: bool) -> bool:
        v = data.get(key)
        return v if isinstance(v, bool) else default

    width = data.get("width")
    lines = data.get("lines")
    lo, hi = WIDTH_RANGE
    return CaptionSettings(
        enabled=flag("enabled", d.enabled),
        width=width if isinstance(width, int) and not isinstance(width, bool)
        and lo <= width <= hi else d.width,
        lines=lines if lines in LINE_CHOICES and not isinstance(lines, bool) else d.lines,
        uppercase=flag("uppercase", d.uppercase),
        filter_profanity=flag("filter_profanity", d.filter_profanity),
        keep_accents=flag("keep_accents", d.keep_accents),
        center=flag("center", d.center))


def save_settings(settings: CaptionSettings, path: Path = CC_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _strip_accents(text: str) -> str:
    out = unicodedata.normalize("NFKD", text)
    return "".join(c for c in out if not unicodedata.combining(c))


def prepare(text: str, s: CaptionSettings) -> str:
    """Le texte tel qu'il partira : un seul espace, gros mots masqués,
    accents et majuscules selon les réglages."""
    text = " ".join(text.split())
    if s.filter_profanity:
        def mask(m: re.Match) -> str:
            word = m.group()
            bare = _strip_accents(word.casefold())
            return "***" if word.casefold() in _PROFANITY or bare in _PROFANITY else word
        text = re.sub(r"\w+", mask, text)
    if not s.keep_accents:
        text = _strip_accents(text)
    if s.uppercase:
        text = text.upper()
    return text


def wrap(text: str, width: int, lines: int) -> list[str]:
    """Découpe en morceaux de `lines` lignes d'au plus `width` caractères,
    lignes séparées par « \\n ». Coupure aux espaces ; un mot plus long que la
    ligne est coupé net plutôt que de déborder."""
    rows: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > width:
            if current:
                rows.append(current)
                current = ""
            rows.append(word[:width])
            word = word[width:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            rows.append(current)
            current = word
    if current:
        rows.append(current)
    return ["\n".join(rows[i:i + lines]) for i in range(0, len(rows), lines)]


def center_lines(chunk: str) -> str:
    """Centre chaque ligne sur l'écran en la précédant d'espaces.

    OBS place toujours le texte des CC à gauche ; le format n'offre pas
    d'alignement par l'API d'OBS. Avec ces espaces, une phrase courte reste au
    milieu et s'élargit vers les deux côtés en s'allongeant.
    """
    return "\n".join(" " * ((SCREEN_COLUMNS - len(row)) // 2) + row
                     for row in chunk.split("\n"))


def show_time(chunk: str, backlog: int) -> float:
    """Temps d'affichage d'un morceau. En retard sur la parole, on lit plus
    vite plutôt que d'accumuler un décalage croissant."""
    base = max(SHOW_MIN_S, min(SHOW_MAX_S, len(chunk) / READ_CHARS_PER_S))
    return 1.0 if backlog > BACKLOG_MAX else base


class CaptionQueue:
    """Reçoit les évènements des sous-titres, envoie les morceaux à leur heure.

    `send(chunk)` part vers OBS ; `wait_revise` vaut vrai en mode Précis.
    """

    def __init__(self, send: Callable[[str], None], settings: CaptionSettings,
                 wait_revise: bool = False,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._send = send
        self.settings = settings
        self.wait_revise = wait_revise
        self._clock = clock
        self._pending: dict[int, tuple[str, float]] = {}   # uid -> (texte, échéance)
        self._sent_uids: set[int] = set()
        self._queue: list[str] = []
        self._next_send = 0.0

    def on_text(self, kind: str, text: str, uid: int) -> None:
        if kind == "final":
            if self.wait_revise:
                self._pending[uid] = (text, self._clock() + REVISE_WAIT_S)
            else:
                self._enqueue(uid, text)
        elif kind == "revise":
            if uid in self._pending:
                del self._pending[uid]
                self._enqueue(uid, text)
            # Déjà envoyée sans correction : trop tard, le CC ne se corrige pas.

    def push(self, text: str) -> None:
        """Texte direct (phrase test) : pas de correction à attendre."""
        self._enqueue(-1, text)

    def _enqueue(self, uid: int, text: str) -> None:
        if uid >= 0:
            if uid in self._sent_uids:
                return
            self._sent_uids.add(uid)
            if len(self._sent_uids) > 256:
                self._sent_uids = {u for u in self._sent_uids if u >= uid - 128}
        s = self.settings
        for chunk in wrap(prepare(text, s), s.width, s.lines):
            self._queue.append(center_lines(chunk) if s.center else chunk)

    def tick(self) -> None:
        now = self._clock()
        for uid in sorted(u for u, (_t, due) in self._pending.items() if due <= now):
            text, _due = self._pending.pop(uid)
            self._enqueue(uid, text)
        if self._queue and now >= self._next_send:
            chunk = self._queue.pop(0)
            self._send(chunk)
            self._next_send = now + show_time(chunk, len(self._queue))

    def flush_pending(self) -> None:
        """Met en file les phrases qui attendaient leur correction : à
        l'arrêt, aucune correction ne viendra plus."""
        for uid in sorted(self._pending):
            self._enqueue(uid, self._pending[uid][0])
        self._pending.clear()

    def clear(self) -> None:
        self._pending.clear()
        self._queue.clear()
        self._next_send = 0.0

    @property
    def idle(self) -> bool:
        return not self._pending and not self._queue
