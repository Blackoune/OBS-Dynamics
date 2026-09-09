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
from PIL import Image, ImageEnhance, ImageFilter

logger = logging.getLogger("obs_dynamics.cover")

# Dimensions standardisées pour l'affichage vertical des jaquettes (ratio 2:3 style Steam).
COVER_WIDTH = 300
COVER_HEIGHT = 450

# Plafond du cache mémoire. Une jaquette 300x450 RGB décodée pèse ~400 Ko :
# 60 entrées ≈ 24 Mo, suffisant pour une bibliothèque courante sans fuite.
MEM_CACHE_MAX = 60

# Incrémenté quand la façon de fabriquer la jaquette change : les fichiers de
# l'ancienne version restent sur disque mais ne sont plus relus, donc pas de
# vieux recadrage centré qui survivrait à la mise à jour.
CACHE_VERSION = 2

_HTTP_TIMEOUT = 8

# Plafond d'un téléchargement de jaquette. Les images visées pèsent 50 à 300 Ko ;
# 8 Mo laissent passer une capsule inhabituellement lourde, tout en empêchant un
# hôte tiers de nous faire avaler un flux sans fin en mémoire.
_MAX_IMAGE_BYTES = 8 * 1024 * 1024

# Une image de 20 000 x 20 000 pixels tient dans quelques centaines de Ko
# compressés, mais réclame plus d'un Go une fois décodée : la taille du fichier
# ne dit rien du coût de décodage. La limite par défaut de Pillow (89 Mpx) se
# contente d'un avertissement à ce seuil ; 40 Mpx lève, et reste très au-dessus
# d'une jaquette comme d'une capture d'écran 8K (16,6 Mpx).
Image.MAX_IMAGE_PIXELS = 40_000_000

# Téléchargements réseau : purement I/O-bound, donc les threads se recouvrent
# bien. Avec un seul worker, scanner une bibliothèque de 40 jeux faisait
# apparaître les jaquettes une par une pendant une quinzaine de secondes.
# 6 est un compromis : assez pour que la grille se remplisse d'un bloc, pas
# assez pour que le CDN Steam nous limite.
WORKER_THREADS = 6



def _fetch_bounded(session: requests.Session, url: str) -> Optional[bytes]:
    """Corps de la réponse, plafonné à `_MAX_IMAGE_BYTES`, ou None.

    `resp.content` lit tout ce que l'hôte veut bien envoyer : un serveur qui ne
    ferme jamais, ou un `Content-Length` menteur, faisait grossir la mémoire
    sans limite. Ici la lecture se fait par morceaux et s'arrête au
    dépassement, avant d'avoir tout accumulé.
    """
    resp = session.get(url, timeout=_HTTP_TIMEOUT, stream=True)
    try:
        if resp.status_code != 200:
            return None
        morceaux: list[bytes] = []
        total = 0
        for bloc in resp.iter_content(64 * 1024):
            total += len(bloc)
            if total > _MAX_IMAGE_BYTES:
                logger.debug("Image écartée : dépasse %d octets.", _MAX_IMAGE_BYTES)
                return None
            morceaux.append(bloc)
        return b"".join(morceaux) or None
    finally:
        resp.close()


