"""
OBS Dynamics — Application autonome tout-en-un.
GUI (CustomTkinter) + scan bibliothèques Steam locales + détection processus/visuelle
(OpenCV) + contrôle OBS WebSocket v5 (simpleobsws), tout en threads locaux.
AUCUNE dépendance HTTP/serveur web, AUCUN PowerShell requis côté utilisateur final.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Optional

# --- DPI awareness Windows : DOIT être fait AVANT d'importer customtkinter,
# sinon CTk calcule ses tailles de widgets sur un facteur d'échelle incorrect
# et la fenêtre s'ouvre plus petite que son contenu (sidebar qui déborde sur
# le dashboard, texte de bouton coupé, etc.) ---
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass  # plateforme/version Windows sans cette API — dégradation silencieuse

import customtkinter as ctk
import cv2
import numpy as np
import psutil
import simpleobsws
from PIL import Image, ImageGrab

try:
    import winreg  # Windows uniquement — absent sur Linux/Mac, géré en aval
except ImportError:
    winreg = None  # type: ignore[assignment]

# ============================================================================
# CHEMINS / CONSTANTES
# ============================================================================
def get_base_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


BASE_DIR = get_base_path()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
ENV_PATH = BASE_DIR / ".env"
GAMES_PATH = DATA_DIR / "games.json"
ASSETS_DIR = BASE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"
LOG_PATH = DATA_DIR / "obs_dynamics.log"
I18N_PATH = BASE_DIR / "i18n.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("obs_dynamics")

# --- i18n : import + init AVANT toute construction de widget CTk ---
import i18n
i18n.init(path=I18N_PATH)
from i18n import t

# ============================================================================
# PALETTE — thème AAA violet sombre (Steam/Discord/Spotify inspired)
# ============================================================================
COL_BG = "#0F0C1B"
COL_BG_GRADIENT_TOP = "#151024"
COL_SIDEBAR = "#120E20"
COL_CARD = "#1A1530"
COL_CARD_HOVER = "#221B3D"
COL_BORDER = "#2A2145"
COL_BORDER_ACCENT = "#A855F7"
COL_ACCENT = "#A855F7"
COL_ACCENT_HOVER = "#9333EA"
COL_ACCENT_SOFT = "#7C3AED"
COL_TEXT = "#F3F0FA"
COL_TEXT_MUTED = "#9B93B5"
COL_GREEN = "#22C55E"
COL_YELLOW = "#F1C40F"
COL_RED = "#EF4444"
COL_BADGE_BG_INACTIVE = "#3A1420"
COL_BADGE_BG_ACTIVE = "#123A22"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

FONT_FAMILY = "Segoe UI" if sys.platform == "win32" else "Inter"


def font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


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


ENV_KEYS = {
    "host": "OBS_WS_HOST",
    "port": "OBS_WS_PORT",
    "password": "OBS_WS_PASSWORD",
    "scan_interval_seconds": "OBS_SCAN_INTERVAL_SECONDS",
    "match_threshold": "OBS_MATCH_THRESHOLD",
    "lang": "OBS_APP_LANG",
}


class EnvConfigManager:
    """Lit/écrit les clés OBS_* dans .env. Préserve toute autre ligne existante."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> OBSConfig:
        with self._lock:
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
                    if key == ENV_KEYS["host"]:
                        cfg.host = value
                    elif key == ENV_KEYS["port"]:
                        try:
                            cfg.port = int(value)
                        except ValueError:
                            logger.warning("OBS_WS_PORT invalide dans .env.")
                    elif key == ENV_KEYS["password"]:
                        cfg.password = value
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
            except OSError:
                logger.exception("Lecture .env échouée, valeurs par défaut utilisées.")
            return cfg

    def save(self, cfg: OBSConfig) -> bool:
        with self._lock:
            try:
                updates = {
                    ENV_KEYS["host"]: cfg.host,
                    ENV_KEYS["port"]: str(cfg.port),
                    ENV_KEYS["password"]: cfg.password,
                    ENV_KEYS["scan_interval_seconds"]: str(cfg.scan_interval_seconds),
                    ENV_KEYS["match_threshold"]: str(cfg.match_threshold),
                    ENV_KEYS["lang"]: cfg.lang,
                }
                seen = dict.fromkeys(updates, False)
                lines: list[str] = []
                if self._path.exists():
                    for line in self._path.read_text(encoding="utf-8").splitlines():
                        stripped = line.strip()
                        if "=" in stripped and not stripped.startswith("#"):
                            key = stripped.split("=", 1)[0].strip().upper()
                            if key in updates:
                                lines.append(f"{key}={updates[key]}")
                                seen[key] = True
                                continue
                        lines.append(line)
                for key, present in seen.items():
                    if not present:
                        lines.append(f"{key}={updates[key]}")
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
                tmp.replace(self._path)
                return True
            except OSError:
                logger.exception("Échec sauvegarde .env.")
                return False


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "source": self.source,
            "active_match": self.active_match, "appid": self.appid,
            "menu_images": self.menu_images, "ingame_images": self.ingame_images,
            "obs_scene_menu": self.obs_scene_menu, "obs_scene_ingame": self.obs_scene_ingame,
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
        )


class GameStore:
    """Persistance JSON locale (aucun réseau). Écriture atomique."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def load(self) -> list[Game]:
        with self._lock:
            if not self._path.exists():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                # Compat rétro : anciennes versions écrivaient un tableau brut
                # ([...]) au lieu du format {"games": [...]}. On accepte les
                # deux ; save() réécrit toujours au format canonique ensuite.
                games_raw = raw if isinstance(raw, list) else raw.get("games", [])
                return [Game.from_dict(g) for g in games_raw]
            except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                logger.exception("games.json invalide.")
                return []

    def save(self, games: list[Game]) -> bool:
        with self._lock:
            try:
                payload = {"games": [g.to_dict() for g in games]}
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
                tmp.replace(self._path)
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


# ============================================================================
# DÉTECTION PROCESSUS + VISUELLE (OpenCV)
# ============================================================================
def _imread_unicode(path: str, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """cv2.imread() échoue silencieusement sur les chemins Windows contenant
    des caractères accentués ou des apostrophes. Contournement fiable :
    lecture des octets bruts via numpy.fromfile puis décodage cv2.imdecode."""
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, flags)
    except (OSError, ValueError):
        logger.debug("Lecture image échouée: %s", path, exc_info=True)
        return None


def is_game_active(game: Game) -> bool:
    """Steam: correspondance par chemin d'installation (fiable, indépendant du
    nom d'exe). Manuel: correspondance par nom de processus."""
    try:
        if game.source == "steam" and game.active_match:
            target = str(Path(game.active_match)).lower()
            for proc in psutil.process_iter(["exe"]):
                exe = proc.info.get("exe")
                if exe and exe.lower().startswith(target):
                    return True
            return False
        if game.active_match:
            target_name = game.active_match.strip().lower()
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").lower()
                if name == target_name:
                    return True
    except (psutil.Error, OSError):
        logger.debug("Erreur vérification processus pour '%s'.", game.name, exc_info=True)
    return False


def _capture_screen_bgr() -> Optional[np.ndarray]:
    try:
        img: Image.Image = ImageGrab.grab()
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception:
        logger.debug("Capture écran échouée.", exc_info=True)
        return None


