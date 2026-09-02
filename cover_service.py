"""
cover_service.py — Téléchargement, mise en cache et distribution des jaquettes.

CONTRAT DE THREADING (important) :
    Tous les callbacks sont remis au `dispatch` fourni au constructeur, que
    l'image vienne du cache mémoire, du cache disque ou du réseau. Le code
    appelant reçoit donc TOUJOURS son callback sur le même thread — en
    pratique le thread UI via App.post_ui. Sans ça, un cache hit rappelait
    sur le thread appelant et un cache miss sur le thread worker, ce qui
    revenait à toucher Tk depuis un thread non-UI une fois sur deux.
"""
from __future__ import annotations

import io
import logging
import queue
import threading
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from PIL import Image

logger = logging.getLogger("obs_dynamics.cover")

# Dimensions standardisées pour l'affichage vertical des jaquettes (ratio 2:3 style Steam).
COVER_WIDTH = 300
COVER_HEIGHT = 450

# Plafond du cache mémoire. Une jaquette 300x450 RGB décodée pèse ~400 Ko :
# 60 entrées ≈ 24 Mo, suffisant pour une bibliothèque courante sans fuite.
MEM_CACHE_MAX = 60

_HTTP_TIMEOUT = 8


class GameCoverService:
    """Sources d'images, dans l'ordre de préférence :
    1. Cache mémoire (LRU borné) puis cache disque (data/covers/{game_id}.jpg)
    2. CDN Steam direct (si appid connu)
    3. API RAWG (si RAWG_API_KEY renseignée)
    4. Recherche Steam Store (fallback sans clé API)
    """

    def __init__(self, cache_dir: Path, rawg_api_key: str = "",
                 dispatch: Optional[Callable[[Callable[[], None]], None]] = None) -> None:
        self.cache_dir = cache_dir
        self.rawg_api_key = rawg_api_key.strip()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # dispatch(fn) doit exécuter fn sur le thread UI. Par défaut on appelle
        # directement : utilisable hors GUI (tests, scripts).
        self._dispatch: Callable[[Callable[[], None]], None] = dispatch or (lambda fn: fn())

        self._mem_cache: "OrderedDict[str, Image.Image]" = OrderedDict()
        self._lock = threading.Lock()
        self._queue: "queue.Queue[tuple[Any, Callable[[Optional[Image.Image]], None], bool]]" = queue.Queue()
        self._pending_ids: set[str] = set()
        self._stop = threading.Event()

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="cover-downloader")
        self._worker_thread.start()

    # -- API publique ------------------------------------------------------ #

    def set_api_key(self, api_key: str) -> None:
        with self._lock:
            self.rawg_api_key = api_key.strip()

    def stop(self) -> None:
        """Arrête proprement le worker (appelé à la fermeture de l'app)."""
        self._stop.set()
        self._queue.put((None, lambda _img: None, False))  # sentinelle de réveil

    def request_cover(self, game: Any, callback: Callable[[Optional[Image.Image]], None],
                      force: bool = False) -> None:
        """Demande la jaquette de `game`. Le callback est TOUJOURS remis via
        le dispatch (jamais appelé en ligne sur le thread appelant)."""
        game_id = getattr(game, "id", "")
        if not game_id:
            self._deliver(callback, None)
            return

        if not force:
            cached = self._get_from_mem(game_id)
            if cached is not None:
                self._deliver(callback, cached)
                return

        with self._lock:
            if game_id in self._pending_ids and not force:
                return  # déjà en file, le callback en vol servira
            self._pending_ids.add(game_id)

        self._queue.put((game, callback, force))

    # -- Caches ------------------------------------------------------------ #

    def _deliver(self, callback: Callable[[Optional[Image.Image]], None],
                 img: Optional[Image.Image]) -> None:
        try:
            self._dispatch(lambda: callback(img))
        except Exception:
            logger.debug("Dispatch du callback de jaquette échoué.", exc_info=True)

    def _get_from_mem(self, game_id: str) -> Optional[Image.Image]:
        with self._lock:
            img = self._mem_cache.get(game_id)
            if img is not None:
                self._mem_cache.move_to_end(game_id)  # LRU : marque comme récent
                return img
        return self._load_from_disk(game_id)

    def _put_in_mem(self, game_id: str, img: Image.Image) -> None:
        with self._lock:
            self._mem_cache[game_id] = img
            self._mem_cache.move_to_end(game_id)
            while len(self._mem_cache) > MEM_CACHE_MAX:
                self._mem_cache.popitem(last=False)  # évince le plus ancien

    def _load_from_disk(self, game_id: str) -> Optional[Image.Image]:
        file_path = self.cache_dir / f"{game_id}.jpg"
        if not file_path.exists():
            return None
        try:
            with Image.open(file_path) as fh:
                img = fh.convert("RGB")  # copie en mémoire, libère le descripteur
            self._put_in_mem(game_id, img)
            return img
        except Exception:
            logger.warning("Jaquette en cache illisible pour %s, purge.", game_id)
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    # -- Worker ------------------------------------------------------------ #

    def _worker_loop(self) -> None:
        session = requests.Session()
        session.headers.update({"User-Agent": "OBS-Dynamics/1.0 (Windows NT; GameCoverFetcher)"})

        while not self._stop.is_set():
            try:
                game, callback, force = self._queue.get()
            except Exception:
                continue
            if game is None:  # sentinelle d'arrêt
                self._queue.task_done()
                break

            game_id = getattr(game, "id", "")
            try:
                img = self._fetch_and_cache(session, game, force=force)
                if img is not None:
                    self._put_in_mem(game_id, img)
                self._deliver(callback, img)
            except Exception:
                logger.exception("Récupération de jaquette échouée pour %s",
                                 getattr(game, "name", game_id))
                self._deliver(callback, None)
            finally:
                with self._lock:
                    self._pending_ids.discard(game_id)
                self._queue.task_done()

    def _fetch_and_cache(self, session: requests.Session, game: Any,
                         force: bool = False) -> Optional[Image.Image]:
        game_id = getattr(game, "id", "")
        game_name = (getattr(game, "name", "") or "").strip()
        appid = (getattr(game, "appid", "") or "").strip()

        if not force:
            on_disk = self._load_from_disk(game_id)
            if on_disk is not None:
                return on_disk

        file_path = self.cache_dir / f"{game_id}.jpg"
        for url in self._find_image_urls(session, game_name, appid):
            try:
                resp = session.get(url, timeout=_HTTP_TIMEOUT)
                if resp.status_code != 200 or not resp.content:
                    continue
                img = self._process_image(resp.content)
                if img is None:
                    continue
                tmp_path = file_path.with_suffix(".tmp")
                img.save(tmp_path, format="JPEG", quality=90)
                tmp_path.replace(file_path)  # écriture atomique
                return img
            except Exception:
                logger.debug("Téléchargement échoué depuis %s", url, exc_info=True)
                continue
        return None

    def _find_image_urls(self, session: requests.Session, name: str, appid: str) -> list[str]:
        urls: list[str] = []

        # 1. CDN Steam direct si l'appid est connu (chemin le plus fiable).
        if appid:
            urls.extend([
                f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_616x353.jpg",
            ])

        with self._lock:
            api_key = self.rawg_api_key

        # 2. RAWG si une clé est configurée (couvre les jeux hors Steam).
        if api_key and name:
            try:
                url = (f"https://api.rawg.io/api/games?key={api_key}"
                       f"&search={urllib.parse.quote(name)}&page_size=1")
                resp = session.get(url, timeout=_HTTP_TIMEOUT)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results:
                        top = results[0]
                        if top.get("background_image"):
                            urls.insert(0, top["background_image"])
                        if top.get("background_image_additional"):
                            urls.append(top["background_image_additional"])
            except Exception:
                logger.debug("Requête RAWG échouée pour '%s'", name, exc_info=True)

        # 3. Recherche Steam Store, sans clé, pour les jeux manuels.
        if name and not appid:
            try:
                url = ("https://store.steampowered.com/api/storesearch/"
                       f"?term={urllib.parse.quote(name)}&l=english&cc=US")
                resp = session.get(url, timeout=_HTTP_TIMEOUT)
                if resp.status_code == 200:
                    items = resp.json().get("items", [])
                    if items:
                        found_id = str(items[0].get("id", ""))
                        if found_id:
                            urls.extend([
                                f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{found_id}/library_600x900_2x.jpg",
                                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{found_id}/header.jpg",
                            ])
            except Exception:
                logger.debug("Recherche Steam Store échouée pour '%s'", name, exc_info=True)

        return urls

    def _process_image(self, raw_bytes: bytes) -> Optional[Image.Image]:
        """Recadre au centre puis redimensionne au format 2:3 (300x450)."""
        try:
            with Image.open(io.BytesIO(raw_bytes)) as original:
                img = original.convert("RGB")
                target_w, target_h = COVER_WIDTH, COVER_HEIGHT
                orig_w, orig_h = img.size
                target_ratio = target_w / target_h

                if orig_w / orig_h > target_ratio:
                    crop_w = int(orig_h * target_ratio)          # trop large -> rogne les côtés
                    offset_x = (orig_w - crop_w) // 2
                    img = img.crop((offset_x, 0, offset_x + crop_w, orig_h))
                else:
                    crop_h = int(orig_w / target_ratio)          # trop haut -> rogne haut/bas
                    offset_y = (orig_h - crop_h) // 2
                    img = img.crop((0, offset_y, orig_w, offset_y + crop_h))

                return img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        except Exception:
            logger.exception("Traitement de l'image échoué.")
            return None
