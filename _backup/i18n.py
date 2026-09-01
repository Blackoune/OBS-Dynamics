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
from typing import Any

logger = logging.getLogger("obs_dynamics.i18n")

DEFAULT_LANG = "fr"


def _get_base_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class I18n:
    def __init__(self, path: Path | None = None, lang: str = DEFAULT_LANG) -> None:
        self._path = path or (_get_base_path() / "i18n.json")
        self._lang = lang
        self._data: dict[str, dict[str, str]] = {}
        self.reload()

    def reload(self) -> None:
        try:
            self._data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Échec chargement i18n.json (%s): %s", self._path, exc)
            self._data = {DEFAULT_LANG: {}}

    def set_lang(self, lang: str) -> None:
        if lang in self._data:
            self._lang = lang
        else:
            logger.warning("Langue '%s' absente de i18n.json, conservée: '%s'.", lang, self._lang)

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


def set_lang(lang: str) -> None:
    if _instance is None:
        init()
    _instance.set_lang(lang)  # type: ignore[union-attr]