def _best_match_score(screen_bgr: np.ndarray, template_paths: list[str]) -> float:
    best = 0.0
    for path in template_paths:
        template = _imread_unicode(path, cv2.IMREAD_COLOR)
        if template is None:
            continue
        th, tw = template.shape[:2]
        sh, sw = screen_bgr.shape[:2]
        if th > sh or tw > sw:
            continue
        try:
            result = cv2.matchTemplate(screen_bgr, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            best = max(best, float(max_val))
        except cv2.error:
            logger.debug("matchTemplate échoué pour %s.", path, exc_info=True)
    return best


def detect_game_state(game: Game, threshold: float) -> str:
    """'inactive' | 'active' (process seul, sans images de référence) |
    'menu' | 'in_game' (avec correspondance visuelle OpenCV)."""
    if not is_game_active(game):
        return "inactive"
    if not game.menu_images and not game.ingame_images:
        return "active"
    screen = _capture_screen_bgr()
    if screen is None:
        return "active"
    menu_score = _best_match_score(screen, game.menu_images) if game.menu_images else 0.0
    ingame_score = _best_match_score(screen, game.ingame_images) if game.ingame_images else 0.0
    if max(menu_score, ingame_score) < threshold:
        return "active"
    return "in_game" if ingame_score >= menu_score else "menu"


def state_label(state: str) -> str:
    """Remplace l'ancien dict STATE_LABELS statique par un lookup i18n
    dynamique — recalculé à chaque appel donc valide après un changement
    de langue à chaud, avec mapping explicite pour éviter toute clé invalide."""
    mapping = {
        "inactive": "GAME_STATE_INACTIVE",
        "active": "GAME_STATE_ACTIVE",
        "menu": "GAME_STATE_MENU",
        "in_game": "GAME_STATE_IN_GAME",
    }
    return t(mapping.get(state, "GAME_STATE_INACTIVE"))


STATE_COLORS = {"inactive": COL_RED, "active": COL_YELLOW, "menu": COL_YELLOW, "in_game": COL_GREEN}
STATE_BADGE_BG = {
    "inactive": COL_BADGE_BG_INACTIVE, "active": COL_BADGE_BG_ACTIVE,
    "menu": COL_BADGE_BG_ACTIVE, "in_game": COL_BADGE_BG_ACTIVE,
}


# ============================================================================
# BOUCLE ASYNCIO DÉDIÉE (pour piloter simpleobsws depuis Tkinter, sans bloquer)
# ============================================================================
class AsyncLoopThread:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="obs-asyncio-loop")
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def run_coro(self, coro: Any) -> concurrent.futures.Future:
        if self._loop is None:
            raise RuntimeError("Boucle asyncio OBS non démarrée.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = None
        self._thread = None


class OBSClientError(Exception):
    pass


class OBSClient:
    """Client OBS WebSocket v5 minimal: connexion, requêtes typées, aucune
    dépendance à un serveur HTTP local."""

    def __init__(self, host: str, port: int, password: str) -> None:
        self.host, self.port, self.password = host, port, password
        self._ws: Optional[simpleobsws.WebSocketClient] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    async def connect(self, timeout: float = 8.0) -> None:
        url = f"ws://{self.host}:{self.port}"
        params = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False)
        ws = simpleobsws.WebSocketClient(url=url, password=self.password, identification_parameters=params)
        try:
            await asyncio.wait_for(ws.connect(), timeout=timeout)
            await asyncio.wait_for(ws.wait_until_identified(), timeout=timeout)
        except Exception as exc:
            raise OBSClientError(t("LOG_OBS_CONNECT_FAILED", url=url, error=exc)) from exc
        self._ws = ws
        self._connected = True
        logger.info(t("LOG_OBS_CONNECTED", url=url))

    async def disconnect(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.disconnect()
            except Exception:
                logger.debug("Erreur déconnexion OBS (ignorée).", exc_info=True)
        self._ws = None
        self._connected = False

    async def call(self, request_type: str, request_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        if not self.is_connected or self._ws is None:
            raise OBSClientError(t("LOG_OBS_NOT_CONNECTED", request_type=request_type))
        request = simpleobsws.Request(request_type, request_data or {})
        response = await self._ws.call(request)
        ok = getattr(response, "ok", lambda: False)()
        if not ok:
            status = getattr(response, "requestStatus", None)
            raise OBSClientError(t(
                "LOG_OBS_REQUEST_REFUSED", request_type=request_type,
                code=getattr(status, "code", None), comment=getattr(status, "comment", None),
            ))
        return getattr(response, "responseData", None) or {}

    async def get_scene_list(self) -> list[str]:
        data = await self.call("GetSceneList")
        return [s["sceneName"] for s in data.get("scenes", [])]

    async def set_current_scene(self, scene_name: str) -> None:
        await self.call("SetCurrentProgramScene", {"sceneName": scene_name})


# ============================================================================
# SCAN WORKER — boucle de surveillance + bascule de scène automatique
# ============================================================================
class ScanWorker:
    def __init__(self, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 config_mgr: EnvConfigManager, post_ui: Callable[[Callable[[], None]], None]) -> None:
        self._store = store
        self._obs_loop = obs_loop
        self._get_obs_client = obs_client_getter
        self._config_mgr = config_mgr
        self._post_ui = post_ui
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_states: dict[str, str] = {}

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, on_update: Callable[[list[dict[str, Any]]], None]) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, args=(on_update,), daemon=True, name="scan-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def _loop(self, on_update: Callable[[list[dict[str, Any]]], None]) -> None:
        while not self._stop_event.is_set():
            try:
                cfg = self._config_mgr.load()
                games = self._store.load()
                results: list[dict[str, Any]] = []
                for game in games:
                    state = detect_game_state(game, cfg.match_threshold)
                    results.append({
                        "id": game.id, "name": game.name, "exe": game.active_match,
                        "state": state, "active": state != "inactive",
                    })
                    self._maybe_switch_scene(game, state)
                self._post_ui(lambda r=results: on_update(r))
            except Exception:
                logger.exception("Erreur dans la boucle de surveillance.")
            self._stop_event.wait(max(0.5, cfg.scan_interval_seconds) if "cfg" in locals() else 2.0)

    def _maybe_switch_scene(self, game: Game, state: str) -> None:
        if state == self._last_states.get(game.id):
            return
        self._last_states[game.id] = state
        client = self._get_obs_client()
        if client is None or not client.is_connected:
            return
        target_scene = (
            game.obs_scene_ingame if state == "in_game"
            else game.obs_scene_menu if state == "menu"
            else ""
        )
        if not target_scene:
            return
        try:
            self._obs_loop.run_coro(client.set_current_scene(target_scene))
            logger.info(t("LOG_SCENE_SWITCH_OK", scene=target_scene, game=game.name, state=state))
        except Exception:
            logger.exception("Échec bascule de scène OBS.")


# ============================================================================
# COMPOSANT : CARTE JEU — poster 2:3, badge unique, overlay hover
# ============================================================================
class GameCard(ctk.CTkFrame):
    """Carte façon grille Steam : jaquette verticale 2:3, UN SEUL badge de
    statut (règle stricte anti-doublon), overlay sombre révélé au survol avec
    titre complet + source + actions Éditer/Supprimer."""

    CARD_WIDTH = 190
    CARD_HEIGHT = 285  # ratio 2:3

    def __init__(self, master, game: Game, state: str,
                 on_edit: Callable[[Game], None], on_delete: Callable[[Game], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_CARD, corner_radius=12,
                          border_width=1, border_color=COL_BORDER,
                          width=self.CARD_WIDTH, height=self.CARD_HEIGHT, **kwargs)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.game_id = game.id
        self._game = game
        self._current_state = state
        self._on_edit = on_edit
        self._on_delete = on_delete

        # --- Zone "jaquette" : dégradé violet + icône, occupe toute la carte ---
        self._poster = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=12)
        self._poster.grid(row=0, column=0, sticky="nsew")
        self._poster.grid_columnconfigure(0, weight=1)
        self._poster.grid_rowconfigure(0, weight=1)

        icon_lbl = ctk.CTkLabel(self._poster, text="🎮", font=font(46))
        icon_lbl.place(relx=0.5, rely=0.42, anchor="center")

        title_static = ctk.CTkLabel(self._poster, text=game.name, font=font(13, "bold"),
                                     text_color=COL_TEXT, wraplength=self.CARD_WIDTH - 24, justify="center")
        title_static.place(relx=0.5, rely=0.82, anchor="center")

        # --- Badge unique de statut (top-right) — jamais plus d'un par carte ---
        self._badge = ctk.CTkFrame(self._poster, fg_color=STATE_BADGE_BG.get(state, COL_BADGE_BG_INACTIVE),
                                    corner_radius=10, border_width=1,
                                    border_color=STATE_COLORS.get(state, COL_RED))
        self._badge.place(relx=1.0, rely=0.0, x=-8, y=8, anchor="ne")
        self._badge_dot = ctk.CTkLabel(self._badge, text="●", font=font(9),
                                        text_color=STATE_COLORS.get(state, COL_RED))
        self._badge_dot.pack(side="left", padx=(8, 2), pady=3)
        self._badge_lbl = ctk.CTkLabel(self._badge, text=state_label(state), font=font(10, "bold"),
                                        text_color=STATE_COLORS.get(state, COL_RED))
        self._badge_lbl.pack(side="left", padx=(0, 8), pady=3)

        # --- Overlay hover : masqué par défaut, révélé au survol ---
        self._overlay = ctk.CTkFrame(self, fg_color="#08060F", corner_radius=12)
        self._overlay_visible = False

        source_txt = t("GAME_SOURCE_STEAM") if game.source == "steam" else t("GAME_SOURCE_MANUAL")
        self._overlay_title = ctk.CTkLabel(self._overlay, text=game.name, font=font(13, "bold"),
                                            text_color=COL_TEXT, wraplength=self.CARD_WIDTH - 24, justify="center")
        self._overlay_title.place(relx=0.5, rely=0.30, anchor="center")
        self._overlay_source = ctk.CTkLabel(self._overlay, text=source_txt, font=font(10),
                                             text_color=COL_TEXT_MUTED)
        self._overlay_source.place(relx=0.5, rely=0.42, anchor="center")

        btn_row = ctk.CTkFrame(self._overlay, fg_color="transparent")
        btn_row.place(relx=0.5, rely=0.68, anchor="center")
        self._edit_btn = ctk.CTkButton(btn_row, text=t("GAME_CARD_BTN_EDIT"), width=76, height=28,
                                        fg_color=COL_ACCENT_SOFT, hover_color=COL_ACCENT_HOVER,
                                        font=font(11), corner_radius=8,
                                        command=lambda: self._on_edit(self._game))
        self._edit_btn.pack(side="left", padx=3)
        self._delete_btn = ctk.CTkButton(btn_row, text=t("GAME_CARD_BTN_DELETE"), width=90, height=28,
                                          fg_color="#3A1420", hover_color=COL_RED,
                                          font=font(11), corner_radius=8,
                                          command=lambda: self._on_delete(self._game))
        self._delete_btn.pack(side="left", padx=3)

        # Overlay opacity simulé via couleur sombre unie (CTk ne supporte pas
        # l'alpha réel) : contraste net et lisible sans dépendance externe.
        for widget in (self, self._poster, icon_lbl, title_static):
            widget.bind("<Enter>", self._show_overlay)
        self._overlay.bind("<Leave>", self._hide_overlay)
        self._poster.bind("<Leave>", self._on_poster_leave)

    def _show_overlay(self, _event: Any = None) -> None:
        if self._overlay_visible:
            return
        self._overlay_visible = True
        self._overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._overlay.lift()

    def _on_poster_leave(self, event: Any) -> None:
        # Tolère les micro-déplacements entre poster et overlay (évite un
        # flicker d'ouverture/fermeture lors du passage de souris entre les
        # deux widgets superposés).
        self.after(60, self._maybe_hide, event.widget.winfo_pointerxy())

    def _maybe_hide(self, pointer_xy: tuple[int, int]) -> None:
        x, y = pointer_xy
        try:
            widget_under = self.winfo_containing(x, y)
        except Exception:
            widget_under = None
        if widget_under is None or not self._is_descendant(widget_under):
            self._hide_overlay()

    def _is_descendant(self, widget: Any) -> bool:
        current = widget
        while current is not None:
            if current == self:
                return True
            current = getattr(current, "master", None)
        return False

    def _hide_overlay(self, _event: Any = None) -> None:
        if not self._overlay_visible:
            return
        self._overlay_visible = False
        self._overlay.place_forget()

    def refresh_labels(self) -> None:
        """Recharge les libellés dynamiques (source, boutons, badge) après un
        changement de langue à chaud — sans recréer les widgets."""
        source_txt = t("GAME_SOURCE_STEAM") if self._game.source == "steam" else t("GAME_SOURCE_MANUAL")
        self._overlay_source.configure(text=source_txt)
        self._edit_btn.configure(text=t("GAME_CARD_BTN_EDIT"))
        self._delete_btn.configure(text=t("GAME_CARD_BTN_DELETE"))
        self._badge_lbl.configure(text=state_label(self._current_state))

    def set_state(self, state: str) -> None:
        """Met à jour uniquement le badge d'état, sans recréer le widget
        (appelé à chaque cycle de scan — doit rester O(1) et sans flicker)."""
        if state == self._current_state:
            return
        self._current_state = state
        color = STATE_COLORS.get(state, COL_RED)
        self._badge.configure(fg_color=STATE_BADGE_BG.get(state, COL_BADGE_BG_INACTIVE), border_color=color)
        self._badge_dot.configure(text_color=color)
        self._badge_lbl.configure(text=state_label(state), text_color=color)


# ============================================================================
# MODAL : AJOUTER / MODIFIER UN JEU
# ============================================================================
class GameModal(ctk.CTkToplevel):
    def __init__(self, master, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 on_saved: Callable[[], None], steam_candidates: list[dict[str, str]],
                 game: Optional[Game] = None) -> None:
        super().__init__(master)
        self.title(t("GAME_MODAL_TITLE_EDIT") if game else t("GAME_MODAL_TITLE_ADD"))
        self.geometry("480x640")
        self.configure(fg_color=COL_BG)
        self.transient(master)
        self.grab_set()

        self._store = store
        self._obs_loop = obs_loop
        self._get_obs_client = obs_client_getter
        self._on_saved = on_saved
        self._game = game
        self._steam_candidates = steam_candidates
        self._menu_images: list[str] = list(game.menu_images) if game else []
        self._ingame_images: list[str] = list(game.ingame_images) if game else []

        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=16, pady=16)
        scroll.grid_columnconfigure(0, weight=1)

        # --- Source ---
        self.source_var = ctk.StringVar(value=(game.source if game else "manual"))
        source_row = ctk.CTkFrame(scroll, fg_color="transparent")
        source_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ctk.CTkRadioButton(source_row, text=t("GAME_MODAL_SOURCE_STEAM"), variable=self.source_var,
                            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                            value="steam", command=self._toggle_source).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(source_row, text=t("GAME_MODAL_SOURCE_MANUAL"), variable=self.source_var,
                            fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                            value="manual", command=self._toggle_source).pack(side="left")

        # --- Steam: dropdown des jeux détectés non encore ajoutés ---
        self.steam_var = ctk.StringVar()
        steam_names = [f"{g['name']} (appid {g['appid']})" for g in steam_candidates] or [t("GAME_MODAL_STEAM_NONE_DETECTED")]
        self.steam_menu = ctk.CTkOptionMenu(scroll, values=steam_names, variable=self.steam_var,
                                             fg_color=COL_BG, button_color=COL_ACCENT,
                                             button_hover_color=COL_ACCENT_HOVER)
        self.steam_menu.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        # --- Manuel: nom + exe ---
        self.name_var = ctk.StringVar(value=game.name if game else "")
        self.exe_var = ctk.StringVar(value=game.active_match if (game and game.source == "manual") else "")
        self._labeled_entry(scroll, 2, t("GAME_MODAL_LABEL_NAME"), self.name_var)
        self._labeled_entry(scroll, 3, t("GAME_MODAL_LABEL_EXE"), self.exe_var)

        # --- Images ---
        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_IMAGES_MENU"), font=font(12, "bold"),
                     anchor="w").grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.menu_list_lbl = ctk.CTkLabel(scroll, text=self._images_summary(self._menu_images),
                                           text_color=COL_TEXT_MUTED, anchor="w", font=font(11))
        self.menu_list_lbl.grid(row=5, column=0, sticky="w")
        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_PICK_MENU_IMAGES"), height=30, fg_color=COL_CARD,
                       hover_color=COL_CARD_HOVER,
                       command=lambda: self._pick_images("menu")).grid(row=6, column=0, sticky="ew", pady=(4, 10))

        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_IMAGES_INGAME"), font=font(12, "bold"),
                     anchor="w").grid(row=7, column=0, sticky="w", pady=(4, 2))
        self.ingame_list_lbl = ctk.CTkLabel(scroll, text=self._images_summary(self._ingame_images),
                                             text_color=COL_TEXT_MUTED, anchor="w", font=font(11))
        self.ingame_list_lbl.grid(row=8, column=0, sticky="w")
        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_PICK_INGAME_IMAGES"), height=30, fg_color=COL_CARD,
                       hover_color=COL_CARD_HOVER,
                       command=lambda: self._pick_images("ingame")).grid(row=9, column=0, sticky="ew", pady=(4, 10))

        # --- Scènes OBS ---
        scene_names = self._fetch_scene_names()
        self.scene_menu_var = ctk.StringVar(value=game.obs_scene_menu if game else "")
        self.scene_ingame_var = ctk.StringVar(value=game.obs_scene_ingame if game else "")
        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_SCENE_MENU"), font=font(12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=10, column=0, sticky="w", pady=(6, 2))
        self._scene_selector(scroll, 11, scene_names, self.scene_menu_var)
        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_SCENE_INGAME"), font=font(12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=12, column=0, sticky="w", pady=(6, 2))
        self._scene_selector(scroll, 13, scene_names, self.scene_ingame_var)

        self.msg_lbl = ctk.CTkLabel(scroll, text="", font=font(11))
        self.msg_lbl.grid(row=14, column=0, sticky="w", pady=(10, 0))

        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_SAVE"), height=38, fg_color=COL_ACCENT,
                       hover_color=COL_ACCENT_HOVER, text_color="#0F0C1B",
                       font=font(13, "bold"), command=self._save).grid(
            row=15, column=0, sticky="ew", pady=(16, 0))

        self._toggle_source()

    @staticmethod
    def _images_summary(paths: list[str]) -> str:
        return t("GAME_MODAL_IMAGES_COUNT", count=len(paths)) if paths else t("GAME_MODAL_IMAGES_NONE")

    def _labeled_entry(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=font(12), text_color=COL_TEXT_MUTED,
                     anchor="w").grid(row=row, column=0, sticky="w", pady=(4, 2))
        entry = ctk.CTkEntry(parent, textvariable=var, fg_color=COL_BG, border_color=COL_BORDER)
        entry.grid(row=row, column=0, sticky="ew", pady=(20, 8))

    def _scene_selector(self, parent, row: int, scene_names: list[str], var: ctk.StringVar) -> None:
        values = scene_names or [t("GAME_MODAL_SCENE_NOT_CONNECTED")]
        ctk.CTkOptionMenu(parent, values=values, variable=var, fg_color=COL_BG,
                           button_color=COL_ACCENT, button_hover_color=COL_ACCENT_HOVER
                           ).grid(row=row, column=0, sticky="ew")

    def _fetch_scene_names(self) -> list[str]:
        client = self._get_obs_client()
        if client is None or not client.is_connected:
            return []
        try:
            future = self._obs_loop.run_coro(client.get_scene_list())
            return future.result(timeout=5)
        except Exception:
            logger.debug("Impossible de récupérer la liste des scènes OBS.", exc_info=True)
            return []

    def _toggle_source(self) -> None:
        is_steam = self.source_var.get() == "steam"
        self.steam_menu.configure(state="normal" if is_steam else "disabled")

    def _pick_images(self, kind: str) -> None:
        paths = filedialog.askopenfilenames(
            title=t("GAME_MODAL_FILEDIALOG_TITLE"),
            filetypes=[(t("GAME_MODAL_FILEDIALOG_FILTER_LABEL"), "*.png")],
        )
        if not paths:
            return
        if kind == "menu":
            self._menu_images = list(paths)
            self.menu_list_lbl.configure(text=self._images_summary(self._menu_images))
        else:
            self._ingame_images = list(paths)
            self.ingame_list_lbl.configure(text=self._images_summary(self._ingame_images))

    def _save(self) -> None:
        if self.source_var.get() == "steam":
            selection = self.steam_var.get()
            match = next((g for g in self._steam_candidates
                          if f"{g['name']} (appid {g['appid']})" == selection), None)
            if match is None:
                self.msg_lbl.configure(text=t("GAME_MODAL_ERR_INVALID_STEAM_SELECTION"), text_color=COL_RED)
                return
            name, source, active_match, appid = match["name"], "steam", match["install_dir"], match["appid"]
        else:
            name = self.name_var.get().strip()
            exe = self.exe_var.get().strip()
            if not name or not exe:
                self.msg_lbl.configure(text=t("GAME_MODAL_ERR_MISSING_NAME_EXE"), text_color=COL_RED)
                return
            source, active_match, appid = "manual", exe, ""

        game = Game(
            id=self._game.id if self._game else uuid.uuid4().hex,
            name=name, source=source, active_match=active_match, appid=appid,
            menu_images=self._menu_images, ingame_images=self._ingame_images,
            obs_scene_menu=self.scene_menu_var.get(), obs_scene_ingame=self.scene_ingame_var.get(),
        )
        if self._store.upsert(game):
            self._on_saved()
            self.destroy()
        else:
            self.msg_lbl.configure(text=t("GAME_MODAL_ERR_SAVE_FAILED"), text_color=COL_RED)


