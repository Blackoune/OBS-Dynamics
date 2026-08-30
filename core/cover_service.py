from __future__ import annotations

import io
import json
import logging
import queue
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Callable, Optional

import requests
from PIL import Image

logger = logging.getLogger("obs_dynamics.cover")

# Dimensions standardisées HD pour l'affichage vertical des jaquettes (ratio 2:3 style Steam)
COVER_WIDTH = 300
COVER_HEIGHT = 450


class GameCoverService:
    """Service de téléchargement, mise en cache et distribution des jaquettes de jeux.

    Sources d'images :
    1. Cache local disque (data/covers/{game_id}.jpg)
    2. API RAWG (si RAWG_API_KEY renseignée)
    3. CDN Steam direct (si appid renseigné)
    4. Recherche Steam Store API (fallback sans clé API)
    """

    def __init__(self, cache_dir: Path, rawg_api_key: str = "") -> None:
        self.cache_dir = cache_dir
        self.rawg_api_key = rawg_api_key.strip()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._mem_cache: dict[str, Image.Image] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[Any, Callable[[Optional[Image.Image]], None], bool]] = queue.Queue()
        self._pending_ids: set[str] = set()

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="CoverDownloader")
        self._worker_thread.start()

    def set_api_key(self, api_key: str) -> None:
        """Met à jour dynamiquement la clé API RAWG."""
        with self._lock:
            self.rawg_api_key = api_key.strip()

    def get_cached_image(self, game_id: str) -> Optional[Image.Image]:
        """Retourne l'image PIL en mémoire ou sur disque si déjà en cache."""
        with self._lock:
            if game_id in self._mem_cache:
                return self._mem_cache[game_id]

        file_path = self.cache_dir / f"{game_id}.jpg"
        if file_path.exists():
            try:
                img = Image.open(file_path)
                img.load()
                with self._lock:
                    self._mem_cache[game_id] = img
                return img
            except Exception:
                logger.warning("Échec lecture jaquette en cache pour %s", game_id)
                try:
                    file_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return None

    def request_cover(
        self,
        game: Any,
        callback: Callable[[Optional[Image.Image]], None],
        force: bool = False,
    ) -> None:
        """Demande une jaquette pour un jeu.

        Si présente en cache (et non force), appelle directement le callback avec l'image.
        Sinon, l'ajoute à la file de téléchargement en arrière-plan.
        """
        game_id = getattr(game, "id", "")
        if not game_id:
            callback(None)
            return

        if not force:
            cached = self.get_cached_image(game_id)
            if cached is not None:
                callback(cached)
                return

        with self._lock:
            if game_id in self._pending_ids and not force:
                return
            self._pending_ids.add(game_id)

        self._queue.put((game, callback, force))

    def _worker_loop(self) -> None:
        """Boucle de travail en tâche de fond pour télécharger et traiter les jaquettes."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": "OBS-Dynamics/1.0 (Windows NT; GameCoverFetcher)"
        })

        while True:
            try:
                game, callback, force = self._queue.get()
                game_id = getattr(game, "id", "")
                try:
                    img = self._fetch_and_cache(session, game, force=force)
                    if img is not None:
                        with self._lock:
                            self._mem_cache[game_id] = img
                    callback(img)
                except Exception:
                    logger.exception("Erreur lors de la récupération de la jaquette pour %s", getattr(game, "name", game_id))
                    callback(None)
                finally:
                    with self._lock:
                        self._pending_ids.discard(game_id)
                    self._queue.task_done()
            except Exception:
                logger.exception("Erreur inattendue dans le worker cover")

    def _fetch_and_cache(self, session: requests.Session, game: Any, force: bool = False) -> Optional[Image.Image]:
        game_id = getattr(game, "id", "")
        game_name = getattr(game, "name", "").strip()
        appid = getattr(game, "appid", "").strip()

        file_path = self.cache_dir / f"{game_id}.jpg"
        if not force and file_path.exists():
            try:
                img = Image.open(file_path)
                img.load()
                return img
            except Exception:
                pass

        image_urls = self._find_image_urls(session, game_name, appid)

        for url in image_urls:
            try:
                resp = session.get(url, timeout=8)
                if resp.status_code == 200 and resp.content:
                    img = self._process_image(resp.content)
                    if img:
                        # Sauvegarder dans le cache disque
                        tmp_path = file_path.with_suffix(".tmp")
                        img.save(tmp_path, format="JPEG", quality=90)
                        tmp_path.replace(file_path)
                        return img
            except Exception:
                logger.debug("Échec téléchargement image depuis %s", url)
                continue

        return None

    def _find_image_urls(self, session: requests.Session, name: str, appid: str) -> list[str]:
        urls: list[str] = []

        # 1. CDN Steam direct si appid connu
        if appid:
            urls.extend([
                f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_616x353.jpg",
            ])

        # 2. RAWG API si clé configurée
        api_key = ""
        with self._lock:
            api_key = self.rawg_api_key

        if api_key and name:
            try:
                query_name = urllib.parse.quote(name)
                url = f"https://api.rawg.io/api/games?key={api_key}&search={query_name}&page_size=1"
                resp = session.get(url, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("results", [])
                    if results:
                        top = results[0]
                        bg = top.get("background_image")
                        if bg:
                            urls.insert(0, bg)
                        bg_add = top.get("background_image_additional")
                        if bg_add:
                            urls.append(bg_add)
            except Exception:
                logger.debug("Erreur lors de la requête RAWG pour '%s'", name)

        # 3. Fallback recherche Steam Store sans clé
        if name and not appid:
            try:
                query_name = urllib.parse.quote(name)
                url = f"https://store.steampowered.com/api/storesearch/?term={query_name}&l=english&cc=US"
                resp = session.get(url, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    items = data.get("items", [])
                    if items:
                        found_id = str(items[0].get("id", ""))
                        if found_id:
                            urls.extend([
                                f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{found_id}/library_600x900_2x.jpg",
                                f"https://cdn.cloudflare.steamstatic.com/steam/apps/{found_id}/header.jpg",
                            ])
            except Exception:
                pass

        return urls

    def _process_image(self, raw_bytes: bytes) -> Optional[Image.Image]:
        """Convertit l'image brute en PIL Image redimensionnée et centrée."""
        try:
            with Image.open(io.BytesIO(raw_bytes)) as original:
                img = original.convert("RGB")

                # Découpage/centrage pour ratio 2:1 (ex: 280x140)
                target_w, target_h = COVER_WIDTH, COVER_HEIGHT
                orig_w, orig_h = img.size

                target_ratio = target_w / target_h
                orig_ratio = orig_w / orig_h

                if orig_ratio > target_ratio:
                    # Plus large : on rogne les côtés
                    crop_w = int(orig_h * target_ratio)
                    offset_x = (orig_w - crop_w) // 2
                    img = img.crop((offset_x, 0, offset_x + crop_w, orig_h))
                else:
                    # Plus haut : on rogne le bas / haut
                    crop_h = int(orig_w / target_ratio)
                    offset_y = (orig_h - crop_h) // 2
                    img = img.crop((0, offset_y, orig_w, offset_y + crop_h))

                img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                return img
        except Exception:
            logger.exception("Erreur lors du traitement de l'image")
            return None
