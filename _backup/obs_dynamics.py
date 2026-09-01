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
import queue
import re
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

import i18n
from core.cover_service import GameCoverService

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
i18n.init(path=BASE_DIR / "i18n.json")
from i18n import t

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
ENV_PATH = BASE_DIR / ".env"
GAMES_PATH = DATA_DIR / "games.json"
ASSETS_DIR = BASE_DIR / "assets"
ICON_PATH = ASSETS_DIR / "icon.ico"
LOG_PATH = DATA_DIR / "obs_dynamics.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("obs_dynamics")

# --- Palette ---
COL_BG = "#0d1117"
COL_SIDEBAR = "#12151c"
COL_CARD = "#161b22"
COL_BORDER = "#22272e"
COL_ACCENT = "#00e0ff"
COL_ACCENT_HOVER = "#00b8d1"
COL_TEXT_MUTED = "#8b949e"
COL_GREEN = "#2ecc71"
COL_YELLOW = "#f1c40f"
COL_RED = "#e0455c"

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


# ============================================================================
# CONFIG .env — OBS_WS_HOST / OBS_WS_PORT / OBS_WS_PASSWORD / scan params / RAWG
# ============================================================================
@dataclass
class OBSConfig:
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    scan_interval_seconds: float = 2.0
    match_threshold: float = 0.8
    rawg_api_key: str = ""
    language: str = "fr"


ENV_KEYS = {
    "host": "OBS_WS_HOST",
    "port": "OBS_WS_PORT",
    "password": "OBS_WS_PASSWORD",
    "scan_interval_seconds": "OBS_SCAN_INTERVAL_SECONDS",
    "match_threshold": "OBS_MATCH_THRESHOLD",
    "rawg_api_key": "RAWG_API_KEY",
    "language": "OBS_LANGUAGE",
}


class EnvConfigManager:
    """Lit/écrit les clés OBS_* et RAWG_* dans .env. Préserve toute autre ligne existante."""

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
                    elif key == ENV_KEYS["rawg_api_key"]:
                        cfg.rawg_api_key = value
                    elif key == ENV_KEYS["language"]:
                        if value in ("fr", "en", "es"):
                            cfg.language = value
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
                    ENV_KEYS["rawg_api_key"]: cfg.rawg_api_key,
                    ENV_KEYS["language"]: cfg.language,
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
        template = cv2.imread(path, cv2.IMREAD_COLOR)
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
            raise OBSClientError(f"Connexion OBS échouée ({url}): {exc}") from exc
        self._ws = ws
        self._connected = True
        logger.info("Connecté à OBS WebSocket sur %s", url)

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
            raise OBSClientError(f"Non connecté à OBS (requête '{request_type}' annulée).")
        request = simpleobsws.Request(request_type, request_data or {})
        response = await self._ws.call(request)
        ok = getattr(response, "ok", lambda: False)()
        if not ok:
            status = getattr(response, "requestStatus", None)
            raise OBSClientError(
                f"Requête OBS '{request_type}' refusée "
                f"(code={getattr(status, 'code', None)}, comment={getattr(status, 'comment', None)})"
            )
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
            logger.info("Scène OBS -> '%s' (jeu=%s, état=%s)", target_scene, game.name, state)
        except Exception:
            logger.exception("Échec bascule de scène OBS.")


# ============================================================================
# COMPOSANT : CARTE JEU (pastille 3 états: rouge/jaune/vert)
# ============================================================================
STATE_COLORS = {"inactive": COL_RED, "active": COL_YELLOW, "menu": COL_YELLOW, "in_game": COL_GREEN}

_STATE_I18N_MAP = {
    "inactive": "GAME_STATE_INACTIVE",
    "active": "GAME_STATE_ACTIVE",
    "menu": "GAME_STATE_MENU",
    "in_game": "GAME_STATE_IN_GAME",
}

def state_label(state: str) -> str:
    key = _STATE_I18N_MAP.get(state)
    return t(key) if key else state


import tkinter as tk