# ============================================================================
# VUE : DASHBOARD (Bibliothèque — grille poster style Steam)
# ============================================================================
class DashboardView(ctk.CTkFrame):
    GRID_COLUMNS = 5

    def __init__(self, master, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 steam_scanner: SteamScanner, post_ui: Callable[[Callable[[], None]], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._store = store
        self._obs_loop = obs_loop
        self._get_obs_client = obs_client_getter
        self._steam_scanner = steam_scanner
        self._post_ui = post_ui
        self._latest_states: dict[str, str] = {}
        self._steam_candidates: list[dict[str, str]] = []
        self._cards: dict[str, GameCard] = {}
        self._rendered_ids: tuple[str, ...] = ()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(28, 8))
        header.grid_columnconfigure(0, weight=1)

        title_col = ctk.CTkFrame(header, fg_color="transparent")
        title_col.grid(row=0, column=0, sticky="w")
        self._title_lbl = ctk.CTkLabel(title_col, text=t("DASHBOARD_TITLE"), font=font(22, "bold"),
                                        text_color=COL_TEXT)
        self._title_lbl.pack(anchor="w")
        self._subtitle_lbl = ctk.CTkLabel(title_col, text="", font=font(11), text_color=COL_TEXT_MUTED)
        self._subtitle_lbl.pack(anchor="w", pady=(2, 0))

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        self._add_btn = ctk.CTkButton(btns, text=t("DASHBOARD_BTN_ADD"), width=110, height=36,
                                       fg_color=COL_CARD, hover_color=COL_CARD_HOVER,
                                       border_width=1, border_color=COL_BORDER, corner_radius=9,
                                       font=font(12), command=self._open_add_modal)
        self._add_btn.pack(side="left", padx=(0, 8))
        self.scan_btn = ctk.CTkButton(btns, text=t("DASHBOARD_BTN_SCAN_STEAM"), width=160, height=36,
                                       fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                       text_color="#0F0C1B", font=font(12, "bold"), corner_radius=9,
                                       command=self.scan_steam_library)
        self.scan_btn.pack(side="left")

        self.status_lbl = ctk.CTkLabel(self, text="", font=font(11),
                                        text_color=COL_TEXT_MUTED, anchor="w")
        self.status_lbl.grid(row=1, column=0, sticky="w", padx=30)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=22, pady=10)
        for c in range(self.GRID_COLUMNS):
            self.scroll.grid_columnconfigure(c, weight=1)
        self._enable_smooth_scroll(self.scroll)

        self.render_games()

    @staticmethod
    def _enable_smooth_scroll(scrollable: ctk.CTkScrollableFrame) -> None:
        """Remplace le binding molette par défaut de CTkScrollableFrame (pas
        grossier, un seul 'saut' par cran) par un défilement à granularité
        fine sur le canvas interne, pour un rendu fluide haute fréquence
        plutôt qu'un défilement par paliers saccadés."""
        canvas = getattr(scrollable, "_parent_canvas", None)
        if canvas is None:
            return  # version de customtkinter sans canvas exposé — no-op sûr

        def _on_wheel(event: Any) -> str:
            steps = max(1, abs(int(event.delta / 40)))
            direction = -1 if event.delta > 0 else 1
            for _ in range(steps):
                canvas.yview_scroll(direction, "units")
            return "break"

        canvas.bind("<MouseWheel>", _on_wheel)
        for child in scrollable.winfo_children():
            child.bind("<MouseWheel>", _on_wheel)

    def _clear(self) -> None:
        for widget in self.scroll.winfo_children():
            widget.destroy()
        self._cards.clear()

    def refresh_labels(self) -> None:
        """Rechargement à chaud de tous les libellés statiques après un
        changement de langue — pas de rebuild des cartes, juste leurs textes."""
        self._title_lbl.configure(text=t("DASHBOARD_TITLE"))
        self._add_btn.configure(text=t("DASHBOARD_BTN_ADD"))
        self.scan_btn.configure(text=t("DASHBOARD_BTN_SCAN_STEAM"))
        self._update_subtitle()
        for card in self._cards.values():
            card.refresh_labels()
        if not self._cards and not self._store.load():
            self.render_games()

    def _update_subtitle(self) -> None:
        count = len(self._store.load())
        self._subtitle_lbl.configure(text=t("DASHBOARD_SUBTITLE", count=count))

    def render_games(self) -> None:
        """Reconstruction complète de la grille — appelée uniquement quand la
        LISTE des jeux change réellement (ajout/suppression/réordonnancement),
        jamais à chaque cycle de scan. Voir apply_scan_results()."""
        self._clear()
        games = self._store.load()
        self._rendered_ids = tuple(g.id for g in games)
        self._update_subtitle()
        if not games:
            ctk.CTkLabel(self.scroll, text=t("DASHBOARD_EMPTY_STATE"),
                         text_color=COL_TEXT_MUTED, font=font(12)).grid(row=0, column=0, padx=10, pady=30)
            return
        for i, game in enumerate(games):
            state = self._latest_states.get(game.id, "inactive")
            card = GameCard(self.scroll, game=game, state=state,
                             on_edit=self._open_edit_modal, on_delete=self._delete_game)
            card.grid(row=i // self.GRID_COLUMNS, column=i % self.GRID_COLUMNS,
                      sticky="n", padx=10, pady=10)
            self._cards[game.id] = card

    def apply_scan_results(self, results: list[dict[str, Any]]) -> None:
        """Appelé depuis ScanWorker (via la file UI thread-safe) à chaque cycle
        (toutes les 0.5-2s selon config). CRITIQUE : ne doit JAMAIS détruire/
        recréer les widgets si la liste de jeux n'a pas changé, sous peine de
        provoquer le flicker + reset du scroll de CTkScrollableFrame observés
        précédemment. Diff par ID : rebuild complet seulement si le set/ordre
        des jeux a changé, sinon simple mise à jour du badge d'état."""
        new_states = {r["id"]: r["state"] for r in results}
        active_count = sum(1 for r in results if r["active"])
        self.status_lbl.configure(
            text=t("DASHBOARD_STATUS_SUMMARY", count=len(results), active=active_count),
            text_color=COL_TEXT_MUTED,
        )

        new_ids = tuple(r["id"] for r in results)
        if new_ids != self._rendered_ids:
            # Un jeu a été ajouté/supprimé (ou l'ordre a changé) depuis le
            # dernier rendu : seule situation qui justifie un rebuild complet.
            self._latest_states = new_states
            self.render_games()
            return

        # Aucun changement structurel : mise à jour ciblée, zéro destruction
        # de widget, zéro flicker, scroll utilisateur préservé intact.
        for game_id, state in new_states.items():
            card = self._cards.get(game_id)
            if card is not None:
                card.set_state(state)
        self._latest_states = new_states

    def scan_steam_library(self) -> None:
        self.scan_btn.configure(state="disabled", text=t("DASHBOARD_BTN_SCAN_STEAM_PROGRESS"))
        self.status_lbl.configure(text=t("DASHBOARD_SCAN_IN_PROGRESS"))
        threading.Thread(target=self._scan_steam_bg, daemon=True).start()

    def _scan_steam_bg(self) -> None:
        try:
            found = self._steam_scanner.scan_installed_games()
            added = self._store.import_steam_games(found)
            self._steam_candidates = found
            error = None
        except Exception as exc:
            logger.exception("Échec scan Steam.")
            found, added, error = [], 0, str(exc)
        self._post_ui(lambda: self._on_scan_done(len(found), added, error))

    def _on_scan_done(self, total_found: int, added: int, error: Optional[str]) -> None:
        self.scan_btn.configure(state="normal", text=t("DASHBOARD_BTN_SCAN_STEAM"))
        if error:
            self.status_lbl.configure(text=t("DASHBOARD_SCAN_ERROR", error=error), text_color=COL_RED)
            return
        self.status_lbl.configure(
            text=t("DASHBOARD_SCAN_RESULT", found=total_found, added=added),
            text_color=COL_GREEN if added else COL_TEXT_MUTED,
        )
        self.render_games()

    def _open_add_modal(self) -> None:
        GameModal(self, store=self._store, obs_loop=self._obs_loop,
                  obs_client_getter=self._get_obs_client, on_saved=self.render_games,
                  steam_candidates=self._steam_candidates)

    def _open_edit_modal(self, game: Game) -> None:
        GameModal(self, store=self._store, obs_loop=self._obs_loop,
                  obs_client_getter=self._get_obs_client, on_saved=self.render_games,
                  steam_candidates=self._steam_candidates, game=game)

    def _delete_game(self, game: Game) -> None:
        if messagebox.askyesno(t("CONFIRM_DIALOG_TITLE"), t("CONFIRM_DELETE_GAME", name=game.name)):
            self._store.delete(game.id)
            self.render_games()


