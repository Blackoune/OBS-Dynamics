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
# CONFIG .env — OBS_WS_HOST / OBS_WS_PORT / OBS_WS_PASSWORD / scan params
# ============================================================================
@dataclass
class OBSConfig:
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    scan_interval_seconds: float = 2.0
    match_threshold: float = 0.8


ENV_KEYS = {
    "host": "OBS_WS_HOST",
    "port": "OBS_WS_PORT",
    "password": "OBS_WS_PASSWORD",
    "scan_interval_seconds": "OBS_SCAN_INTERVAL_SECONDS",
    "match_threshold": "OBS_MATCH_THRESHOLD",
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
                return [Game.from_dict(g) for g in raw.get("games", [])]
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
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

    async def create_scene(self, scene_name: str) -> None:
        try:
            await self.call("CreateScene", {"sceneName": scene_name})
        except OBSClientError as e:
            logger.warning(f"Erreur lors de la création de la scène '{scene_name}' (elle existe peut-être déjà) : {e}")

    async def create_input(self, scene_name: str, input_name: str, input_kind: str, input_settings: dict[str, Any]) -> None:
        try:
            await self.call("CreateInput", {
                "sceneName": scene_name,
                "inputName": input_name,
                "inputKind": input_kind,
                "inputSettings": input_settings
            })
        except OBSClientError as e:
            logger.warning(f"Erreur lors de la création de la source '{input_name}' : {e}")

    async def create_scene_item(self, scene_name: str, source_name: str) -> None:
        try:
            await self.call("CreateSceneItem", {
                "sceneName": scene_name,
                "sourceName": source_name
            })
        except OBSClientError as e:
            logger.warning(f"Erreur ajout '{source_name}' dans '{scene_name}' : {e}")


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
STATE_LABELS = {"inactive": "Inactif", "active": "Actif", "menu": "Menu", "in_game": "En jeu"}


class GameCard(ctk.CTkFrame):
    def __init__(self, master, game: Game, state: str,
                 on_edit: Callable[[Game], None], on_delete: Callable[[Game], None], **kwargs) -> None:
        super().__init__(master, fg_color=COL_CARD, corner_radius=12,
                          border_width=1, border_color=COL_BORDER, **kwargs)
        self.grid_columnconfigure(0, weight=1)

        icon = ctk.CTkLabel(self, text="🎮", font=ctk.CTkFont(size=26))
        icon.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 4))

        name_lbl = ctk.CTkLabel(self, text=game.name, font=ctk.CTkFont(size=15, weight="bold"), anchor="w")
        name_lbl.grid(row=1, column=0, sticky="w", padx=14)

        source_txt = "Steam" if game.source == "steam" else "Manuel"
        sub_lbl = ctk.CTkLabel(self, text=source_txt, font=ctk.CTkFont(size=11),
                                text_color=COL_TEXT_MUTED, anchor="w")
        sub_lbl.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 12))
        ctk.CTkButton(btn_row, text="✎", width=32, height=26, fg_color=COL_BG,
                       hover_color=COL_BORDER, command=lambda: on_edit(game)).pack(side="left", padx=2)
        ctk.CTkButton(btn_row, text="🗑", width=32, height=26, fg_color=COL_BG,
                       hover_color=COL_RED, command=lambda: on_delete(game)).pack(side="left", padx=2)

        dot_color = STATE_COLORS.get(state, COL_RED)
        dot = ctk.CTkLabel(self, text="●", font=ctk.CTkFont(size=16), text_color=dot_color)
        dot.place(relx=1.0, rely=0.0, x=-12, y=10, anchor="ne")
        status_lbl = ctk.CTkLabel(self, text=STATE_LABELS.get(state, state), font=ctk.CTkFont(size=9),
                                   text_color=dot_color)
        status_lbl.place(relx=1.0, rely=0.0, x=-14, y=28, anchor="ne")


