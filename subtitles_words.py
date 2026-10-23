"""Banque de mots des sous-titres : pseudos, jeux, emotes propres au stream.

Aucun modèle ne connaît « Blackoune », « KEKW » ou le nom exact d'un jeu
sorti la semaine dernière. Chaque entrée donne l'écriture correcte, et
éventuellement comment le moteur l'entend (« kékéwé »). Le texte reconnu est
corrigé AVANT d'être affiché ou envoyé, donc partout à la fois : onglet,
widget OBS, sous-titres CC.

Deux façons de reconnaître un mot :
  - exacte, sans tenir compte des majuscules, des accents, des tirets et des
    apostrophes : « pokemon » devient « Pokémon » ;
  - approchée, pour les mots de 5 lettres et plus : « Elden Rin » ou
    « eldenring » deviennent « Elden Ring » sans qu'on ait listé ces variantes.
    Le seuil est haut (`FUZZY_MIN`) : un mot courant ne doit jamais être
    remplacé par un nom propre qui lui ressemble vaguement.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app_paths import DATA_DIR, logger

WORDS_PATH = DATA_DIR / "subtitles_words.json"
KINDS = ("name", "game", "emote", "other")
MAX_WORDS = 500
MAX_LEN = 60
FUZZY_MIN = 0.86          # ressemblance minimale (0 à 1) pour la reconnaissance approchée
FUZZY_MIN_CHARS = 5       # en dessous, seulement la reconnaissance exacte

_TOKEN = re.compile(r"[^\W_]+")      # lettres et chiffres ; « _ » sépare


@dataclass(frozen=True)
class Word:
    text: str                       # écriture correcte, affichée
    heard: tuple[str, ...] = ()     # façons dont le moteur l'entend
    kind: str = "other"


def _norm(s: str) -> str:
    """Minuscules, sans accents, tirets et apostrophes changés en espaces."""
    s = unicodedata.normalize("NFKD", s.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[-'’_]", " ", s).split())


def _clean(value: Any) -> str:
    """Une entrée de l'utilisateur, nettoyée : pas de saut de ligne ni de
    caractère de contrôle, longueur bornée."""
    if not isinstance(value, str):
        return ""
    # Un saut de ligne devient un espace : « Elden\nRing » reste deux mots.
    value = "".join(" " if unicodedata.category(c)[0] == "C" else c for c in value)
    return " ".join(value.split())[:MAX_LEN]


def from_dict(data: Any) -> list[Word]:
    """Liste valide à partir de n'importe quoi ; les entrées vides ou
    invalides sont ignorées, pas les autres."""
    if not isinstance(data, list):
        return []
    words: list[Word] = []
    for item in data[:MAX_WORDS]:
        if not isinstance(item, dict):
            continue
        text = _clean(item.get("text"))
        if not _norm(text):
            continue
        heard_raw = item.get("heard")
        heard = tuple(h for h in (_clean(x) for x in heard_raw[:20])
                      if _norm(h)) if isinstance(heard_raw, list) else ()
        kind = item.get("kind") if item.get("kind") in KINDS else "other"
        words.append(Word(text, heard, kind))
    return words


def to_dict(words: list[Word]) -> list[dict[str, Any]]:
    return [{"text": w.text, "heard": list(w.heard), "kind": w.kind} for w in words]


def load_words(path: Path = WORDS_PATH) -> list[Word]:
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError):
        logger.warning("Banque de mots illisible (%s) : ignorée.", path)
        return []


def save_words(words: list[Word], path: Path = WORDS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(to_dict(words), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class _Form:
    text: str                    # écriture correcte à mettre à la place
    tokens: tuple[str, ...]      # forme normalisée, mot par mot
    joined: str                  # même chose sans espaces


#: Ce qui peut séparer deux mots d'une même entrée : espaces, tirets,
#: apostrophes. Une ponctuation (« Elden. Ring ») sépare deux phrases.
_JOINER = re.compile(r"[\s\-'’_]*")


class Corrector:
    """Applique la banque de mots à un texte. Construit une fois par
    modification de la banque, appelé à chaque mot reconnu (~10 fois/s) :
    tout ce qui peut l'être est indexé ici, pas recalculé à chaque appel."""

    def __init__(self, words: list[Word]) -> None:
        self._exact: dict[tuple[str, ...], str] = {}     # forme -> écriture
        self._exact_joined: dict[str, str] = {}          # « eldenring » -> écriture
        self._fuzzy: dict[str, list[_Form]] = {}         # première lettre -> formes
        self._max_tokens = 1
        for w in words:
            for i, variant in enumerate((w.text, *w.heard)):
                tokens = tuple(_norm(variant).split())
                if not tokens:
                    continue
                joined = "".join(tokens)
                self._exact.setdefault(tokens, w.text)
                self._exact_joined.setdefault(joined, w.text)
                self._max_tokens = max(self._max_tokens, len(tokens))
                # Approché : seulement sur l'écriture correcte, assez longue.
                if i == 0 and len(joined) >= FUZZY_MIN_CHARS:
                    self._fuzzy.setdefault(joined[0], []).append(_Form(w.text, tokens, joined))

    def __bool__(self) -> bool:
        return bool(self._exact)

    def apply(self, text: str) -> str:
        if not self._exact or not text:
            return text
        units = [(m.start(), m.end(), _norm(m.group())) for m in _TOKEN.finditer(text)]
        taken = [False] * len(units)
        hits: list[tuple[int, int, str]] = []        # (premier mot, nb de mots, écriture)
        # Deux passes sur toute la phrase : l'exact d'abord, l'approché
        # seulement sur les mots restés libres. Sinon un rapprochement au mot i
        # pouvait avaler le mot i+1 qui, lui, correspondait exactement à une
        # autre entrée.
        for exact in (True, False):
            i = 0
            while i < len(units):
                hit = None if taken[i] else self._match(text, units, taken, i, exact)
                if hit is None:
                    i += 1
                    continue
                n, replacement = hit
                hits.append((i, n, replacement))
                for k in range(i, i + n):
                    taken[k] = True
                i += n
        out, last = [], 0
        for i, n, replacement in sorted(hits):
            start, end = units[i][0], units[i + n - 1][1]
            out.append(text[last:start])
            out.append(replacement)
            last = end
        out.append(text[last:])
        return "".join(out)

    def _windows(self, text: str, units, taken, i: int):
        """Fenêtres de mots libres et contigus commençant au mot `i`, de la
        plus longue à la plus courte."""
        windows = [(1, (units[i][2],))]
        for n in range(2, self._max_tokens + 1):
            k = i + n - 1
            if k >= len(units) or taken[k]:
                break
            if not _JOINER.fullmatch(text[units[k - 1][1]:units[k][0]]):
                break
            windows.append((n, windows[-1][1] + (units[k][2],)))
        return windows[::-1]

    def _match(self, text: str, units, taken, i: int, exact: bool):
        """Entrée qui commence au mot `i` : (nb de mots, écriture), ou None.

        L'approché ne compare que des fenêtres du MÊME nombre de mots que
        l'entrée (« elden rin ») ou un seul mot collé (« eldenring »), avec la
        même première lettre : sinon « à elden rin » ou « l'eldenring »
        ressemblaient assez à « eldenring » pour avaler le « à » ou le « l' ».
        """
        windows = self._windows(text, units, taken, i)
        if exact:
            for n, window in windows:
                found = self._exact.get(window)
                if found is None and n == 1:
                    found = self._exact_joined.get(window[0])
                if found is not None:
                    return n, found
            return None
        best = None
        for n, window in windows:
            joined = "".join(window)
            for f in self._fuzzy.get(joined[:1], ()):
                if n not in (1, len(f.tokens)):
                    continue
                if abs(len(joined) - len(f.joined)) > max(2, len(f.joined) // 4):
                    continue
                sm = SequenceMatcher(None, joined, f.joined)
                if sm.real_quick_ratio() < FUZZY_MIN or sm.quick_ratio() < FUZZY_MIN:
                    continue
                score = sm.ratio()
                if score >= FUZZY_MIN and (best is None or score > best[0]):
                    best = (score, n, f.text)
        return (best[1], best[2]) if best else None