# ============================================================================
# VUE : PARAMÈTRES
# ============================================================================
class SettingsView(ctk.CTkFrame):
    def __init__(self, master, config_mgr: EnvConfigManager, on_saved: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.config_mgr = config_mgr
        self.on_saved = on_saved
        self.grid_columnconfigure(0, weight=1)

        self._title_lbl = ctk.CTkLabel(self, text=t("SETTINGS_TITLE"), font=font(22, "bold"), text_color=COL_TEXT)
        self._title_lbl.grid(row=0, column=0, sticky="w", padx=28, pady=(28, 16))

        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14, border_width=1, border_color=COL_BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=28)
        card.grid_columnconfigure(1, weight=1)

        self._section_lbl = ctk.CTkLabel(card, text=t("SETTINGS_SECTION_OBS_WS"), font=font(14, "bold"),
                                          text_color=COL_TEXT)
        self._section_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self.host_var = ctk.StringVar()
        self.port_var = ctk.StringVar()
        self.pwd_var = ctk.StringVar()
        self.interval_var = ctk.StringVar()
        self.threshold_var = ctk.StringVar()

        self._field_labels: dict[str, ctk.CTkLabel] = {}
        self._field_labels["host"] = self._field(card, 1, t("SETTINGS_LABEL_HOST"), self.host_var)
        self._field_labels["port"] = self._field(card, 2, t("SETTINGS_LABEL_PORT"), self.port_var)
        self._field_labels["password"] = self._password_field(card, 3, t("SETTINGS_LABEL_PASSWORD"), self.pwd_var)
        self._field_labels["interval"] = self._field(card, 4, t("SETTINGS_LABEL_SCAN_INTERVAL"), self.interval_var)
        self._field_labels["threshold"] = self._field(card, 5, t("SETTINGS_LABEL_MATCH_THRESHOLD"), self.threshold_var)

        self.msg_lbl = ctk.CTkLabel(card, text="", font=font(11))
        self.msg_lbl.grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 0))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=7, column=0, columnspan=2, sticky="ew", padx=20, pady=18)
        self._save_btn = ctk.CTkButton(btn_row, text=t("SETTINGS_BTN_SAVE"), width=160, height=38,
                                        fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                        text_color="#0F0C1B", corner_radius=9,
                                        font=font(13, "bold"), command=self._save)
        self._save_btn.pack(side="left")

        # --- Sélecteur de langue : segmented control géométriquement stable ---
        lang_card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14, border_width=1, border_color=COL_BORDER)
        lang_card.grid(row=2, column=0, sticky="ew", padx=28, pady=(20, 0))
        lang_card.grid_columnconfigure(1, weight=1)

        self._lang_section_lbl = ctk.CTkLabel(lang_card, text=t("SETTINGS_SECTION_LANGUAGE"),
                                               font=font(14, "bold"), text_color=COL_TEXT)
        self._lang_section_lbl.grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self._lang_seg = LanguageSegmentedControl(lang_card, on_select=self._on_lang_selected)
        self._lang_seg.grid(row=1, column=0, sticky="w", padx=20, pady=(0, 18))

        self._load_into_form()

    def _field(self, parent, row: int, label: str, var: ctk.StringVar) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(parent, text=label, font=font(12), text_color=COL_TEXT_MUTED)
        lbl.grid(row=row, column=0, sticky="w", padx=20, pady=6)
        ctk.CTkEntry(parent, textvariable=var, width=200, height=34, fg_color=COL_BG,
                     border_color=COL_BORDER).grid(row=row, column=1, sticky="e", padx=20, pady=6)
        return lbl

    def _password_field(self, parent, row: int, label: str, var: ctk.StringVar) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(parent, text=label, font=font(12), text_color=COL_TEXT_MUTED)
        lbl.grid(row=row, column=0, sticky="w", padx=20, pady=6)
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=1, sticky="e", padx=20, pady=6)
        self._pwd_entry = ctk.CTkEntry(wrapper, textvariable=var, width=160, height=34, show="•",
                                        fg_color=COL_BG, border_color=COL_BORDER)
        self._pwd_entry.pack(side="left")
        self._pwd_visible = False
        ctk.CTkButton(wrapper, text="👁", width=34, height=34, fg_color=COL_BG,
                      hover_color=COL_BORDER, command=self._toggle_pwd).pack(side="left", padx=(4, 0))
        return lbl

    def _toggle_pwd(self) -> None:
        self._pwd_visible = not self._pwd_visible
        self._pwd_entry.configure(show="" if self._pwd_visible else "•")

    def _load_into_form(self) -> None:
        cfg = self.config_mgr.load()
        self.host_var.set(cfg.host)
        self.port_var.set(str(cfg.port))
        self.pwd_var.set(cfg.password)
        self.interval_var.set(str(cfg.scan_interval_seconds))
        self.threshold_var.set(str(cfg.match_threshold))
        self._lang_seg.set_active(cfg.lang, notify=False)

    def _on_lang_selected(self, lang: str) -> None:
        """Applique le changement de langue à chaud (i18n.set_lang notifie
        tous les listeners) puis persiste le choix dans .env, sans jamais
        redémarrer l'application ni casser les libellés déjà affichés."""
        if not i18n.set_lang(lang):
            return
        cfg = self.config_mgr.load()
        cfg.lang = lang
        self.config_mgr.save(cfg)

    def refresh_labels(self) -> None:
        """Rechargement à chaud après changement de langue."""
        self._title_lbl.configure(text=t("SETTINGS_TITLE"))
        self._section_lbl.configure(text=t("SETTINGS_SECTION_OBS_WS"))
        self._lang_section_lbl.configure(text=t("SETTINGS_SECTION_LANGUAGE"))
        self._field_labels["host"].configure(text=t("SETTINGS_LABEL_HOST"))
        self._field_labels["port"].configure(text=t("SETTINGS_LABEL_PORT"))
        self._field_labels["password"].configure(text=t("SETTINGS_LABEL_PASSWORD"))
        self._field_labels["interval"].configure(text=t("SETTINGS_LABEL_SCAN_INTERVAL"))
        self._field_labels["threshold"].configure(text=t("SETTINGS_LABEL_MATCH_THRESHOLD"))
        self._save_btn.configure(text=t("SETTINGS_BTN_SAVE"))
        self._lang_seg.refresh_labels()

    def _save(self) -> None:
        try:
            port = int(self.port_var.get())
            if not (0 < port <= 65535):
                raise ValueError(t("SETTINGS_ERR_PORT_RANGE"))
            interval = max(0.5, float(self.interval_var.get()))
            threshold = min(1.0, max(0.0, float(self.threshold_var.get())))
        except ValueError as exc:
            self.msg_lbl.configure(text=t("SETTINGS_ERR_INVALID_VALUE", error=exc), text_color=COL_RED)
            return

        cfg = OBSConfig(
            host=self.host_var.get().strip() or "localhost",
            port=port, password=self.pwd_var.get(),
            scan_interval_seconds=interval, match_threshold=threshold,
            lang=i18n.current_lang(),
        )
        if self.config_mgr.save(cfg):
            self.msg_lbl.configure(text=t("SETTINGS_SAVE_SUCCESS"), text_color=COL_GREEN)
            self.on_saved()
        else:
            self.msg_lbl.configure(text=t("SETTINGS_SAVE_FAILED"), text_color=COL_RED)


