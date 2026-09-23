"""
hotkeys.py — Hotkeys globales (pynput) pour forcer manuellement un état de jeu.

Permet de basculer une scène OBS au clavier même quand la détection visuelle
se trompe ou qu'aucune image de référence n'est configurée.

La table raccourci -> état est persistée dans data/hotkeys.json et modifiable
sans toucher au code. Une touche seule comme une combinaison sont acceptées ;
l'ordre des modificateurs n'a pas d'importance, il est normalisé à la lecture :

    {"f1": "in_game", "ctrl+f2": "menu", "ctrl+shift+f3": "inactive"}

NOTE pynput : `Key.f1.name` vaut "f1" en MINUSCULE. L'ancienne implémentation
(_backup) indexait sur "F1" en majuscule, donc aucune touche ne déclenchait
jamais rien. Toutes les clés sont normalisées en minuscule ici.
"""
from __future__ import annotations

import ctypes
import json
import logging
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("obs_dynamics.hotkeys")

#: Codes de touche virtuels Windows des modificateurs.
_VK_MODIFIERS: dict[str, tuple[int, ...]] = {
    "ctrl": (0x11,), "shift": (0x10,), "alt": (0x12,), "cmd": (0x5B, 0x5C),
}


def _key_down(vk: int) -> Optional[bool]:
    """État réel d'une touche selon Windows ; None hors Windows ou en échec."""
    if sys.platform != "win32":
        return None
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return None


def _drop_released_modifiers(pressed: set[str]) -> None:
    """Retire de `pressed` les modificateurs que Windows dit relâchés.

    Win+L, une fenêtre UAC ou le bureau sécurisé avalent l'événement de
    relâchement : Ctrl restait « enfoncé » pour pynput, et F5 seul
    déclenchait ensuite Ctrl+F5. On ne fait que RETIRER — jamais ajouter —
    pour ne pas inventer une combinaison que l'utilisateur n'a pas faite.
    """
    for mod in list(pressed):
        codes = _VK_MODIFIERS.get(mod, ())
        if codes and all(_key_down(code) is False for code in codes):
            pressed.discard(mod)

DEFAULT_BINDINGS: dict[str, str] = {
    "f1": "in_game",
    "f2": "menu",
    "f3": "inactive",
}

VALID_STATES = {"inactive", "active", "menu", "in_game"}


def normalize_combo(combo: str) -> str:
    """Met un raccourci écrit à la main sous forme canonique.

    "Ctrl+Shift+F1", "shift+ctrl+f1" et "CTRL + SHIFT + F1" doivent tous
    désigner le même raccourci, sinon deux règles identiques ne se
    reconnaîtraient pas et la table de correspondance raterait la touche.
    Retourne "" si le raccourci n'a pas exactement une touche principale.
    """
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return ""
    mods = {canonical_modifier(p) for p in parts if is_modifier(p)}
    main = [p for p in parts if not is_modifier(p)]
    if len(main) != 1:
        return ""          # "ctrl+alt" seul, ou "a+b" : pas un raccourci valide
    return build_combo(mods, main[0])


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
        combo = normalize_combo(key) if isinstance(key, str) else ""
        if combo and state in VALID_STATES:
            bindings[combo] = state
        else:
            logger.warning("Binding ignoré (raccourci ou état invalide) : %r -> %r", key, state)
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
        self._pressed: set[str] = set()
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
            listener = keyboard.Listener(on_press=self._on_press,
                                         on_release=self._on_release)
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
        with self._lock:
            self._pressed.clear()   # sinon un modificateur resté "enfoncé" au
                                    # redémarrage fausserait toutes les combinaisons
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
            if is_modifier(name):
                with self._lock:
                    self._pressed.add(canonical_modifier(name))
                return
            with self._lock:
                _drop_released_modifiers(self._pressed)
                # Sans modificateur enfoncé, build_combo() renvoie la touche
                # nue : un hotkeys.json historique {"f1": ...} marche tel quel.
                state = self._bindings.get(build_combo(set(self._pressed), name))
            if state is not None:
                self._on_hotkey(state)
        except Exception:
            logger.debug("Erreur de traitement d'une touche.", exc_info=True)

    def _on_release(self, key: object) -> None:
        try:
            name = self._key_name(key)
            if name and is_modifier(name):
                with self._lock:
                    self._pressed.discard(canonical_modifier(name))
        except Exception:
            logger.debug("Erreur au relâchement d'une touche.", exc_info=True)


# ============================================================================
# COMBINAISONS (Ctrl+Shift+A) — pour les déclencheurs de médias
# ============================================================================

# Familles de modificateurs : pynput distingue gauche et droite (ctrl_l /
# ctrl_r), sans intérêt pour un raccourci utilisateur. On normalise.
_MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd",
}

# Ordre canonique : "ctrl+shift+a" et "shift+ctrl+a" doivent produire la même
# chaîne, sinon deux règles identiques ne se reconnaîtraient pas entre elles.
_MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")


def is_modifier(key_name: str) -> bool:
    return key_name in _MODIFIER_ALIASES


def canonical_modifier(key_name: str) -> str:
    return _MODIFIER_ALIASES.get(key_name, key_name)


def build_combo(modifiers: set[str], key_name: str) -> str:
    """Assemble un identifiant de combinaison stable et ordonné."""
    ordered = [m for m in _MODIFIER_ORDER if m in modifiers]
    return "+".join(ordered + [key_name])


