"""
i18n.py — Loader centralisé pour OBS Dynamics.
Charge i18n.json (racine du projet) une seule fois, expose t(key, **kwargs).
Fallback: clé manquante -> retourne la clé elle-même (jamais de crash UI).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("obs_dynamics.i18n")

DEFAULT_LANG = "fr"
SUPPORTED_LANGS = ("fr", "en", "es")


def _get_base_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class I18n:
    """Loader i18n avec support de rechangement de langue à chaud : les vues
    qui s'enregistrent via on_change() sont notifiées à chaque set_lang()
    réussi, ce qui permet de reconstruire leurs libellés sans redémarrage."""

    def __init__(self, path: Path | None = None, lang: str = DEFAULT_LANG) -> None:
        self._path = path or (_get_base_path() / "i18n.json")
        self._lang = lang
        self._data: dict[str, dict[str, str]] = {}
        self._listeners: list[Callable[[str], None]] = []
        self.reload()

    def reload(self) -> None:
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Échec chargement i18n.json (%s): %s", self._path, exc)
            self._data = {DEFAULT_LANG: {}}

    def set_lang(self, lang: str) -> bool:
        """Change la langue active et notifie les listeners enregistrés.
        Retourne True si le changement a été appliqué (langue connue)."""
        if lang not in self._data:
            logger.warning("Langue '%s' absente de i18n.json, conservée: '%s'.", lang, self._lang)
            return False
        if lang == self._lang:
            return True
        self._lang = lang
        for callback in list(self._listeners):
            try:
                callback(lang)
            except Exception:
                logger.exception("Erreur dans un listener i18n lors du changement de langue.")
        return True

    @property
    def current_lang(self) -> str:
        return self._lang

    def on_change(self, callback: Callable[[str], None]) -> None:
        """Enregistre un callback(lang: str) appelé à chaque set_lang() réussi
        (rechargement réactif des libellés sans redémarrage de l'app)."""
        self._listeners.append(callback)

    def off_change(self, callback: Callable[[str], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def available_langs(self) -> list[str]:
        return list(self._data.keys())

    def t(self, key: str, **kwargs: Any) -> str:
        table = self._data.get(self._lang) or self._data.get(DEFAULT_LANG, {})
        template = table.get(key)
        if template is None:
            logger.debug("Clé i18n manquante: '%s'", key)
            return key
        if not kwargs:
            return template
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            logger.debug("Placeholder manquant pour la clé '%s' (kwargs=%s)", key, kwargs)
            return template


_instance: I18n | None = None


def init(path: Path | None = None, lang: str = DEFAULT_LANG) -> I18n:
    global _instance
    _instance = I18n(path=path, lang=lang)
    return _instance


def t(key: str, **kwargs: Any) -> str:
    if _instance is None:
        init()
    return _instance.t(key, **kwargs)  # type: ignore[union-attr]


def set_lang(lang: str) -> bool:
    if _instance is None:
        init()
    return _instance.set_lang(lang)  # type: ignore[union-attr]


def current_lang() -> str:
    if _instance is None:
        init()
    return _instance.current_lang  # type: ignore[union-attr]


def on_change(callback: Callable[[str], None]) -> None:
    if _instance is None:
        init()
    _instance.on_change(callback)  # type: ignore[union-attr]


def off_change(callback: Callable[[str], None]) -> None:
    if _instance is None:
        init()
    _instance.off_change(callback)  # type: ignore[union-attr]