class LanguageSegmentedControl(ctk.CTkFrame):
    """Sélecteur de langue en groupe de boutons segmentés à géométrie fixe :
    chaque bouton a une largeur figée (LANG_BTN_WIDTH) et le highlight actif
    ne fait que changer de couleur de fond — il ne redimensionne, ne déplace
    et ne pousse jamais les widgets voisins, quelle que soit la langue active
    (corrige le bug de décalage du sélecteur mentionné dans les specs)."""

    LANG_BTN_WIDTH = 64
    LANG_BTN_HEIGHT = 34
    COL_ACTIVE = "#3B82F6"
    COL_ACTIVE_HOVER = "#2563EB"
    COL_INACTIVE = COL_BG
    COL_INACTIVE_HOVER = COL_BORDER

    def __init__(self, master, on_select: Callable[[str], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_BG, corner_radius=10,
                          border_width=1, border_color=COL_BORDER, **kwargs)
        self._on_select = on_select
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._active_lang = i18n.current_lang()

        for i, lang in enumerate(("fr", "en", "es")):
            btn = ctk.CTkButton(
                self, text=t(f"LANG_{lang.upper()}"), width=self.LANG_BTN_WIDTH, height=self.LANG_BTN_HEIGHT,
                corner_radius=8, font=font(12, "bold"),
                fg_color=self.COL_INACTIVE, hover_color=self.COL_INACTIVE_HOVER,
                text_color=COL_TEXT_MUTED, border_width=0,
                command=lambda l=lang: self._select(l),
            )
            btn.grid(row=0, column=i, padx=3, pady=3)
            self._buttons[lang] = btn

        self._apply_active_style()

    def _select(self, lang: str) -> None:
        if lang == self._active_lang:
            return
        self._active_lang = lang
        self._apply_active_style()
        self._on_select(lang)

    def set_active(self, lang: str, notify: bool = True) -> None:
        if lang not in self._buttons:
            return
        self._active_lang = lang
        self._apply_active_style()
        if notify:
            self._on_select(lang)

    def _apply_active_style(self) -> None:
        # Géométrie strictement inchangée : seule fg_color/text_color change,
        # jamais width/height/padx/pady -> le carré actif reste verrouillé
        # dans son footprint sans décaler les boutons adjacents.
        for lang, btn in self._buttons.items():
            is_active = lang == self._active_lang
            btn.configure(
                fg_color=self.COL_ACTIVE if is_active else self.COL_INACTIVE,
                hover_color=self.COL_ACTIVE_HOVER if is_active else self.COL_INACTIVE_HOVER,
                text_color="#FFFFFF" if is_active else COL_TEXT_MUTED,
            )

    def refresh_labels(self) -> None:
        for lang, btn in self._buttons.items():
            btn.configure(text=t(f"LANG_{lang.upper()}"))


# ============================================================================
# SIDEBAR
# ============================================================================
class Sidebar(ctk.CTkFrame):
    def __init__(self, master, on_nav: Callable[[str], None],
                 on_start: Callable[[], None], on_stop: Callable[[], None],
                 on_open_folder: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_SIDEBAR, corner_radius=0, width=240, **kwargs)
        self.grid_propagate(False)
        self.grid_rowconfigure(6, weight=1)
        self.on_nav = on_nav

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=22, pady=(26, 30))
        self._brand_icon_lbl = ctk.CTkLabel(brand, text=t("SIDEBAR_BRAND_ICON"), font=font(19, "bold"),
                                             text_color=COL_ACCENT)
        self._brand_icon_lbl.pack(side="left")
        self._brand_suffix_lbl = ctk.CTkLabel(brand, text=t("SIDEBAR_BRAND_SUFFIX"), font=font(19, "bold"),
                                               text_color=COL_TEXT)
        self._brand_suffix_lbl.pack(side="left")

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_btn("dashboard", t("SIDEBAR_NAV_DASHBOARD"), row=1)
        self._nav_btn("settings", t("SIDEBAR_NAV_SETTINGS"), row=2)

        self._folder_btn = ctk.CTkButton(self, text=t("SIDEBAR_BTN_OPEN_FOLDER"), anchor="w", height=36,
                                          corner_radius=8, fg_color="transparent", hover_color=COL_CARD,
                                          text_color=COL_TEXT_MUTED, font=font(12), command=on_open_folder)
        self._folder_btn.grid(row=3, column=0, sticky="ew", padx=12, pady=(10, 3))

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=6, column=0, sticky="sew", padx=16, pady=20)

        self.status_dot = ctk.CTkLabel(ctrl, text="●", text_color=COL_RED, font=font(14))
        self.status_dot.pack(anchor="w")
        self.status_text = ctk.CTkLabel(ctrl, text=t("SIDEBAR_STATUS_STOPPED"), font=font(11),
                                         text_color=COL_TEXT_MUTED, wraplength=195, justify="left", anchor="w")
        self.status_text.pack(anchor="w", pady=(0, 10), fill="x")

        self.start_btn = ctk.CTkButton(ctrl, text=t("SIDEBAR_BTN_START"), height=38, fg_color=COL_GREEN,
                                        hover_color="#16A34A", font=font(12, "bold"), corner_radius=9,
                                        command=on_start)
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ctk.CTkButton(ctrl, text=t("SIDEBAR_BTN_STOP"), height=38, fg_color=COL_RED,
                                       hover_color="#DC2626", font=font(12, "bold"), corner_radius=9,
                                       command=on_stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)

        self._is_running = False

    def _nav_btn(self, key: str, text: str, row: int) -> None:
        btn = ctk.CTkButton(self, text=text, anchor="w", height=42, corner_radius=9,
                             fg_color="transparent", hover_color=COL_CARD, text_color=COL_TEXT,
                             font=font(13), command=lambda: self.on_nav(key))
        btn.grid(row=row, column=0, sticky="ew", padx=12, pady=3)
        self.nav_buttons[key] = btn

    def set_active(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=COL_CARD if k == key else "transparent",
                          border_width=1 if k == key else 0,
                          border_color=COL_BORDER_ACCENT if k == key else COL_BORDER)

    def set_running_state(self, running: bool) -> None:
        self._is_running = running
        self.status_dot.configure(text_color=COL_GREEN if running else COL_RED)
        self.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING") if running else t("SIDEBAR_STATUS_STOPPED"))
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def refresh_labels(self) -> None:
        self._brand_icon_lbl.configure(text=t("SIDEBAR_BRAND_ICON"))
        self._brand_suffix_lbl.configure(text=t("SIDEBAR_BRAND_SUFFIX"))
        self.nav_buttons["dashboard"].configure(text=t("SIDEBAR_NAV_DASHBOARD"))
        self.nav_buttons["settings"].configure(text=t("SIDEBAR_NAV_SETTINGS"))
        self._folder_btn.configure(text=t("SIDEBAR_BTN_OPEN_FOLDER"))
        self.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING") if self._is_running else t("SIDEBAR_STATUS_STOPPED"))
        self.start_btn.configure(text=t("SIDEBAR_BTN_START"))
        self.stop_btn.configure(text=t("SIDEBAR_BTN_STOP"))