class ToolTip:
    def __init__(self, widget: Any, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: Optional[tk.Toplevel] = None
        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")

    def _on_enter(self, event: Any = None) -> None:
        self.after_id = self.widget.after(350, self._show)

    def _on_leave(self, event: Any = None) -> None:
        if hasattr(self, "after_id"):
            self.widget.after_cancel(self.after_id)
        self._hide()

    def _show(self) -> None:
        if self.tip_window:
            return
        # Centrer au-dessus du bouton
        x = self.widget.winfo_rootx() + (self.widget.winfo_width() // 2) - 50
        y = self.widget.winfo_rooty() - 32
        
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        
        frame = ctk.CTkFrame(tw, fg_color="#12151c", border_color="#2b3342", border_width=1, corner_radius=6)
        frame.pack()
        lbl = ctk.CTkLabel(frame, text=self.text, font=ctk.CTkFont(size=11, weight="bold"), text_color="#e6edf3", padx=8, pady=3)
        lbl.pack()

    def _hide(self) -> None:
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()


class GameCard(ctk.CTkFrame):
    _active_card: Optional[GameCard] = None

    def __init__(
        self,
        master,
        game: Game,
        state: str,
        on_edit: Callable[[Game], None],
        on_delete: Callable[[Game], None],
        cover_service: Optional[GameCoverService] = None,
        post_ui: Optional[Callable[[Callable[[], None]], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            fg_color="#0e1218",
            corner_radius=12,
            border_width=1,
            border_color="#1f2530",
            width=240,
            height=360,
            **kwargs,
        )
        self.grid_propagate(False)
        self.game = game
        self.game_id = game.id
        self._cover_service = cover_service
        self._post_ui = post_ui
        self._current_state = state
        self._ctk_image: Optional[ctk.CTkImage] = None
        self._on_edit = on_edit
        self._on_delete = on_delete
        self._is_open: bool = False
        self._poll_id: Optional[str] = None

        # --- Image de fond (Cover plein format 2:3) ---
        self.cover_label = ctk.CTkLabel(
            self,
            text="🎮",
            font=ctk.CTkFont(size=48),
            text_color=COL_TEXT_MUTED,
            fg_color="#0b0e14",
            corner_radius=12,
        )
        self.cover_label.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

        # --- Badge d'état supérieur droit (Glass) ---
        dot_color = STATE_COLORS.get(state, COL_RED)
        self._badge_frame = ctk.CTkFrame(
            self,
            fg_color="#0e131c",
            corner_radius=8,
            border_width=1,
            border_color="#2b3342",
        )
        self._badge_frame.place(relx=1.0, rely=0.0, x=-10, y=10, anchor="ne")

        self._dot = ctk.CTkLabel(self._badge_frame, text="●", font=ctk.CTkFont(size=12), text_color=dot_color)
        self._dot.pack(side="left", padx=(8, 3), pady=3)
        self._status_lbl = ctk.CTkLabel(
            self._badge_frame,
            text=state_label(state),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=dot_color,
        )
        self._status_lbl.pack(side="left", padx=(0, 8), pady=3)

        # --- Pill de titre par défaut (en bas, style Steam playtime) ---
        display_name = game.name if len(game.name) <= 24 else game.name[:22] + "…"
        self._default_pill = ctk.CTkFrame(
            self,
            fg_color="#0e131c",
            corner_radius=8,
            border_width=1,
            border_color="#2b3342",
        )
        self._default_pill_lbl = ctk.CTkLabel(
            self._default_pill,
            text=display_name,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#e6edf3",
        )
        self._default_pill_lbl.pack(padx=12, pady=5)
        self._default_pill.place(relx=0.5, rely=1.0, y=-12, anchor="s")

        # --- Overlay Glass d'actions au survol (Invisible par défaut) ---
        self.glass_overlay = ctk.CTkFrame(
            self,
            fg_color="#090d14",
            corner_radius=12,
            border_width=1,
            border_color="#364254",
        )

        self._overlay_name = ctk.CTkLabel(
            self.glass_overlay,
            text=game.name,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="#ffffff",
            wraplength=210,
            justify="center",
        )
        self._overlay_name.pack(padx=12, pady=(12, 3))

        source_txt = t("GAME_SOURCE_STEAM") if game.source == "steam" else t("GAME_MODAL_SOURCE_MANUAL")
        self._overlay_sub = ctk.CTkLabel(
            self.glass_overlay,
            text=source_txt,
            font=ctk.CTkFont(size=11),
            text_color=COL_TEXT_MUTED,
        )
        self._overlay_sub.pack(pady=(0, 10))

        btn_row = ctk.CTkFrame(self.glass_overlay, fg_color="transparent")
        btn_row.pack(padx=10, pady=(0, 14))

        self.edit_btn = ctk.CTkButton(
            btn_row,
            text="✏",
            width=48,
            height=36,
            fg_color="#19202c",
            hover_color="#2b364a",
            text_color="#ffffff",
            font=ctk.CTkFont(size=16),
            corner_radius=6,
            command=lambda: self._on_edit(self.game),
        )
        self.edit_btn.pack(side="left", padx=4)
        ToolTip(self.edit_btn, t("GAME_CARD_TOOLTIP_EDIT"))

        self.reload_btn = ctk.CTkButton(
            btn_row,
            text="⟳",
            width=48,
            height=36,
            fg_color="#19202c",
            hover_color="#2b364a",
            text_color="#ffffff",
            font=ctk.CTkFont(size=16),
            corner_radius=6,
            command=self.reload_cover,
        )
        self.reload_btn.pack(side="left", padx=4)
        ToolTip(self.reload_btn, t("GAME_CARD_TOOLTIP_RELOAD"))

        self.del_btn = ctk.CTkButton(
            btn_row,
            text="✕",
            width=48,
            height=36,
            fg_color="#19202c",
            hover_color=COL_RED,
            text_color="#ffffff",
            font=ctk.CTkFont(size=16),
            corner_radius=6,
            command=lambda: self._on_delete(self.game),
        )
        self.del_btn.pack(side="left", padx=4)
        ToolTip(self.del_btn, t("GAME_CARD_TOOLTIP_DELETE"))

        # Lier les événements de survol (Hover Glass Effect)
        self._bind_hover(self)

        # Chargement de la jaquette
        self._load_cover()

    def _bind_hover(self, widget: Any) -> None:
        def on_enter(e=None):
            self._show_overlay()

        def on_leave(e=None):
            self.after(30, self._check_leave)

        try:
            self.bind("<Enter>", on_enter, add="+")
            self.bind("<Leave>", on_leave, add="+")
            self.cover_label.bind("<Enter>", on_enter, add="+")
            self.cover_label.bind("<Leave>", on_leave, add="+")
        except Exception:
            pass

    def _is_mouse_inside(self) -> bool:
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            rx = self.winfo_rootx()
            ry = self.winfo_rooty()
            rw = self.winfo_width()
            rh = self.winfo_height()
            return (rx <= x <= rx + rw) and (ry <= y <= ry + rh)
        except Exception:
            return False

    def _show_overlay(self) -> None:
        if self._is_open:
            return

        # Ferme immédiatement toute autre carte ouverte (garantie d'un seul overlay actif)
        if GameCard._active_card is not None and GameCard._active_card is not self:
            GameCard._active_card._hide_overlay()
        GameCard._active_card = self

        self._is_open = True
        self.configure(border_color=COL_ACCENT)
        self._default_pill.place_forget()
        self.glass_overlay.place(relx=0.0, rely=1.0, relwidth=1.0, y=0, anchor="sw")

        # Surveille en continu la position de la souris tant que l'overlay est visible
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        if self._is_open:
            self._poll_id = self.after(50, self._poll_hover)

    def _poll_hover(self) -> None:
        self._poll_id = None
        if not self._is_open or GameCard._active_card is not self:
            return
        if not self._is_mouse_inside():
            self._hide_overlay()
        else:
            self._schedule_poll()

    def _check_leave(self) -> None:
        if not self._is_mouse_inside():
            self._hide_overlay()

    def _hide_overlay(self) -> None:
        if not self._is_open:
            return

        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None

        if GameCard._active_card is self:
            GameCard._active_card = None

        self._is_open = False
        self.configure(border_color="#1f2530")
        self.glass_overlay.place_forget()
        self._default_pill.place(relx=0.5, rely=1.0, y=-12, anchor="s")

    def _load_cover(self, force: bool = False) -> None:
        if self._cover_service is None:
            return
        self._cover_service.request_cover(self.game, self._on_cover_received, force=force)

    def reload_cover(self) -> None:
        self.cover_label.configure(image="", text="⏳")
        self._load_cover(force=True)

    def _on_cover_received(self, pil_image: Optional[Image.Image]) -> None:
        def _apply() -> None:
            try:
                if not self.winfo_exists():
                    return
                if pil_image is None:
                    self.cover_label.configure(image="", text="🎮")
                else:
                    self._apply_cover_image(pil_image)
            except Exception:
                pass

        if self._post_ui is not None:
            self._post_ui(_apply)
        else:
            try:
                self.after(0, _apply)
            except Exception:
                pass

    def _apply_cover_image(self, pil_image: Image.Image) -> None:
        try:
            self._ctk_image = ctk.CTkImage(
                light_image=pil_image,
                dark_image=pil_image,
                size=(240, 360),
            )
            self.cover_label.configure(image=self._ctk_image, text="")
        except Exception:
            logger.debug("Erreur lors de l'application de l'image de cover pour %s", self.game.name)

    def set_state(self, state: str) -> None:
        """Met à jour uniquement la pastille d'état, sans recréer le widget
        (appelé à chaque cycle de scan — doit rester O(1) et sans flicker)."""
        if state == self._current_state:
            return
        self._current_state = state
        color = STATE_COLORS.get(state, COL_RED)
        self._dot.configure(text_color=color)
        self._status_lbl.configure(text=state_label(state), text_color=color)


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
                            value="steam", command=self._toggle_source).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(source_row, text=t("GAME_MODAL_SOURCE_MANUAL"), variable=self.source_var,
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
        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_IMAGES_MENU"), font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.menu_list_lbl = ctk.CTkLabel(scroll, text=self._images_summary(self._menu_images),
                                           text_color=COL_TEXT_MUTED, anchor="w", font=ctk.CTkFont(size=11))
        self.menu_list_lbl.grid(row=5, column=0, sticky="w")
        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_PICK_MENU_IMAGES"), height=30,
                       command=lambda: self._pick_images("menu")).grid(row=6, column=0, sticky="ew", pady=(4, 10))

        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_IMAGES_INGAME"), font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=7, column=0, sticky="w", pady=(4, 2))
        self.ingame_list_lbl = ctk.CTkLabel(scroll, text=self._images_summary(self._ingame_images),
                                             text_color=COL_TEXT_MUTED, anchor="w", font=ctk.CTkFont(size=11))
        self.ingame_list_lbl.grid(row=8, column=0, sticky="w")
        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_PICK_INGAME_IMAGES"), height=30,
                       command=lambda: self._pick_images("ingame")).grid(row=9, column=0, sticky="ew", pady=(4, 10))

        # --- Scènes OBS ---
        scene_names = self._fetch_scene_names()
        self.scene_menu_var = ctk.StringVar(value=game.obs_scene_menu if game else "")
        self.scene_ingame_var = ctk.StringVar(value=game.obs_scene_ingame if game else "")
        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_SCENE_MENU"), font=ctk.CTkFont(size=12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=10, column=0, sticky="w", pady=(6, 2))
        self._scene_selector(scroll, 11, scene_names, self.scene_menu_var)
        ctk.CTkLabel(scroll, text=t("GAME_MODAL_LABEL_SCENE_INGAME"), font=ctk.CTkFont(size=12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=12, column=0, sticky="w", pady=(6, 2))
        self._scene_selector(scroll, 13, scene_names, self.scene_ingame_var)

        self.msg_lbl = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=11))
        self.msg_lbl.grid(row=14, column=0, sticky="w", pady=(10, 0))

        ctk.CTkButton(scroll, text=t("GAME_MODAL_BTN_SAVE"), height=38, fg_color=COL_ACCENT,
                       hover_color=COL_ACCENT_HOVER, text_color="#0d1117",
                       font=ctk.CTkFont(weight="bold"), command=self._save).grid(
            row=15, column=0, sticky="ew", pady=(16, 0))

        self._toggle_source()

    @staticmethod
    def _images_summary(imgs: list[str]) -> str:
        if not imgs:
            return t("GAME_MODAL_IMAGES_NONE")
        names = [Path(p).name for p in imgs]
        if len(names) <= 2:
            return ", ".join(names)
        return f"{names[0]}, {names[1]} (+{len(names)-2})"

    def _labeled_entry(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=row, column=0, sticky="w", pady=(6, 2))
        ctk.CTkEntry(parent, textvariable=var, height=34, fg_color=COL_CARD,
                     border_color=COL_BORDER).grid(row=row, column=0, sticky="ew", pady=(0, 6))

    def _scene_selector(self, parent, row: int, scene_names: list[str], var: ctk.StringVar) -> None:
        none_label = t("GAME_MODAL_SCENE_NONE")
        values = [none_label] + scene_names if scene_names else [none_label]
        initial = var.get() if (var.get() and var.get() in values) else none_label
        var.set(initial)
        ctk.CTkOptionMenu(parent, values=values, variable=var, fg_color=COL_CARD,
                          button_color=COL_ACCENT, button_hover_color=COL_ACCENT_HOVER
                          ).grid(row=row, column=0, sticky="ew", pady=(0, 6))

    def _fetch_scene_names(self) -> list[str]:
        client = self._get_obs_client()
        if client is None or not client.is_connected:
            return []
        try:
            future = self._obs_loop.run_coro(client.get_scene_list())
            return future.result(timeout=3)
        except Exception:
            logger.exception("Échec récupération des scènes OBS pour le modal.")
            return []

    def _toggle_source(self) -> None:
        is_steam = self.source_var.get() == "steam"
        if is_steam:
            self.steam_menu.grid()
        else:
            self.steam_menu.grid_remove()

    def _pick_images(self, kind: str) -> None:
        files = filedialog.askopenfilenames(
            title=t("GAME_MODAL_FILEDIALOG_TITLE"),
            filetypes=[(t("GAME_MODAL_FILEDIALOG_FILTER_LABEL"), "*.png"), (t("GAME_MODAL_FILEDIALOG_ALL_FILES"), "*.*")],
        )
        if not files:
            return
        if kind == "menu":
            self._menu_images = list(files)
            self.menu_list_lbl.configure(text=self._images_summary(self._menu_images))
        else:
            self._ingame_images = list(files)
            self.ingame_list_lbl.configure(text=self._images_summary(self._ingame_images))

    def _save(self) -> None:
        source = self.source_var.get()
        if source == "steam":
            selected = self.steam_var.get()
            match = next((g for g in self._steam_candidates if f"{g['name']} (appid {g['appid']})" == selected), None)
            if not match and not self._game:
                self.msg_lbl.configure(text=t("GAME_MODAL_ERR_INVALID_STEAM_SELECTION"), text_color=COL_RED)
                return
            name = match["name"] if match else (self._game.name if self._game else "")
            appid = match["appid"] if match else (self._game.appid if self._game else "")
            active_match = match["install_dir"] if match else (self._game.active_match if self._game else "")
        else:
            name = self.name_var.get().strip()
            exe = self.exe_var.get().strip()
            if not name or not exe:
                self.msg_lbl.configure(text=t("GAME_MODAL_ERR_MISSING_NAME_EXE"), text_color=COL_RED)
                return
            appid = ""
            active_match = exe

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
# VUE : DASHBOARD
# ============================================================================
class DashboardView(ctk.CTkFrame):
    def __init__(self, master, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 steam_scanner: SteamScanner, post_ui: Callable[[Callable[[], None]], None],
                 cover_service: Optional[GameCoverService] = None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._store = store
        self._obs_loop = obs_loop
        self._get_obs_client = obs_client_getter
        self._steam_scanner = steam_scanner
        self._post_ui = post_ui
        self._cover_service = cover_service
        self._latest_states: dict[str, str] = {}
        self._steam_candidates: list[dict[str, str]] = []
        self._cards: dict[str, GameCard] = {}
        self._rendered_ids: tuple[str, ...] = ()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text=t("DASHBOARD_TITLE"), font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="w")

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text=t("DASHBOARD_BTN_ADD"), width=110, height=34, fg_color=COL_CARD,
                      hover_color=COL_BORDER, command=self._open_add_modal).pack(side="left", padx=(0, 8))
        self.scan_btn = ctk.CTkButton(btns, text=t("DASHBOARD_BTN_SCAN_STEAM"), width=150, height=34,
                                       fg_color=COL_ACCENT, hover_color=COL_ACCENT_HOVER,
                                       text_color="#0d1117", font=ctk.CTkFont(weight="bold"),
                                       command=self.scan_steam_library)
        self.scan_btn.pack(side="left")

        self.status_lbl = ctk.CTkLabel(self, text="", font=ctk.CTkFont(size=11),
                                        text_color=COL_TEXT_MUTED, anchor="w")
        self.status_lbl.grid(row=1, column=0, sticky="w", padx=26)

        self.scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=10)
        for c in range(3):
            self.scroll.grid_columnconfigure(c, weight=1)
        self._enable_smooth_scroll(self.scroll)

        self.render_games()

    @staticmethod
    def _enable_smooth_scroll(scrollable: ctk.CTkScrollableFrame) -> None:
        """Remplace le gestionnaire de molette interne pour synchroniser le rafraîchissement
        du canvas et éliminer définitivement l'effet de rémanence / fondu (ghosting)."""
        canvas = getattr(scrollable, "_parent_canvas", None)
        if canvas is None:
            return

        def _custom_mouse_wheel(event: Any) -> str:
            if not scrollable._check_if_valid_scroll(event.widget):
                return ""
            if event.delta:
                # Ferme immédiatement toute carte active pour éviter les calculs pendant le défilement
                if GameCard._active_card is not None:
                    GameCard._active_card._hide_overlay()

                # Défilement réactif et fluide
                units = -int(event.delta / 4)
                if canvas.yview() != (0.0, 1.0):
                    canvas.yview_scroll(units, "units")
                    # Force la synchronisation immédiate de tous les composants enfants (cartes/images)
                    canvas.update_idletasks()
                return "break"
            return ""

        scrollable._mouse_wheel_all = _custom_mouse_wheel

    def _clear(self) -> None:
        for widget in self.scroll.winfo_children():
            widget.destroy()
        self._cards.clear()

    def render_games(self) -> None:
        """Reconstruction complète de la grille — appelée uniquement quand la
        LISTE des jeux change réellement (ajout/suppression/réordonnancement),
        jamais à chaque cycle de scan. Voir apply_scan_results()."""
        self._clear()
        games = self._store.load()
        self._rendered_ids = tuple(g.id for g in games)
        if not games:
            ctk.CTkLabel(self.scroll, text=t("DASHBOARD_EMPTY_STATE"),
                         text_color=COL_TEXT_MUTED).grid(row=0, column=0, padx=10, pady=20)
            return
        for i, game in enumerate(games):
            state = self._latest_states.get(game.id, "inactive")
            card = GameCard(self.scroll, game=game, state=state,
                             on_edit=self._open_edit_modal, on_delete=self._delete_game,
                             cover_service=self._cover_service, post_ui=self._post_ui)
            card.grid(row=i // 3, column=i % 3, padx=12, pady=14)
            self._cards[game.id] = card

    def apply_scan_results(self, results: list[dict[str, Any]]) -> None:
        """Appelé depuis ScanWorker (via la file UI thread-safe) à chaque cycle
        (toutes les 0.5-2s selon config). CRITIQUE : ne doit JAMAIS détruire/
        recréer les widgets si la liste de jeux n'a pas changé, sous peine de
        provoquer le flicker + reset du scroll de CTkScrollableFrame observés
        précédemment. Diff par ID : rebuild complet seulement si le set/ordre
        des jeux a changé, sinon simple mise à jour de la pastille d'état."""
        new_states = {r["id"]: r["state"] for r in results}
        active_count = sum(1 for r in results if r["active"])
        self.status_lbl.configure(
            text=t("DASHBOARD_STATUS_SUMMARY", count=len(results), active=active_count), text_color=COL_TEXT_MUTED
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
    def __init__(self, master, config_mgr: EnvConfigManager, on_saved: Callable[[], None],
                 on_lang_change: Optional[Callable[[str], None]] = None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.config_mgr = config_mgr
        self.on_saved = on_saved
        self._on_lang_change_cb = on_lang_change
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text=t("SETTINGS_TITLE"), font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=24, pady=(24, 16))

        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14, border_width=1, border_color=COL_BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=24)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text=t("SETTINGS_SECTION_OBS_WS"), font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self.host_var = ctk.StringVar()
        self.port_var = ctk.StringVar()
        self.pwd_var = ctk.StringVar()
        self.interval_var = ctk.StringVar()
        self.threshold_var = ctk.StringVar()
        self.rawg_api_key_var = ctk.StringVar()

        self._field(card, 1, t("SETTINGS_LABEL_HOST"), self.host_var)
        self._field(card, 2, t("SETTINGS_LABEL_PORT"), self.port_var)
        self._password_field(card, 3, t("SETTINGS_LABEL_PASSWORD"), self.pwd_var)
        self._field(card, 4, t("SETTINGS_LABEL_SCAN_INTERVAL"), self.interval_var)
        self._field(card, 5, t("SETTINGS_LABEL_MATCH_THRESHOLD"), self.threshold_var)

        # Section Base de données RAWG
        ctk.CTkLabel(card, text=t("SETTINGS_SECTION_RAWG_DB"), font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 6))
        self._field(card, 7, t("SETTINGS_LABEL_RAWG_API_KEY"), self.rawg_api_key_var)

        # Section Langue
        ctk.CTkLabel(card, text=t("SETTINGS_SECTION_LANGUAGE"), font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=10, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 6))
        ctk.CTkLabel(card, text=t("SETTINGS_LABEL_LANGUAGE"), font=ctk.CTkFont(size=12), text_color=COL_TEXT_MUTED
                     ).grid(row=11, column=0, sticky="w", padx=20, pady=6)
        self.lang_var = ctk.StringVar(value="fr")
        self.lang_segment = ctk.CTkSegmentedButton(
            card, values=["fr", "en", "es"], variable=self.lang_var,
            selected_color="#3b82f6", selected_hover_color="#2563eb",
            unselected_color=COL_BG, unselected_hover_color=COL_BORDER,
            text_color="#ffffff", font=ctk.CTkFont(size=12, weight="bold"),
            command=self._on_lang_change,
        )
        self.lang_segment.grid(row=11, column=1, sticky="e", padx=20, pady=6)

        self.msg_lbl = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11))
        self.msg_lbl.grid(row=12, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 0))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=13, column=0, columnspan=2, sticky="ew", padx=20, pady=18)
        ctk.CTkButton(btn_row, text=t("SETTINGS_BTN_SAVE"), width=150, height=36, fg_color=COL_ACCENT,
                      hover_color=COL_ACCENT_HOVER, text_color="#0d1117",
                      font=ctk.CTkFont(weight="bold"), command=self._save).pack(side="left")

        self._load_into_form()

    def _field(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12), text_color=COL_TEXT_MUTED
                     ).grid(row=row, column=0, sticky="w", padx=20, pady=6)
        ctk.CTkEntry(parent, textvariable=var, width=220, height=34, fg_color=COL_BG,
                     border_color=COL_BORDER).grid(row=row, column=1, sticky="e", padx=20, pady=6)

    def _password_field(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12), text_color=COL_TEXT_MUTED
                     ).grid(row=row, column=0, sticky="w", padx=20, pady=6)
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=1, sticky="e", padx=20, pady=6)
        self._pwd_entry = ctk.CTkEntry(wrapper, textvariable=var, width=180, height=34, show="•",
                                        fg_color=COL_BG, border_color=COL_BORDER)
        self._pwd_entry.pack(side="left")
        self._pwd_visible = False
        ctk.CTkButton(wrapper, text="👁", width=34, height=34, fg_color=COL_BG,
                      hover_color=COL_BORDER, command=self._toggle_pwd).pack(side="left", padx=(4, 0))

    def _toggle_pwd(self) -> None:
        self._pwd_visible = not self._pwd_visible
        self._pwd_entry.configure(show="" if self._pwd_visible else "•")

    def _on_lang_change(self, lang: str) -> None:
        i18n.set_lang(lang)
        if self._on_lang_change_cb is not None:
            self._on_lang_change_cb(lang)

    def _load_into_form(self) -> None:
        cfg = self.config_mgr.load()
        self.host_var.set(cfg.host)
        self.port_var.set(str(cfg.port))
        self.pwd_var.set(cfg.password)
        self.interval_var.set(str(cfg.scan_interval_seconds))
        self.threshold_var.set(str(cfg.match_threshold))
        self.rawg_api_key_var.set(cfg.rawg_api_key)
        self.lang_var.set(cfg.language)

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
            rawg_api_key=self.rawg_api_key_var.get().strip(),
            language=self.lang_var.get(),
        )
        if self.config_mgr.save(cfg):
            self.msg_lbl.configure(text=t("SETTINGS_SAVE_SUCCESS"), text_color=COL_GREEN)
            self.on_saved()
        else:
            self.msg_lbl.configure(text=t("SETTINGS_SAVE_FAILED"), text_color=COL_RED)


