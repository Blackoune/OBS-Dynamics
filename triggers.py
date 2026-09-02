"""
triggers.py — Règles « raccourci clavier -> média affiché dans OBS ».

Chaque règle associe une combinaison de touches globale à un fichier média
(image, vidéo ou son) diffusé sur une source navigateur OBS dédiée.

Persistance : data/triggers.json, même contrat que GameStore (écriture
atomique via fichier temporaire, cache invalidé sur (mtime, taille)).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("obs_dynamics.triggers")

MEDIA_TYPES = ("image", "video", "sound")

# Extensions proposées dans le sélecteur de fichier, par type de média.
MEDIA_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"),
    "video": (".mp4", ".webm", ".mov", ".mkv"),
    "sound": (".mp3", ".ogg", ".wav", ".m4a"),
}

DEFAULT_DURATION_MS = 3000


@dataclass
class TriggerRule:
    id: str
    hotkey: str = ""                    # combo normalisé, ex. "ctrl+shift+a"
    media_type: str = "image"           # image | video | sound
    media_path: str = ""
    duration_ms: int = DEFAULT_DURATION_MS
    enabled: bool = True

    @property
    def is_complete(self) -> bool:
        """Une règle n'est active que si elle a une touche ET un média."""
        return bool(self.hotkey) and bool(self.media_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "hotkey": self.hotkey, "media_type": self.media_type,
            "media_path": self.media_path, "duration_ms": self.duration_ms,
            "enabled": self.enabled,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "TriggerRule":
        media_type = str(data.get("media_type", "image"))
        if media_type not in MEDIA_TYPES:
            logger.warning("Type de média inconnu %r, remplacé par 'image'.", media_type)
            media_type = "image"
        try:
            duration = int(data.get("duration_ms", DEFAULT_DURATION_MS))
        except (TypeError, ValueError):
            duration = DEFAULT_DURATION_MS
        return TriggerRule(
            id=str(data.get("id") or uuid.uuid4().hex),
            hotkey=str(data.get("hotkey", "")).strip().lower(),
            media_type=media_type,
            media_path=str(data.get("media_path", "")),
            duration_ms=max(100, min(60_000, duration)),
            enabled=bool(data.get("enabled", True)),
        )


class TriggerStore:
    """Persistance JSON locale. Les règles sont conservées dans l'ordre
    d'affichage : les nouvelles s'insèrent EN TÊTE de liste."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached: Optional[list[TriggerRule]] = None
        self._cache_stamp: Optional[tuple[float, int]] = None

    def _stamp(self) -> Optional[tuple[float, int]]:
        try:
            st = self._path.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def load(self) -> list[TriggerRule]:
        with self._lock:
            stamp = self._stamp()
            if self._cached is not None and stamp is not None and stamp == self._cache_stamp:
                return list(self._cached)
            if not self._path.exists():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                rules_raw = raw if isinstance(raw, list) else raw.get("triggers", [])
                rules = [TriggerRule.from_dict(r) for r in rules_raw]
            except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                logger.exception("triggers.json invalide.")
                return []
            self._cached, self._cache_stamp = list(rules), stamp
            return rules

    def save(self, rules: list[TriggerRule]) -> bool:
        with self._lock:
            try:
                payload = {"triggers": [r.to_dict() for r in rules]}
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._path)
                self._cached, self._cache_stamp = None, None
                return True
            except OSError:
                logger.exception("Échec sauvegarde triggers.json.")
                return False

    def add(self) -> TriggerRule:
        """Crée une règle vide EN TÊTE de liste et la retourne."""
        rule = TriggerRule(id=uuid.uuid4().hex)
        rules = self.load()
        rules.insert(0, rule)
        self.save(rules)
        return rule

    def upsert(self, rule: TriggerRule) -> bool:
        rules = self.load()
        for i, existing in enumerate(rules):
            if existing.id == rule.id:
                rules[i] = rule
                break
        else:
            rules.insert(0, rule)
        return self.save(rules)

    def delete(self, rule_id: str) -> bool:
        return self.save([r for r in self.load() if r.id != rule_id])

    def get(self, rule_id: str) -> Optional[TriggerRule]:
        return next((r for r in self.load() if r.id == rule_id), None)

    def conflicting(self, hotkey: str, exclude_id: str = "") -> Optional[TriggerRule]:
        """Retourne la règle utilisant déjà ce raccourci, s'il y en a une.
        Deux règles sur la même combinaison rendraient le déclenchement
        imprévisible — mieux vaut le signaler à la saisie."""
        if not hotkey:
            return None
        return next((r for r in self.load()
                     if r.hotkey == hotkey and r.id != exclude_id), None)