# ============================================================================
# APPLICATION PRINCIPALE
# ============================================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(t("APP_TITLE_WINDOW"))
        self.geometry("1180x720")
        self.minsize(960, 620)
        self.configure(fg_color=COL_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        self.config_mgr = EnvConfigManager(ENV_PATH)
        # Applique la langue persistée en .env avant toute construction de vue.
        _saved_cfg = self.config_mgr.load()
        i18n.set_lang(_saved_cfg.lang)

        self.store = GameStore(GAMES_PATH)
        self.steam_scanner = SteamScanner()
        self.obs_loop = AsyncLoopThread()
        self._obs_client: Optional[OBSClient] = None
        self.scan_worker = ScanWorker(self.store, self.obs_loop, lambda: self._obs_client,
                                       self.config_mgr, self.post_ui)

        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, on_nav=self._navigate, on_start=self._start, on_stop=self._stop,
                                on_open_folder=self._open_data_folder)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.dashboard = DashboardView(self.content, store=self.store, obs_loop=self.obs_loop,
                                        obs_client_getter=lambda: self._obs_client,
                                        steam_scanner=self.steam_scanner, post_ui=self.post_ui)
        self.settings = SettingsView(self.content, self.config_mgr, on_saved=self._on_settings_saved)
        self.views: dict[str, ctk.CTkFrame] = {"dashboard": self.dashboard, "settings": self.settings}
        self._navigate("dashboard")

        # --- Rechargement à chaud : toute vue exposant refresh_labels() est
        # notifiée à chaque changement de langue, sans jamais redémarrer
        # l'application ni recréer les widgets structurels.
        i18n.on_change(self._on_lang_changed)

        self._pump_ui_queue()

    def _on_lang_changed(self, _lang: str) -> None:
        self.title(t("APP_TITLE_WINDOW"))
        self.sidebar.refresh_labels()
        self.dashboard.refresh_labels()
        self.settings.refresh_labels()

    # -- Icône fenêtre + barre des tâches --------------------------------- #
    def _set_window_icon(self) -> None:
        if ICON_PATH.exists():
            try:
                self.iconbitmap(str(ICON_PATH))
            except Exception:
                logger.debug("iconbitmap a échoué (plateforme non-Windows ?).", exc_info=True)
        try:
            # AppUserModelID: force Windows à afficher l'icône dans la barre
            # des tâches au lieu de l'icône Python générique.
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OBSDynamics.App.1")
        except Exception:
            logger.debug("SetCurrentProcessExplicitAppUserModelID indisponible (non-Windows).", exc_info=True)

    def _open_data_folder(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(DATA_DIR))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(DATA_DIR)])
            else:
                subprocess.Popen(["xdg-open", str(DATA_DIR)])
        except Exception:
            logger.exception("Impossible d'ouvrir le dossier de données.")

    # -- File d'attente UI thread-safe ------------------------------------ #
    def post_ui(self, callback: Callable[[], None]) -> None:
        self._ui_queue.put(callback)

    def _pump_ui_queue(self) -> None:
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                logger.exception("Erreur dans un callback UI en file d'attente.")
        self.after(50, self._pump_ui_queue)

    # -- Navigation --------------------------------------------------------#
    def _navigate(self, key: str) -> None:
        for view in self.views.values():
            view.grid_forget()
        self.views[key].grid(row=0, column=0, sticky="nsew")
        self.sidebar.set_active(key)

    # -- Démarrage / arrêt de la surveillance ------------------------------#
    def _start(self) -> None:
        self.sidebar.start_btn.configure(state="disabled", text=t("SIDEBAR_BTN_START_PROGRESS"))
        self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_CONNECTING_OBS"))
        threading.Thread(target=self._start_bg, daemon=True).start()

    def _start_bg(self) -> None:
        self.obs_loop.start()
        cfg = self.config_mgr.load()
        client = OBSClient(cfg.host, cfg.port, cfg.password)
        obs_error: Optional[str] = None
        try:
            future = self.obs_loop.run_coro(client.connect())
            future.result(timeout=10)
            self._obs_client = client
        except Exception as exc:
            obs_error = str(exc)
            logger.warning("Connexion OBS échouée, la surveillance démarre quand même sans bascule de scène : %s", exc)
            self._obs_client = None
        self.scan_worker.start(on_update=lambda results: self.post_ui(lambda: self.dashboard.apply_scan_results(results)))
        self.post_ui(lambda: self._on_start_done(obs_error))

    def _on_start_done(self, obs_error: Optional[str]) -> None:
        self.sidebar.set_running_state(True)
        self.sidebar.start_btn.configure(text=t("SIDEBAR_BTN_START"))
        if obs_error:
            self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_ERROR", error=obs_error[:40]))
        else:
            self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_OK"))

    def _stop(self) -> None:
        self.sidebar.stop_btn.configure(state="disabled", text=t("SIDEBAR_BTN_STOP_PROGRESS"))
        threading.Thread(target=self._stop_bg, daemon=True).start()

    def _stop_bg(self) -> None:
        self.scan_worker.stop()
        if self._obs_client is not None:
            try:
                self.obs_loop.run_coro(self._obs_client.disconnect()).result(timeout=5)
            except Exception:
                logger.debug("Erreur déconnexion OBS à l'arrêt (ignorée).", exc_info=True)
            self._obs_client = None
        self.post_ui(self._on_stop_done)

    def _on_stop_done(self) -> None:
        self.sidebar.set_running_state(False)
        self.sidebar.stop_btn.configure(text=t("SIDEBAR_BTN_STOP"))

    def _on_settings_saved(self) -> None:
        """Reconnecte OBS avec les nouveaux paramètres si la surveillance tourne déjà."""
        if not self.scan_worker.is_running:
            return
        threading.Thread(target=self._reconnect_obs_bg, daemon=True).start()

    def _reconnect_obs_bg(self) -> None:
        cfg = self.config_mgr.load()
        if self._obs_client is not None:
            try:
                self.obs_loop.run_coro(self._obs_client.disconnect()).result(timeout=5)
            except Exception:
                logger.debug("Erreur déconnexion OBS lors de la reconnexion.", exc_info=True)
        client = OBSClient(cfg.host, cfg.port, cfg.password)
        try:
            self.obs_loop.run_coro(client.connect()).result(timeout=10)
            self._obs_client = client
            self.post_ui(lambda: self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_RECONNECTED")))
        except Exception as exc:
            self._obs_client = None
            logger.warning("Reconnexion OBS échouée : %s", exc)
            self.post_ui(lambda: self.sidebar.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING_OBS_ERROR", error=exc)))

    def _on_close(self) -> None:
        self.scan_worker.stop()
        if self._obs_client is not None:
            try:
                self.obs_loop.run_coro(self._obs_client.disconnect()).result(timeout=3)
            except Exception:
                pass
        self.obs_loop.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
