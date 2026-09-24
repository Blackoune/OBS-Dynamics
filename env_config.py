"""Lecture et écriture du .env utilisateur (%APPDATA%/OBS Dynamics/.env)."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import i18n
import secret_store
from overlay_server import DEFAULT_PORT as OVERLAY_DEFAULT_PORT

from app_paths import logger


# ============================================================================
# CONFIG .env — OBS_WS_HOST / OBS_WS_PORT / OBS_WS_PASSWORD / scan params / lang
# ============================================================================
@dataclass
class OBSConfig:
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    scan_interval_seconds: float = 2.0
    match_threshold: float = 0.8
    lang: str = i18n.DEFAULT_LANG
    rawg_api_key: str = ""
    overlay_port: int = OVERLAY_DEFAULT_PORT


ENV_KEYS = {
    "host": "OBS_WS_HOST",
    "port": "OBS_WS_PORT",
    "password": "OBS_WS_PASSWORD",
    "scan_interval_seconds": "OBS_SCAN_INTERVAL_SECONDS",
    "match_threshold": "OBS_MATCH_THRESHOLD",
    "lang": "OBS_APP_LANG",
    "rawg_api_key": "RAWG_API_KEY",
    "overlay_port": "OBS_OVERLAY_PORT",
}

# Les deux seuls identifiants du fichier. Ils ne subissent aucune conversion
# ni validation — les recenser évite d'empiler des `elif` identiques dans
# load() — mais ils sont **chiffrés au repos** : écrits par
# secret_store.encrypt(), relus par secret_store.decrypt(). Tout le reste du
# .env (hôte, port, seuils, langue) reste en clair : ce sont des réglages, pas
# des secrets, et les garder lisibles permet de dépanner le fichier à la main.
_SECRET_ENV_FIELDS = ("password", "rawg_api_key")
_SECRET_ENV_BY_KEY = {ENV_KEYS[field]: field for field in _SECRET_ENV_FIELDS}

#: Nom d'une variable qui porte un secret. Une ligne inconnue de cette forme
#: est conservée, mais chiffrée : aucun identifiant ne reste en clair, même
#: ajouté à la main ou laissé par une fonction retirée.
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSW|PWD")


def _cle_valeur(ligne: str) -> Optional[tuple[str, str]]:
    """(CLÉ, valeur) d'une ligne d'affectation, None pour le reste."""
    stripped = ligne.strip()
    if stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    return key.strip().upper(), value.strip().strip('"').strip("'")


def _a_nettoyer(key: str, value: str) -> bool:
    """Ligne étrangère qui porte un secret en clair, donc à chiffrer."""
    return (key not in ENV_KEYS.values() and bool(_SECRET_NAME.search(key))
            and bool(value) and not secret_store.is_encrypted(value))


class EnvConfigManager:
    """Lit/écrit les clés OBS_* dans .env. Préserve toute autre ligne existante."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Cache invalidé par (mtime, taille) : ScanWorker appelle load() à
        # chaque cycle (toutes les 0.5-2s), relire et reparser le .env à
        # chaque fois est du pur gaspillage d'I/O.
        self._cached: Optional[OBSConfig] = None
        self._cache_stamp: Optional[tuple[float, int]] = None

    def _stamp(self) -> Optional[tuple[float, int]]:
        try:
            st = self._path.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def load(self) -> OBSConfig:
        with self._lock:
            stamp = self._stamp()
            if self._cached is not None and stamp is not None and stamp == self._cache_stamp:
                return replace(self._cached)  # copie : l'appelant peut muter sans polluer le cache
            cfg = OBSConfig()
            if not self._path.exists():
                return cfg
            try:
                for line in self._path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    key, _, value = stripped.partition("=")
                    key = key.strip().upper()
                    value = value.strip().strip('"').strip("'")
                    secret_field = _SECRET_ENV_BY_KEY.get(key)
                    if secret_field is not None:
                        setattr(cfg, secret_field, secret_store.decrypt(value))
                    elif key == ENV_KEYS["host"]:
                        cfg.host = value
                    elif key == ENV_KEYS["port"]:
                        try:
                            cfg.port = int(value)
                        except ValueError:
                            logger.warning("OBS_WS_PORT invalide dans .env.")
                    elif key == ENV_KEYS["scan_interval_seconds"]:
                        try:
                            cfg.scan_interval_seconds = max(0.5, float(value))
                        except ValueError:
                            pass
                    elif key == ENV_KEYS["match_threshold"]:
                        try:
                            cfg.match_threshold = min(1.0, max(0.0, float(value)))
                        except ValueError:
                            pass
                    elif key == ENV_KEYS["lang"]:
                        if value in i18n.SUPPORTED_LANGS:
                            cfg.lang = value
                    elif key == ENV_KEYS["overlay_port"]:
                        try:
                            cfg.overlay_port = int(value)
                        except ValueError:
                            logger.warning("OBS_OVERLAY_PORT invalide dans .env.")
            except OSError:
                logger.exception("Lecture .env échouée, valeurs par défaut utilisées.")
                return cfg
            self._cached, self._cache_stamp = replace(cfg), stamp
            return cfg

    def encrypt_secrets_at_rest(self) -> bool:
        """Met les identifiants du `.env` au format de chiffrement courant.

        Deux cas : une valeur en clair d'avant le chiffrement, et un blob
        `enc:v1:` scellé sans entropie secondaire.

        Sans cet appel, un mot de passe déjà saisi ne serait chiffré qu'au
        prochain enregistrement depuis l'onglet Paramètres — donc peut-être
        jamais. Le fichier n'est réécrit **que** s'il reste quelque chose à
        chiffrer : appelée à chaque démarrage, cette méthode ne doit ni
        remuer le disque ni produire un nouveau blob à chaque lancement.

        Retourne True si le fichier a été réécrit.
        """
        if not secret_store.available() or not self._path.exists():
            return False
        try:
            lignes = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            logger.exception("Lecture .env échouée avant chiffrement.")
            return False

        for ligne in lignes:
            paire = _cle_valeur(ligne)
            if paire is None:
                continue
            key, value = paire
            if ((key in _SECRET_ENV_BY_KEY and secret_store.needs_rewrite(value))
                    or _a_nettoyer(key, value)):
                break
        else:
            return False        # tout est au format courant, ou rien de saisi

        if not self.save(self.load()):
            return False
        logger.info("Identifiants du .env chiffrés au repos (DPAPI).")
        return True

    def save(self, cfg: OBSConfig) -> bool:
        with self._lock:
            try:
                updates = {
                    ENV_KEYS["host"]: cfg.host,
                    ENV_KEYS["port"]: str(cfg.port),
                    ENV_KEYS["password"]: secret_store.encrypt(cfg.password),
                    ENV_KEYS["scan_interval_seconds"]: str(cfg.scan_interval_seconds),
                    ENV_KEYS["match_threshold"]: str(cfg.match_threshold),
                    ENV_KEYS["lang"]: cfg.lang,
                    ENV_KEYS["rawg_api_key"]: secret_store.encrypt(cfg.rawg_api_key),
                    ENV_KEYS["overlay_port"]: str(cfg.overlay_port),
                }
                seen = dict.fromkeys(updates, False)
                lines: list[str] = []
                if self._path.exists():
                    for line in self._path.read_text(encoding="utf-8").splitlines():
                        paire = _cle_valeur(line)
                        if paire is not None:
                            key, value = paire
                            if key in updates:
                                lines.append(f"{key}={updates[key]}")
                                seen[key] = True
                                continue
                            if _a_nettoyer(key, value):
                                lines.append(f"{key}={secret_store.encrypt(value)}")
                                continue
                        lines.append(line)
                for key, present in seen.items():
                    if not present:
                        lines.append(f"{key}={updates[key]}")
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(self._path)
                self._cached, self._cache_stamp = None, None  # invalide le cache
                return True
            except OSError:
                logger.exception("Échec sauvegarde .env.")
                return False
