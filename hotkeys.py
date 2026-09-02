"""
hotkeys.py — Hotkeys globales (pynput) pour forcer manuellement un état de jeu.

Permet de basculer une scène OBS au clavier même quand la détection visuelle
se trompe ou qu'aucune image de référence n'est configurée.

La table touche -> état est persistée dans data/hotkeys.json et modifiable
sans toucher au code :

    {"f1": "in_game", "f2": "menu", "f3": "inactive"}

NOTE pynput : `Key.f1.name` vaut "f1" en MINUSCULE. L'ancienne implémentation
(_backup) indexait sur "F1" en majuscule, donc aucune touche ne déclenchait
jamais rien. Toutes les clés sont normalisées en minuscule ici.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("obs_dynamics.hotkeys")

DEFAULT_BINDINGS: dict[str, str] = {
    "f1": "in_game",
    "f2": "menu",
    "f3": "inactive",
}

VALID_STATES = {"inactive", "active", "menu", "in_game"}


def load_bindings(path: Path) -> dict[str, str]:
    """Lit data/hotkeys.json. Tolère l'ancien format (liste vide) et tout
    fichier corrompu en retombant sur les valeurs par défaut."""
    if not path.exists():
        return dict(DEFAULT_BINDINGS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("hotkeys.json illisible, bindings par défaut utilisés.")
        return dict(DEFAULT_BINDINGS)

    # Ancien format hérité de l'archi web : [] -> on réinitialise.
    if not isinstance(raw, dict) or not raw:
        return dict(DEFAULT_BINDINGS)

    bindings: dict[str, str] = {}
    for key, state in raw.items():
        if isinstance(key, str) and state in VALID_STATES:
            bindings[key.strip().lower()] = state
        else:
            logger.warning("Binding ignoré (touche ou état invalide) : %r -> %r", key, state)
    return bindings or dict(DEFAULT_BINDINGS)


def save_bindings(path: Path, bindings: dict[str, str]) -> bool:
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(bindings, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        logger.exception("Écriture de hotkeys.json échouée.")
        return False


class HotkeyManager:
    """Écoute le clavier système en arrière-plan et appelle
    `on_hotkey(state)` quand une touche liée est pressée.

    pynput est optionnel : s'il n'est pas installé, le manager se désactive
    proprement au lieu de faire planter l'application au démarrage.
    """

    def __init__(self, on_hotkey: Callable[[str], None],
                 bindings: Optional[dict[str, str]] = None) -> None:
        self._on_hotkey = on_hotkey
        self._bindings = dict(bindings) if bindings else dict(DEFAULT_BINDINGS)
        self._listener: Optional[object] = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._listener is not None

    @property
    def bindings(self) -> dict[str, str]:
        return dict(self._bindings)

    def set_bindings(self, bindings: dict[str, str]) -> None:
        with self._lock:
            self._bindings = dict(bindings)

    def start(self) -> bool:
        """Démarre l'écoute. Retourne False si pynput est absent ou si l'OS
        refuse l'accès clavier global (pas fatal : l'app tourne sans)."""
        if self._listener is not None:
            return True
        try:
            from pynput import keyboard
        except ImportError:
            logger.warning("pynput absent — hotkeys globales désactivées "
                           "(pip install pynput pour les activer).")
            return False
        try:
            listener = keyboard.Listener(on_press=self._on_press)
            listener.daemon = True
            listener.start()
        except Exception:
            logger.exception("Démarrage de l'écoute clavier impossible — hotkeys désactivées.")
            return False
        self._listener = listener
        logger.info("Hotkeys actives : %s",
                    ", ".join(f"{k.upper()}={v}" for k, v in sorted(self._bindings.items())))
        return True

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Arrêt de l'écoute clavier échoué (ignoré).", exc_info=True)
            logger.info("Hotkeys arrêtées.")

    @staticmethod
    def _key_name(key: object) -> str:
        """Normalise une touche pynput en identifiant minuscule stable.
        Key.f1 -> "f1" ; KeyCode(char='k') -> "k"."""
        name = getattr(key, "name", None)          # touches spéciales (f1, esc, ...)
        if isinstance(name, str):
            return name.lower()
        char = getattr(key, "char", None)          # touches caractères
        if isinstance(char, str) and char:
            return char.lower()
        return ""

    def _on_press(self, key: object) -> None:
        # Exécuté sur le thread pynput : on ne fait que router, jamais de Tk ici.
        try:
            name = self._key_name(key)
            if not name:
                return
            with self._lock:
                state = self._bindings.get(name)
            if state is not None:
                self._on_hotkey(state)
        except Exception:
            logger.debug("Erreur de traitement d'une touche.", exc_info=True)