def format_combo(combo: str) -> str:
    """Rendu lisible pour l'interface : "ctrl+shift+a" -> "Ctrl + Shift + A"."""
    if not combo:
        return ""
    pretty = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Cmd"}
    parts = [pretty.get(p, p.upper() if len(p) == 1 else p.capitalize())
             for p in combo.split("+")]
    return " + ".join(parts)


class ComboListener:
    """Écoute globale de combinaisons de touches.

    Suit l'état des modificateurs enfoncés et, à chaque touche non-modificateur,
    calcule la combinaison courante puis appelle `on_combo(combo)`.

    Une seule instance suffit pour toutes les règles : la résolution
    combinaison -> règle se fait côté appelant, ce qui évite d'ouvrir un
    listener clavier par raccourci configuré.
    """

    def __init__(self, on_combo: Callable[[str], None],
                 on_release: Optional[Callable[[str], None]] = None) -> None:
        self._on_combo = on_combo
        self._on_release = on_release
        self._listener: Optional[object] = None
        self._pressed: set[str] = set()
        # touche principale -> combinaison déclenchée. Ancrer sur la touche
        # principale (et non sur la combinaison complète) permet de relâcher
        # correctement même si l'utilisateur lâche Ctrl avant la lettre.
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._listener is not None

    def start(self) -> bool:
        if self._listener is not None:
            return True
        try:
            from pynput import keyboard
        except ImportError:
            logger.warning("pynput absent — déclencheurs clavier désactivés.")
            return False
        try:
            listener = keyboard.Listener(on_press=self._on_press, on_release=self._handle_release)
            listener.daemon = True
            listener.start()
        except Exception:
            logger.exception("Écoute des combinaisons impossible.")
            return False
        self._listener = listener
        logger.info("Écoute des combinaisons de touches active.")
        return True

    def stop(self) -> None:
        listener = self._listener
        self._listener = None
        with self._lock:
            self._pressed.clear()
            self._active.clear()
        if listener is not None:
            try:
                listener.stop()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Arrêt de l'écoute des combinaisons échoué.", exc_info=True)

    def _on_press(self, key: object) -> None:
        try:
            name = HotkeyManager._key_name(key)
            if not name:
                return
            if is_modifier(name):
                with self._lock:
                    self._pressed.add(canonical_modifier(name))
                return
            with self._lock:
                if name in self._active:
                    # Windows répète l'événement press tant que la touche est
                    # maintenue : sans ce garde, un maintien enverrait des
                    # dizaines de déclenchements par seconde.
                    return
                _drop_released_modifiers(self._pressed)
                mods = set(self._pressed)
                combo = build_combo(mods, name)
                self._active[name] = combo
            self._on_combo(combo)
        except Exception:
            logger.debug("Erreur de traitement d'une combinaison.", exc_info=True)

    def _handle_release(self, key: object) -> None:
        try:
            name = HotkeyManager._key_name(key)
            if not name:
                return
            if is_modifier(name):
                with self._lock:
                    self._pressed.discard(canonical_modifier(name))
                return
            with self._lock:
                combo = self._active.pop(name, None)
            if combo is not None and self._on_release is not None:
                self._on_release(combo)
        except Exception:
            logger.debug("Erreur au relâchement d'une touche.", exc_info=True)


class ComboRecorder:
    """Capture UNE combinaison puis s'arrête — alimente le bouton
    « Cliquer pour enregistrer la combinaison » de l'interface.

    `on_captured(combo)` est appelé depuis le thread pynput : l'appelant doit
    repasser sur le thread UI (post_ui) avant de toucher un widget.
    """

    def __init__(self, on_captured: Callable[[str], None]) -> None:
        self._on_captured = on_captured
        self._listener: Optional[object] = None
        self._pressed: set[str] = set()
        self._done = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> bool:
        try:
            from pynput import keyboard
        except ImportError:
            logger.warning("pynput absent — capture de raccourci impossible.")
            return False
        try:
            listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            listener.daemon = True
            listener.start()
        except Exception:
            logger.exception("Capture de raccourci impossible.")
            return False
        self._listener = listener
        return True

    def cancel(self) -> None:
        self._done.set()
        self._stop_listener()

    def _stop_listener(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()  # type: ignore[attr-defined]
            except Exception:
                logger.debug("Arrêt du recorder échoué.", exc_info=True)

    def _on_press(self, key: object) -> None:
        if self._done.is_set():
            return
        try:
            name = HotkeyManager._key_name(key)
            if not name:
                return
            if name == "esc":                      # annulation explicite
                self._done.set()
                self._stop_listener()
                self._on_captured("")
                return
            if is_modifier(name):
                with self._lock:
                    self._pressed.add(canonical_modifier(name))
                return
            with self._lock:
                _drop_released_modifiers(self._pressed)
                mods = set(self._pressed)
            combo = build_combo(mods, name)
            self._done.set()
            self._stop_listener()
            self._on_captured(combo)
        except Exception:
            logger.debug("Erreur pendant la capture.", exc_info=True)

    def _on_release(self, key: object) -> None:
        try:
            name = HotkeyManager._key_name(key)
            if name and is_modifier(name):
                with self._lock:
                    self._pressed.discard(canonical_modifier(name))
        except Exception:
            pass
