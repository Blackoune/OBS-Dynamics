"""Bibliothèque de jeux : scan Steam, modèle Game, persistance atomique."""
from __future__ import annotations

import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app_paths import logger

try:
    import winreg  # Windows uniquement — absent sur Linux/Mac, géré en aval
except ImportError:
    winreg = None  # type: ignore[assignment]


# ============================================================================
# STEAM SCANNER — bibliothèques locales, sans lancer Steam
# ============================================================================
class SteamScanner:
    """Localise l'installation Steam via le registre Windows, parcourt toutes
    les bibliothèques déclarées (libraryfolders.vdf) et lit chaque
    appmanifest_*.acf pour lister les jeux réellement installés en local.
    Parsing par regex ciblé (format Valve KeyValue): suffisant et robuste
    pour ces deux types de fichiers sans dépendance externe."""

    _PATH_RE = re.compile(r'"path"\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
    _APPID_RE = re.compile(r'"appid"\s*"(\d+)"', re.IGNORECASE)
    _NAME_RE = re.compile(r'"name"\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
    _INSTALLDIR_RE = re.compile(r'"installdir"\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE)

    @staticmethod
    def _unescape(value: str) -> str:
        return value.replace('\\\\', '\\').replace('\\"', '"')

    def find_steam_root(self) -> Optional[Path]:
        if winreg is not None:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                    value, _ = winreg.QueryValueEx(key, "SteamPath")
                    path = Path(value)
                    if path.exists():
                        return path
            except OSError:
                logger.debug("Clé de registre Steam introuvable, tentative chemins par défaut.")
        for candidate in (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")):
            if candidate.exists():
                return candidate
        return None

    def find_library_paths(self, steam_root: Path) -> list[Path]:
        libraries = [steam_root]
        vdf_path = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf_path.exists():
            try:
                content = vdf_path.read_text(encoding="utf-8", errors="ignore")
                for match in self._PATH_RE.finditer(content):
                    lib_path = Path(self._unescape(match.group(1)))
                    if lib_path.exists() and lib_path not in libraries:
                        libraries.append(lib_path)
            except OSError:
                logger.exception("Lecture libraryfolders.vdf échouée.")
        return libraries

    def scan_installed_games(self) -> list[dict[str, str]]:
        """Retourne [{appid, name, install_dir}] pour tous les jeux Steam
        installés localement (dossiers appmanifest_*.acf de chaque bibliothèque)."""
        steam_root = self.find_steam_root()
        if steam_root is None:
            logger.warning("Installation Steam introuvable sur cette machine.")
            return []

        games: list[dict[str, str]] = []
        for library in self.find_library_paths(steam_root):
            steamapps = library / "steamapps"
            if not steamapps.is_dir():
                continue
            for manifest in steamapps.glob("appmanifest_*.acf"):
                try:
                    content = manifest.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                appid_m = self._APPID_RE.search(content)
                name_m = self._NAME_RE.search(content)
                installdir_m = self._INSTALLDIR_RE.search(content)
                if not (appid_m and name_m and installdir_m):
                    continue
                install_dir = steamapps / "common" / self._unescape(installdir_m.group(1))
                games.append({
                    "appid": appid_m.group(1),
                    "name": self._unescape(name_m.group(1)),
                    "install_dir": str(install_dir),
                })
        return games


# ============================================================================
# STORE JEUX — data/games.json
# ============================================================================
@dataclass
class Game:
    id: str
    name: str
    source: str  # "steam" | "manual"
    active_match: str  # dossier d'install (steam) OU nom de l'exe (manual)
    appid: str = ""
    menu_images: list[str] = field(default_factory=list)
    ingame_images: list[str] = field(default_factory=list)
    obs_scene_menu: str = ""
    obs_scene_ingame: str = ""
    # Validation du cadrage automatique, par image de référence :
    #   {chemin: {"stamp": "<mtime>:<taille>", "excluded": [[fx, fy], ...]}}
    # `stamp` sert à redemander une validation UNIQUEMENT quand le fichier
    # change sur le disque ; sans lui la fenêtre de contrôle se rouvrirait à
    # chaque lancement. Les positions exclues sont relatives, donc stables même
    # si la sélection est recalculée.
    patch_reviews: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "source": self.source,
            "active_match": self.active_match, "appid": self.appid,
            "menu_images": self.menu_images, "ingame_images": self.ingame_images,
            "obs_scene_menu": self.obs_scene_menu, "obs_scene_ingame": self.obs_scene_ingame,
            "patch_reviews": self.patch_reviews,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "Game":
        return Game(
            id=str(data.get("id") or uuid.uuid4().hex),
            name=str(data.get("name", "?")),
            source=str(data.get("source", "manual")),
            active_match=str(data.get("active_match", "")),
            appid=str(data.get("appid", "")),
            menu_images=[str(p) for p in data.get("menu_images", [])],
            ingame_images=[str(p) for p in data.get("ingame_images", [])],
            obs_scene_menu=str(data.get("obs_scene_menu", "")),
            obs_scene_ingame=str(data.get("obs_scene_ingame", "")),
            # Absent des games.json antérieurs : un dict vide signifie « jamais
            # validé », ce qui déclenchera simplement une première validation.
            patch_reviews=dict(data.get("patch_reviews") or {}),
        )


class GameStore:
    """Persistance JSON locale (aucun réseau). Écriture atomique."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        # Même motif que EnvConfigManager : load() est appelé à chaque cycle
        # de scan ET plusieurs fois par rendu de la grille.
        self._cached: Optional[list[Game]] = None
        self._cache_stamp: Optional[tuple[float, int]] = None

    def _stamp(self) -> Optional[tuple[float, int]]:
        try:
            st = self._path.stat()
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def load(self) -> list[Game]:
        with self._lock:
            stamp = self._stamp()
            if self._cached is not None and stamp is not None and stamp == self._cache_stamp:
                return list(self._cached)  # copie de liste : pas de mutation croisée
            if not self._path.exists():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                # Compat rétro : anciennes versions écrivaient un tableau brut
                # ([...]) au lieu du format {"games": [...]}. On accepte les
                # deux ; save() réécrit toujours au format canonique ensuite.
                games_raw = raw if isinstance(raw, list) else raw.get("games", [])
                games = [Game.from_dict(g) for g in games_raw]
            except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                logger.exception("games.json invalide.")
                return []
            self._cached, self._cache_stamp = list(games), stamp
            return games

    def save(self, games: list[Game]) -> bool:
        with self._lock:
            try:
                payload = {"games": [g.to_dict() for g in games]}
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._path)
                self._cached, self._cache_stamp = None, None  # invalide le cache
                return True
            except OSError:
                logger.exception("Échec sauvegarde games.json.")
                return False

    def upsert(self, game: Game) -> bool:
        games = [g for g in self.load() if g.id != game.id]
        games.append(game)
        return self.save(games)

    def delete(self, game_id: str) -> bool:
        games = [g for g in self.load() if g.id != game_id]
        return self.save(games)

    def import_steam_games(self, steam_games: list[dict[str, str]]) -> int:
        """Fusionne par appid: n'écrase jamais la config (images/scènes) déjà
        définie par l'utilisateur pour un jeu déjà présent."""
        existing = self.load()
        existing_appids = {g.appid for g in existing if g.source == "steam" and g.appid}
        added = 0
        for sg in steam_games:
            if sg["appid"] in existing_appids:
                continue
            existing.append(Game(
                id=uuid.uuid4().hex, name=sg["name"], source="steam",
                active_match=sg["install_dir"], appid=sg["appid"],
            ))
            added += 1
        if added:
            self.save(existing)
        return added