# ============================================================================
# MODAL : AJOUTER / MODIFIER UN JEU
# ============================================================================
class GameModal(ctk.CTkToplevel):
    def __init__(self, master, store: GameStore, obs_loop: AsyncLoopThread,
                 obs_client_getter: Callable[[], Optional[OBSClient]],
                 on_saved: Callable[[], None], steam_candidates: list[dict[str, str]],
                 game: Optional[Game] = None) -> None:
        super().__init__(master)
        self.title("Modifier le jeu" if game else "Ajouter un jeu")
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
        ctk.CTkRadioButton(source_row, text="Jeu Steam détecté", variable=self.source_var,
                            value="steam", command=self._toggle_source).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(source_row, text="Manuel (exe)", variable=self.source_var,
                            value="manual", command=self._toggle_source).pack(side="left")

        # --- Steam: dropdown des jeux détectés non encore ajoutés ---
        self.steam_var = ctk.StringVar()
        steam_names = [f"{g['name']} (appid {g['appid']})" for g in steam_candidates] or ["Aucun jeu Steam détecté — lance un scan"]
        self.steam_menu = ctk.CTkOptionMenu(scroll, values=steam_names, variable=self.steam_var,
                                             fg_color=COL_BG, button_color=COL_ACCENT,
                                             button_hover_color=COL_ACCENT_HOVER)
        self.steam_menu.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        # --- Manuel: nom + exe ---
        self.name_var = ctk.StringVar(value=game.name if game else "")
        self.exe_var = ctk.StringVar(value=game.active_match if (game and game.source == "manual") else "")
        self._labeled_entry(scroll, 2, "Nom du jeu", self.name_var)
        self._labeled_entry(scroll, 3, "Nom de l'exécutable (ex: VALORANT-Win64-Shipping.exe)", self.exe_var)

        # --- Images ---
        ctk.CTkLabel(scroll, text="Images de détection — Menu", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.menu_list_lbl = ctk.CTkLabel(scroll, text=self._images_summary(self._menu_images),
                                           text_color=COL_TEXT_MUTED, anchor="w", font=ctk.CTkFont(size=11))
        self.menu_list_lbl.grid(row=5, column=0, sticky="w")
        ctk.CTkButton(scroll, text="📁 Choisir images Menu", height=30,
                       command=lambda: self._pick_images("menu")).grid(row=6, column=0, sticky="ew", pady=(4, 10))

        ctk.CTkLabel(scroll, text="Images de détection — En jeu", font=ctk.CTkFont(size=12, weight="bold"),
                     anchor="w").grid(row=7, column=0, sticky="w", pady=(4, 2))
        self.ingame_list_lbl = ctk.CTkLabel(scroll, text=self._images_summary(self._ingame_images),
                                             text_color=COL_TEXT_MUTED, anchor="w", font=ctk.CTkFont(size=11))
        self.ingame_list_lbl.grid(row=8, column=0, sticky="w")
        ctk.CTkButton(scroll, text="📁 Choisir images En jeu", height=30,
                       command=lambda: self._pick_images("ingame")).grid(row=9, column=0, sticky="ew", pady=(4, 10))

        # --- Scènes OBS ---
        scene_names = self._fetch_scene_names()
        self.scene_menu_var = ctk.StringVar(value=game.obs_scene_menu if game else "")
        self.scene_ingame_var = ctk.StringVar(value=game.obs_scene_ingame if game else "")
        ctk.CTkLabel(scroll, text="Scène OBS — Menu", font=ctk.CTkFont(size=12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=10, column=0, sticky="w", pady=(6, 2))
        self._scene_selector(scroll, 11, scene_names, self.scene_menu_var)
        ctk.CTkLabel(scroll, text="Scène OBS — En jeu", font=ctk.CTkFont(size=12), anchor="w",
                     text_color=COL_TEXT_MUTED).grid(row=12, column=0, sticky="w", pady=(6, 2))
        self._scene_selector(scroll, 13, scene_names, self.scene_ingame_var)

        self.create_scene_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(scroll, text="Créer une Scène dédiée dans OBS", variable=self.create_scene_var, font=ctk.CTkFont(size=12)).grid(row=14, column=0, sticky="w", pady=(10, 0))

        self.create_group_var = ctk.BooleanVar(value=False)
        self.chk_group = ctk.CTkCheckBox(scroll, text="Créer un Groupe de Sources OBS", variable=self.create_group_var, font=ctk.CTkFont(size=12), command=self._toggle_group)
        self.chk_group.grid(row=15, column=0, sticky="w", pady=(10, 0))

        self.target_scene_lbl = ctk.CTkLabel(scroll, text="Scène OBS de destination :", font=ctk.CTkFont(size=12), anchor="w", text_color=COL_TEXT_MUTED)
        self.target_scene_lbl.grid(row=16, column=0, sticky="w", pady=(10, 2))
        self.target_scene_var = ctk.StringVar(value="")
        self.target_scene_menu = ctk.CTkOptionMenu(scroll, values=scene_names or ["(non connecté à OBS)"], variable=self.target_scene_var, fg_color=COL_BG, button_color=COL_ACCENT, button_hover_color=COL_ACCENT_HOVER)
        self.target_scene_menu.grid(row=17, column=0, sticky="ew")

        self.msg_lbl = ctk.CTkLabel(scroll, text="", font=ctk.CTkFont(size=11))
        self.msg_lbl.grid(row=18, column=0, sticky="w", pady=(10, 0))

        ctk.CTkButton(scroll, text="💾 Enregistrer", height=38, fg_color=COL_ACCENT,
                       hover_color=COL_ACCENT_HOVER, text_color="#0d1117",
                       font=ctk.CTkFont(weight="bold"), command=self._save).grid(
            row=19, column=0, sticky="ew", pady=(16, 0))

        self._toggle_source()
        self._toggle_group()

    @staticmethod
    def _images_summary(paths: list[str]) -> str:
        return f"{len(paths)} image(s) sélectionnée(s)" if paths else "Aucune image sélectionnée"

    def _labeled_entry(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12), text_color=COL_TEXT_MUTED,
                     anchor="w").grid(row=row, column=0, sticky="w", pady=(4, 2))
        entry = ctk.CTkEntry(parent, textvariable=var, fg_color=COL_BG, border_color=COL_BORDER)
        entry.grid(row=row, column=0, sticky="ew", pady=(20, 8))

    def _scene_selector(self, parent, row: int, scene_names: list[str], var: ctk.StringVar) -> None:
        values = scene_names or ["(non connecté à OBS)"]
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

    def _toggle_group(self) -> None:
        state = "normal" if self.create_group_var.get() else "disabled"
        self.target_scene_menu.configure(state=state)

    def _pick_images(self, kind: str) -> None:
        paths = filedialog.askopenfilenames(
            title="Choisir des images PNG", filetypes=[("Images PNG", "*.png")]
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
                self.msg_lbl.configure(text="⚠ Sélectionne un jeu Steam valide.", text_color=COL_RED)
                return
            name, source, active_match, appid = match["name"], "steam", match["install_dir"], match["appid"]
        else:
            name = self.name_var.get().strip()
            exe = self.exe_var.get().strip()
            if not name or not exe:
                self.msg_lbl.configure(text="⚠ Nom et exécutable requis.", text_color=COL_RED)
                return
            source, active_match, appid = "manual", exe, ""

        client = self._get_obs_client()
        if client is not None and client.is_connected:
            try:
                created_menu = False
                created_ingame = False
                menu_scene = f"{name} - Menu"
                ingame_scene = f"{name} - InGame"
                
                # 1. Scène dédiée
                if self.create_scene_var.get():
                    self._obs_loop.run_coro(client.create_scene(menu_scene)).result(timeout=5)
                    self._obs_loop.run_coro(client.create_scene(ingame_scene)).result(timeout=5)
                    self.scene_menu_var.set(menu_scene)
                    self.scene_ingame_var.set(ingame_scene)
                    created_menu = True
                    created_ingame = True

                # 2. Groupe (Scène imbriquée) & Source
                target_scene = self.target_scene_var.get()
                input_settings = {"executable": active_match} if active_match else {}
                source_name = f"{name} Capture"

                if self.create_group_var.get():
                    group_scene = f"Groupe - {name}"
                    self._obs_loop.run_coro(client.create_scene(group_scene)).result(timeout=5)
                    # Ajout de la source dans le groupe
                    self._obs_loop.run_coro(client.create_input(
                        scene_name=group_scene,
                        input_name=source_name,
                        input_kind="game_capture",
                        input_settings=input_settings
                    )).result(timeout=5)
                    
                    # Ajouter le groupe à la scène de destination si choisie
                    if target_scene and target_scene != "(non connecté à OBS)":
                        self._obs_loop.run_coro(client.create_scene_item(
                            scene_name=target_scene,
                            source_name=group_scene
                        )).result(timeout=5)
                    
                    # Si aucune scène dédiée n'a été créée, on set la scène in-game au target_scene pour simplifier
                    if not created_ingame and target_scene and target_scene != "(non connecté à OBS)":
                        self.scene_ingame_var.set(target_scene)
                else:
                    # Si on ne crée pas de groupe, on ajoute la source directement
                    if created_ingame:
                        self._obs_loop.run_coro(client.create_input(
                            scene_name=ingame_scene,
                            input_name=source_name,
                            input_kind="game_capture",
                            input_settings=input_settings
                        )).result(timeout=5)
                    elif target_scene and target_scene != "(non connecté à OBS)":
                        self._obs_loop.run_coro(client.create_input(
                            scene_name=target_scene,
                            input_name=source_name,
                            input_kind="game_capture",
                            input_settings=input_settings
                        )).result(timeout=5)
                        self.scene_ingame_var.set(target_scene)
                        
            except Exception as e:
                logger.warning(f"Impossible de créer toutes les scènes/sources OBS : {e}")

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
            self.msg_lbl.configure(text="✗ Échec de la sauvegarde.", text_color=COL_RED)


# ============================================================================
# VUE : DASHBOARD
# ============================================================================
class DashboardView(ctk.CTkFrame):
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

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(24, 8))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header, text="Jeux configurés", font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="w")

        btns = ctk.CTkFrame(header, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text="＋ Ajouter", width=110, height=34, fg_color=COL_CARD,
                      hover_color=COL_BORDER, command=self._open_add_modal).pack(side="left", padx=(0, 8))
        self.scan_btn = ctk.CTkButton(btns, text="🔍 Scanner Steam", width=150, height=34,
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

        self.render_games()

    def _clear(self) -> None:
        for widget in self.scroll.winfo_children():
            widget.destroy()

    def render_games(self) -> None:
        self._clear()
        games = self._store.load()
        if not games:
            ctk.CTkLabel(self.scroll, text="Aucun jeu — scanne Steam ou ajoute un jeu manuellement.",
                         text_color=COL_TEXT_MUTED).grid(row=0, column=0, padx=10, pady=20)
            return
        for i, game in enumerate(games):
            state = self._latest_states.get(game.id, "inactive")
            card = GameCard(self.scroll, game=game, state=state,
                             on_edit=self._open_edit_modal, on_delete=self._delete_game)
            card.grid(row=i // 3, column=i % 3, sticky="nsew", padx=8, pady=8)

    def apply_scan_results(self, results: list[dict[str, Any]]) -> None:
        """Appelé depuis ScanWorker (via la file UI thread-safe) à chaque cycle."""
        self._latest_states = {r["id"]: r["state"] for r in results}
        active_count = sum(1 for r in results if r["active"])
        self.status_lbl.configure(
            text=f"{len(results)} jeu(x) suivi(s) — {active_count} actif(s)", text_color=COL_TEXT_MUTED
        )
        self.render_games()

    def scan_steam_library(self) -> None:
        self.scan_btn.configure(state="disabled", text="Scan...")
        self.status_lbl.configure(text="Analyse des bibliothèques Steam locales...")
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
        self.scan_btn.configure(state="normal", text="🔍 Scanner Steam")
        if error:
            self.status_lbl.configure(text=f"Erreur scan Steam : {error}", text_color=COL_RED)
            return
        self.status_lbl.configure(
            text=f"{total_found} jeu(x) Steam détecté(s), {added} nouveau(x) ajouté(s).",
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
        if messagebox.askyesno("Confirmer", f"Supprimer '{game.name}' ?"):
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

        ctk.CTkLabel(self, text="Paramètres", font=ctk.CTkFont(size=20, weight="bold")
                     ).grid(row=0, column=0, sticky="w", padx=24, pady=(24, 16))

        card = ctk.CTkFrame(self, fg_color=COL_CARD, corner_radius=14, border_width=1, border_color=COL_BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=24)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="Connexion OBS WebSocket", font=ctk.CTkFont(size=14, weight="bold")
                     ).grid(row=0, column=0, columnspan=2, sticky="w", padx=20, pady=(18, 12))

        self.host_var = ctk.StringVar()
        self.port_var = ctk.StringVar()
        self.pwd_var = ctk.StringVar()
        self.interval_var = ctk.StringVar()
        self.threshold_var = ctk.StringVar()

        self._field(card, 1, "Adresse (host)", self.host_var)
        self._field(card, 2, "Port", self.port_var)
        self._password_field(card, 3, "Mot de passe", self.pwd_var)
        self._field(card, 4, "Intervalle scan (s)", self.interval_var)
        self._field(card, 5, "Seuil détection visuelle (0-1)", self.threshold_var)

        self.msg_lbl = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11))
        self.msg_lbl.grid(row=6, column=0, columnspan=2, sticky="w", padx=20, pady=(4, 0))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=7, column=0, columnspan=2, sticky="ew", padx=20, pady=18)
        ctk.CTkButton(btn_row, text="💾 Enregistrer", width=150, height=36, fg_color=COL_ACCENT,
                      hover_color=COL_ACCENT_HOVER, text_color="#0d1117",
                      font=ctk.CTkFont(weight="bold"), command=self._save).pack(side="left")

        self._load_into_form()

    def _field(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12), text_color=COL_TEXT_MUTED
                     ).grid(row=row, column=0, sticky="w", padx=20, pady=6)
        ctk.CTkEntry(parent, textvariable=var, width=200, height=34, fg_color=COL_BG,
                     border_color=COL_BORDER).grid(row=row, column=1, sticky="e", padx=20, pady=6)

    def _password_field(self, parent, row: int, label: str, var: ctk.StringVar) -> None:
        ctk.CTkLabel(parent, text=label, font=ctk.CTkFont(size=12), text_color=COL_TEXT_MUTED
                     ).grid(row=row, column=0, sticky="w", padx=20, pady=6)
        wrapper = ctk.CTkFrame(parent, fg_color="transparent")
        wrapper.grid(row=row, column=1, sticky="e", padx=20, pady=6)
        self._pwd_entry = ctk.CTkEntry(wrapper, textvariable=var, width=160, height=34, show="•",
                                        fg_color=COL_BG, border_color=COL_BORDER)
        self._pwd_entry.pack(side="left")
        self._pwd_visible = False
        ctk.CTkButton(wrapper, text="👁", width=34, height=34, fg_color=COL_BG,
                      hover_color=COL_BORDER, command=self._toggle_pwd).pack(side="left", padx=(4, 0))

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

    def _save(self) -> None:
        try:
            port = int(self.port_var.get())
            if not (0 < port <= 65535):
                raise ValueError("Port hors plage 1-65535")
            interval = max(0.5, float(self.interval_var.get()))
            threshold = min(1.0, max(0.0, float(self.threshold_var.get())))
        except ValueError as exc:
            self.msg_lbl.configure(text=f"⚠ Valeur invalide : {exc}", text_color=COL_RED)
            return

        cfg = OBSConfig(
            host=self.host_var.get().strip() or "localhost",
            port=port, password=self.pwd_var.get(),
            scan_interval_seconds=interval, match_threshold=threshold,
        )
        if self.config_mgr.save(cfg):
            self.msg_lbl.configure(text="✓ Configuration enregistrée.", text_color=COL_GREEN)
            self.on_saved()
        else:
            self.msg_lbl.configure(text="✗ Échec de la sauvegarde.", text_color=COL_RED)


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
        ctk.CTkLabel(brand, text="⚡ OBS", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=COL_ACCENT).pack(side="left")
        ctk.CTkLabel(brand, text=" Dynamics", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")

        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_btn("dashboard", "🏠  Tableau de bord", row=1)
        self._nav_btn("settings", "⚙️  Paramètres", row=2)

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.grid(row=6, column=0, sticky="sew", padx=16, pady=20)

        self.status_dot = ctk.CTkLabel(ctrl, text="●", text_color=COL_RED, font=ctk.CTkFont(size=14))
        self.status_dot.pack(anchor="w")
        self.status_text = ctk.CTkLabel(ctrl, text="Surveillance arrêtée", font=ctk.CTkFont(size=11),
                                         text_color=COL_TEXT_MUTED, wraplength=190, justify="left", anchor="w")
        self.status_text.pack(anchor="w", pady=(0, 10), fill="x")

        self.start_btn = ctk.CTkButton(ctrl, text="▶ Démarrer", height=36, fg_color=COL_GREEN,
                                        hover_color="#27ae60", font=ctk.CTkFont(size=12),
                                        command=on_start)
        self.start_btn.pack(fill="x", pady=2)
        self.stop_btn = ctk.CTkButton(ctrl, text="■ Arrêter", height=36, fg_color=COL_RED,
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
        self.status_text.configure(text="Surveillance active" if running else "Surveillance arrêtée")
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")


# ============================================================================
# APPLICATION PRINCIPALE
# ============================================================================
class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OBS Dynamics")
        self.geometry("1020x680")
        self.minsize(860, 580)
        self.configure(fg_color=COL_BG)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_window_icon()

        self.config_mgr = EnvConfigManager(ENV_PATH)
        self.store = GameStore(GAMES_PATH)
        self.steam_scanner = SteamScanner()
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
                                        steam_scanner=self.steam_scanner, post_ui=self.post_ui)
        self.settings = SettingsView(self.content, self.config_mgr, on_saved=self._on_settings_saved)
        self.views: dict[str, ctk.CTkFrame] = {"dashboard": self.dashboard, "settings": self.settings}
        self._navigate("dashboard")
        self._pump_ui_queue()

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
        self.sidebar.start_btn.configure(state="disabled", text="Démarrage...")
        self.sidebar.status_text.configure(text="Connexion à OBS...")
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
        self.sidebar.start_btn.configure(text="▶ Démarrer")
        if obs_error:
            self.sidebar.status_text.configure(text=f"Surveillance active (OBS: {obs_error[:40]})")
        else:
            self.sidebar.status_text.configure(text="Surveillance active — OBS connecté")

    def _stop(self) -> None:
        self.sidebar.stop_btn.configure(state="disabled", text="Arrêt...")
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
        self.sidebar.stop_btn.configure(text="■ Arrêter")

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
            self.post_ui(lambda: self.sidebar.status_text.configure(text="Surveillance active — OBS reconnecté"))
        except Exception as exc:
            self._obs_client = None
            logger.warning("Reconnexion OBS échouée : %s", exc)
            self.post_ui(lambda: self.sidebar.status_text.configure(text=f"Surveillance active (OBS: {exc})"))

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