# ============================================================================
# SIDEBAR
# ============================================================================
class Sidebar(ctk.CTkFrame):
    def __init__(self, master, on_nav: Callable[[str], None],
                 on_start: Callable[[], None], on_stop: Callable[[], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_SIDEBAR, corner_radius=0, width=230, **kwargs)
        self.grid_propagate(False)
        self.grid_rowconfigure(6, weight=1)
        self.on_nav = on_nav

        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(24, 28))
        ctk.CTkLabel(brand, text=t("SIDEBAR_BRAND_ICON"), font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=COL_ACCENT).pack(side="left")
        ctk.CTkLabel(brand, text=t("SIDEBAR_BRAND_SUFFIX"), font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_btn("dashboard", t("SIDEBAR_NAV_DASHBOARD"), row=1)
        self._nav_btn("settings", t("SIDEBAR_NAV_SETTINGS"), row=2)

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=6, column=0, sticky="sew", padx=16, pady=20)

        self.status_dot = ctk.CTkLabel(ctrl, text="●", text_color=COL_RED, font=ctk.CTkFont(size=14))
        self.status_dot.pack(anchor="w")
        self.status_text = ctk.CTkLabel(ctrl, text=t("SIDEBAR_STATUS_STOPPED"), font=ctk.CTkFont(size=11),
                                         text_color=COL_TEXT_MUTED, wraplength=190, justify="left", anchor="w")
        self.status_text.pack(anchor="w", pady=(0, 10), fill="x")

        self.start_btn = ctk.CTkButton(ctrl, text=t("SIDEBAR_BTN_START"), height=36, fg_color=COL_GREEN,
                                        hover_color="#27ae60", font=ctk.CTkFont(size=12),
                                        command=on_start)
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ctk.CTkButton(ctrl, text=t("SIDEBAR_BTN_STOP"), height=36, fg_color=COL_RED,
                                       hover_color="#c0392b", font=ctk.CTkFont(size=12),
                                       command=on_stop, state="disabled")
        self.stop_btn.pack(fill="x", pady=2)

    def _nav_btn(self, key: str, text: str, row: int) -> None:
        btn = ctk.CTkButton(self, text=text, anchor="w", height=40, corner_radius=8,
                             fg_color="transparent", hover_color=COL_CARD, font=ctk.CTkFont(size=13),
                             command=lambda: self.on_nav(key))
        btn.grid(row=row, column=0, sticky="ew", padx=12, pady=3)
        self.nav_buttons[key] = btn

    def set_active(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=COL_CARD if k == key else "transparent")

    def set_running_state(self, running: bool) -> None:
        self.status_dot.configure(text_color=COL_GREEN if running else COL_RED)
        self.status_text.configure(text=t("SIDEBAR_STATUS_RUNNING") if running else t("SIDEBAR_STATUS_STOPPED"))
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")