class GameCoverService:
    """Sources d'images, dans l'ordre de préférence :
    1. Cache mémoire (LRU borné) puis cache disque (data/covers/{id}.vN.jpg)
    2. Jaquette verticale officielle Steam (appid connu, ou résolu par nom via
       la recherche du Steam Store)
    3. API RAWG (si RAWG_API_KEY renseignée) — screenshots, moins fidèles
    4. Header / capsule Steam, en dernier recours (format paysage)
    5. API appdetails, pour les jeux récents dont les images vivent derrière un
       hachage imprévisible et dont aucun chemin historique ne répond
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

        self._workers = [
            threading.Thread(target=self._worker_loop, daemon=True,
                             name=f"cover-downloader-{i}")
            for i in range(WORKER_THREADS)
        ]
        for worker in self._workers:
            worker.start()

    # -- API publique ------------------------------------------------------ #

    def set_api_key(self, api_key: str) -> None:
        with self._lock:
            self.rawg_api_key = api_key.strip()

    def stop(self) -> None:
        """Arrête proprement les workers (appelé à la fermeture de l'app)."""
        self._stop.set()
        # Une sentinelle PAR worker : chacune n'en réveille qu'un seul, et un
        # worker resté bloqué sur queue.get() empêcherait l'arrêt propre.
        for _ in self._workers:
            self._queue.put((None, lambda _img: None, False))

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

    def _cache_path(self, game_id: str) -> Path:
        return self.cache_dir / f"{game_id}.v{CACHE_VERSION}.jpg"

    def _load_from_disk(self, game_id: str) -> Optional[Image.Image]:
        file_path = self._cache_path(game_id)
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

        if not appid and game_name:
            appid = self._steam_appid_for_name(session, game_name)

        file_path = self._cache_path(game_id)
        img = self._download_first(session, self._find_image_urls(session, game_name, appid),
                                   file_path)
        if img is not None:
            return img
        if appid:
            # Aucun chemin devinable n'a répondu : c'est le cas des jeux
            # récents, dont les images vivent derrière un hachage. On demande
            # alors à Steam les URL réelles.
            img = self._download_first(session, self._appdetails_urls(session, appid),
                                       file_path)
        return img

    def _download_first(self, session: requests.Session, urls: list[str],
                        file_path: Path) -> Optional[Image.Image]:
        """Première URL de la liste qui donne une image exploitable."""
        for url in urls:
            try:
                brut = _fetch_bounded(session, url)
                if brut is None:
                    continue
                img = self._process_image(brut)
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

    @staticmethod
    def _steam_portrait_urls(appid: str) -> list[str]:
        """Jaquette VERTICALE officielle du store (celle affichée dans la
        bibliothèque Steam). C'est la seule qui soit déjà au ratio 2:3 et
        cadrée par l'éditeur — tout le reste (header, capsule, screenshot
        RAWG) est du paysage qu'on ne peut que dégrader."""
        return [
            f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900_2x.jpg",
            f"https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/library_600x900.jpg",
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900_2x.jpg",
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
        ]

    @staticmethod
    def _steam_landscape_urls(appid: str) -> list[str]:
        return [
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/capsule_616x353.jpg",
            f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg",
        ]

    @staticmethod
    def _norm(text: str) -> str:
        return "".join(ch for ch in text.lower() if ch.isalnum())

    def _steam_appid_for_name(self, session: requests.Session, name: str) -> str:
        """Résout un appid Steam depuis un nom de jeu. On privilégie une
        correspondance de nom exacte plutôt que le premier résultat : sans ça
        « Portal » ramenait le premier DLC/bundle listé par la recherche."""
        try:
            url = ("https://store.steampowered.com/api/storesearch/"
                   f"?term={urllib.parse.quote(name)}&l=english&cc=US")
            resp = session.get(url, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return ""
            items = resp.json().get("items", [])
        except Exception:
            logger.debug("Recherche Steam Store échouée pour '%s'", name, exc_info=True)
            return ""
        wanted = self._norm(name)
        for item in items:
            if self._norm(str(item.get("name", ""))) == wanted:
                return str(item.get("id", ""))
        return str(items[0].get("id", "")) if items else ""

    def _appdetails_urls(self, session: requests.Session, appid: str) -> list[str]:
        """URL d'images telles que Steam les publie pour cet appid.

        Depuis 2025, les jeux récents ne servent PLUS leurs images sur le
        chemin historique `/steam/apps/<appid>/header.jpg` : chaque fichier vit
        derrière un hachage de contenu imprévisible,
        `/steam/apps/<appid>/<hash>/header.jpg?t=<horodatage>`. Aucune URL ne
        peut donc être devinée — il faut les demander.

        Vérifié sur MECCHA CHAMELEON (4704690) et Mouse X (4255580) : tous les
        chemins historiques renvoient 404, et ces URL-ci répondent 200.

        L'API est fortement limitée en débit : cet appel n'a lieu QUE si tous
        les chemins devinables ont échoué, donc pour une poignée de jeux
        récents, jamais pour une bibliothèque entière.
        """
        try:
            resp = session.get(
                f"https://store.steampowered.com/api/appdetails?appids={appid}",
                timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                return []
            payload = resp.json().get(str(appid)) or {}
            if not payload.get("success"):
                return []
            data = payload.get("data") or {}
        except Exception:
            logger.debug("appdetails indisponible pour %s", appid, exc_info=True)
            return []

        # Steam n'expose ici que du paysage : pas de library_600x900. Ces jeux
        # passeront donc par le fond flouté de _process_image, ce qui reste
        # infiniment préférable au carré vide.
        return [str(data[key]) for key in ("header_image", "capsule_image")
                if data.get(key)]

    def _find_image_urls(self, session: requests.Session, name: str, appid: str) -> list[str]:
        if not appid and name:
            appid = self._steam_appid_for_name(session, name)

        # Ordre = du plus fidèle au jeu (jaquette officielle verticale) au
        # moins fidèle (screenshot générique), jamais l'inverse.
        urls: list[str] = self._steam_portrait_urls(appid) if appid else []

        with self._lock:
            api_key = self.rawg_api_key

        if api_key and name:
            try:
                url = (f"https://api.rawg.io/api/games?key={api_key}"
                       f"&search={urllib.parse.quote(name)}&page_size=1")
                resp = session.get(url, timeout=_HTTP_TIMEOUT)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if results:
                        top = results[0]
                        for key in ("background_image", "background_image_additional"):
                            if top.get(key):
                                urls.append(top[key])
            except Exception as erreur:
                # Surtout PAS `exc_info=True` ici : le message d'une exception
                # `requests` contient l'URL appelée, et cette URL-là porte la
                # clé RAWG en clair (`?key=...`). Le journal part avec les
                # rapports de bug — le type de l'erreur suffit à diagnostiquer.
                logger.debug("Requête RAWG échouée pour '%s' (%s).",
                             name, type(erreur).__name__)

        if appid:
            urls.extend(self._steam_landscape_urls(appid))
        return urls

    def _process_image(self, raw_bytes: bytes) -> Optional[Image.Image]:
        """Met l'image au format 2:3 (300x450) SANS rien couper du visuel.

        L'ancienne version rognait au centre : sur un header 460x215 ça ne
        gardait qu'une bande verticale au milieu, d'où des vignettes hors
        cadre. Ici une source déjà verticale est simplement redimensionnée,
        et une source paysage est posée en entier sur un fond flouté tiré
        d'elle-même.
        """
        try:
            with Image.open(io.BytesIO(raw_bytes)) as original:
                img = original.convert("RGB")
                target_w, target_h = COVER_WIDTH, COVER_HEIGHT
                target_ratio = target_w / target_h

                if abs(img.width / img.height - target_ratio) < 0.02:
                    return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

                background = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
                background = background.filter(ImageFilter.GaussianBlur(20))
                background = ImageEnhance.Brightness(background).enhance(0.5)

                scale = min(target_w / img.width, target_h / img.height)
                fit = img.resize((max(1, round(img.width * scale)),
                                  max(1, round(img.height * scale))),
                                 Image.Resampling.LANCZOS)
                background.paste(fit, ((target_w - fit.width) // 2,
                                       (target_h - fit.height) // 2))
                return background
        except Exception:
            logger.exception("Traitement de l'image échoué.")
            return None