# ============================================================================
# APPLICATION PRINCIPALE
# ============================================================================
class HotkeyManager:
    """Gestionnaire de hotkeys globales via pynput.
    Écoute les touches du clavier système et toggles les scènes OBS correspondantes.
    Fonctionne en arrière-plan sans bloquer l'interface."""
    
    def __init__(self, obs_client_getter: Callable[[], Optional[OBSClient]],
                 on_hotkey: Callable[[str], None]) -> None:
        self._obs_client_getter = obs_client_getter
        self._on_hotkey = on_hotkey
        self._listener: Optional[Thread] = None
        self._running = False
        # Hotkeys par défaut : F1 = In-game, F2 = Menu, F3 = Inactif
        self._hotkeys = {
            "F1": "in_game",
            "F2": "menu",
            "F3": "inactive",
        }
    
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._listener = Thread(target=self._run_listener, daemon=True, name="hotkeys-listener")
        self._listener.start()
        logger.info("Hotkeys démarrées (F1=En jeu, F2=Menu, F3=Inactif)")
    
    def stop(self) -> None:
        self._running = False
        if self._listener is not None:
            self._listener.join(timeout=3)
            self._listener = None
        logger.info("Hotkeys arrêtées")
    
    def _run_listener(self) -> None:
        try:
            with Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            ) as listener:
                while self._running:
                    time.sleep(0.1)
        except Exception as exc:
            logger.debug("Erreur gestionnaire de hotkeys : %s", exc)
    
    def _on_press(self, key: keyboard.Key) -> None:
        try:
            key_str = key.name
        except AttributeError:
            key_str = str(key)
        
        if key_str in self._hotkeys and self._running:
            state = self._hotkeys[key_str]
            self._on_hotkey(state)
    
    def _on_release(self, key: keyboard.Key) -> None:
        pass


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(t("APP_TITLE_WINDOW"))
        self.geometry("1100x750")
        self.minsize(920, 620)
        self.configure(fg_color=COL_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        self.config_mgr = EnvConfigManager(ENV_PATH)
        cfg = self.config_mgr.load()
        i18n.set_lang(cfg.language)
        self.store = GameStore(GAMES_PATH)
        self.steam_scanner = SteamScanner()
        self.covers_dir = DATA_DIR / "covers"
        self.cover_service = GameCoverService(self.covers_dir, self.config_mgr.load().rawg_api_key)
        self.obs_loop = AsyncLoopThread()
        self._obs_client: Optional[OBSClient] = None
        self.scan_worker = ScanWorker(self.store, self.obs_loop, lambda: self._obs_client,
                                       self.config_mgr, self.post_ui)

        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = Sidebar(self, on_nav=self._navigate, on_start=self._start, on_stop=self._stop)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.dashboard = DashboardView(self.content, store=self.store, obs_loop=self.obs_loop,
                                        obs_client_getter=lambda: self._obs_client,
                                        steam_scanner=self.steam_scanner, post_ui=self.post_ui,
                                        cover_service=self.cover_service)
        self.settings = SettingsView(self.content, self.config_mgr, on_saved=self._on_settings_saved,
                                     on_lang_change=self._on_lang_changed)
        self.views: dict[str, ctk.CTkFrame] = {"dashboard": self.dashboard, "settings": self.settings}
        self._navigate("dashboard")
        self._pump_ui_queue()

        # Initialisation du gestionnaire de hotkeys dynamiques
        self._hotkey_manager = HotkeyManager(
            obs_client_getter=lambda: self._obs_client,
            on_hotkey=self._on_hotkey_pressed,
        )

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
        self._hotkey_manager.start()

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

    def _on_hotkey_pressed(self, state: str) -> None:
        """Appelé quand une hotkey est pressée : bascule la scène OBS correspondante."""
        client = self._obs_client
        if client is None or not client.is_connected:
            logger.debug("Hotkey pressée mais non connecté à OBS.")
            return
        try:
            # Mapping des états vers les noms de scène par défaut
            scene_map = {
                "in_game": "En jeu",
                "menu": "Menu",
                "inactive": "Inactif",
            }
            scene_name = scene_map.get(state)
            if scene_name:
                self.obs_loop.run_coro(client.set_current_scene(scene_name))
                logger.info("Hotkey pressée -> scène OBS : '%s'", scene_name)
        except Exception as exc:
            logger.debug("Erreur bascule de scène par hotkey : %s", exc)

    def _on_settings_saved(self) -> None:
        """Met à jour les services et reconnecte OBS si la surveillance tourne."""
        cfg = self.config_mgr.load()
        self.cover_service.set_api_key(cfg.rawg_api_key)
        if not self.scan_worker.is_running:
            return
        threading.Thread(target=self._reconnect_obs_bg, daemon=True).start()

    def _on_lang_changed(self, lang: str) -> None:
        """Reconstruit toutes les vues avec la nouvelle langue."""
        current_view = None
        for key, view in self.views.items():
            if view.winfo_ismapped():
                current_view = key
                view.grid_forget()
                view.destroy()
        self.dashboard = DashboardView(self.content, store=self.store, obs_loop=self.obs_loop,
                                        obs_client_getter=lambda: self._obs_client,
                                        steam_scanner=self.steam_scanner, post_ui=self.post_ui,
                                        cover_service=self.cover_service)
        self.settings = SettingsView(self.content, self.config_mgr, on_saved=self._on_settings_saved,
                                     on_lang_change=self._on_lang_changed)
        self.views = {"dashboard": self.dashboard, "settings": self.settings}
        if current_view:
            self._navigate(current_view)

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


# ============================================================================
# HOTKEYS DYNAMIQUES (pynput + simpleobsws)
# ============================================================================
# Hook les hotkeys dans le ScanWorker ou l'App
# Lorsqu'un hotkey est pressé, on bascule la scène OBS correspondante
