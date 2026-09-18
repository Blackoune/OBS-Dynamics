"""
music_smtc.py — Sonde SMTC (Windows Media Transport Controls).

Premier étage du widget musique : lire, sans aucune authentification ni
intégration par application, ce que Windows sait déjà du morceau en cours.
Windows centralise dans SMTC les métadonnées de toute app qui s'intègre aux
touches multimédia — Spotify, Apple Music, et les onglets de navigateur via la
Media Session API. Une seule source à interroger au lieu d'une par lecteur.

Module volontairement ISOLÉ : rien d'autre dans le projet ne l'importe, sauf
`ui_music.py`. Supprimer ces deux fichiers retire la fonctionnalité en entier.

Bindings : les paquets `winrt-*` (3.x). Le paquet `winsdk`, cité dans la plupart
des exemples en ligne, n'a pas de roue pour Python 3.14 — celui de ce dépôt.

L'API est ÉVÉNEMENTIELLE : on s'abonne aux changements, on ne sonde pas en
boucle. Les rappels arrivent sur un thread du pool WinRT, jamais sur celui de
l'interface : c'est `dispatch` qui fait la traversée (même contrat que
`GameCoverService`).
"""
from __future__ import annotations

import asyncio
import io
import threading

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app_paths import logger

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager)
    from winrt.windows.storage.streams import (Buffer, DataReader,
                                               InputStreamOptions)
    IMPORT_ERROR: Optional[str] = None
except Exception as exc:                      # pragma: no cover - dépend de l'OS
    _Manager = None                           # type: ignore[assignment]
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

try:
    from PIL import Image
except Exception:                             # pragma: no cover
    Image = None                              # type: ignore[assignment]


def available() -> bool:
    """Les bindings WinRT sont-ils chargeables ? Faux hors Windows."""
    return _Manager is not None


# Fenêtre de regroupement des événements SMTC, en secondes. Changer de morceau
# émet plusieurs notifications d'affilée (propriétés, puis état de lecture) ;
# sans ce délai la vue serait reconstruite deux ou trois fois pour un seul
# changement réel. C'est aussi l'anti-scintillement du basculement de source.
DEBOUNCE_S = 0.3

#: Tolérance sur le rapport largeur/hauteur avant de considérer une vignette
#: comme non carrée. Les encodeurs ne rendent pas toujours un carré exact —
#: 301x300 se voit — et un écart d'un pixel ne doit pas basculer l'affichage.
SQUARE_TOLERANCE = 0.05

#: Au-delà de ce nombre de pochettes gardées, le cache est vidé d'un coup.
# ponytail: vidage total plutôt qu'une éviction LRU. Un cache de pochettes qui
# dépasse 8 entrées coûte moins cher à jeter qu'à ordonner, et le pire cas est
# une relecture locale de quelques centaines de kilo-octets.
_THUMB_CACHE_MAX = 8


@dataclass
class Session:
    """Une session SMTC, normalisée pour l'affichage."""

    app_id: str
    title: str = ""
    artist: str = ""
    album: str = ""
    status: str = "UNKNOWN"
    position_ms: int = 0
    duration_ms: int = 0
    thumbnail: Optional[bytes] = None
    thumbnail_size: Optional[tuple[int, int]] = None
    thumbnail_format: str = ""
    is_current: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def is_playing(self) -> bool:
        return self.status == "PLAYING"

    @property
    def is_square_art(self) -> bool:
        """La vignette est-elle une pochette carrée, ou l'image d'une vidéo ?

        C'est l'image qui tranche, jamais l'application : un onglet de
        navigateur peut aussi bien jouer un album, qui publie une pochette
        carrée, qu'une vidéo, qui publie une miniature 16:9. Trier sur
        l'identifiant de la source donnerait la mauvaise réponse une fois sur
        deux, et une image affichée au mauvais rapport est écrasée.

        Vrai par défaut quand la taille est inconnue : le carré est le format
        de très loin le plus fréquent.
        """
        if not self.thumbnail_size:
            return True
        width, height = self.thumbnail_size
        if height <= 0:
            return True
        return abs(width / height - 1.0) <= SQUARE_TOLERANCE


def _ms(delta: Any) -> int:
    """Convertit un TimeSpan projeté (timedelta) en millisecondes."""
    try:
        return int(delta.total_seconds() * 1000)
    except AttributeError:
        return 0


def _read_all(reader: Any, length: int) -> bytes:
    """Vide un DataReader en octets.

    Les deux générations de bindings ne s'accordent pas sur `read_bytes` :
    l'une rend les octets, l'autre remplit un tampon fourni. Les essayer dans
    cet ordre évite de dépendre d'une version précise de `winrt-runtime`.
    """
    try:
        return bytes(reader.read_bytes(length))
    except TypeError:
        out = bytearray(length)
        reader.read_bytes(out)
        return bytes(out)


class MusicWatcher:
    """Écoute SMTC et republie la liste des sessions à chaque changement.

    `on_update` reçoit `list[Session]`, déjà repassée par `dispatch`. Elle est
    aussi appelée une première fois juste après `start()`, sans attendre un
    événement : un lecteur déjà en cours de lecture au démarrage doit
    s'afficher tout de suite.
    """

    def __init__(self, on_update: Callable[[list[Session]], None],
                 dispatch: Callable[[Callable[[], None]], None],
                 on_error: Optional[Callable[[str], None]] = None) -> None:
        self._on_update = on_update
        self._dispatch = dispatch
        self._on_error = on_error
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._manager: Any = None
        self._tokens: list[tuple[Any, str, Any]] = []
        self._pending: Optional[asyncio.Future] = None
        self._thumbs: dict[tuple[str, str, str],
                           tuple[bytes, Optional[tuple[int, int]], str]] = {}
        self._stopping = threading.Event()
        # Créé hors de toute boucle : depuis Python 3.10, asyncio.Event ne se
        # lie plus à une boucle à la construction. L'avoir dès maintenant
        # permet à stop() de le signaler même si le thread vient de démarrer.
        self._stopped = asyncio.Event()

    # -- cycle de vie ------------------------------------------------------ #

    def start(self) -> None:
        if self._thread is not None or not available():
            return
        self._stopping.clear()
        self._thread = threading.Thread(target=self._run, name="smtc-watcher",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Détache les abonnements puis arrête la boucle.

        Les jetons d'événement DOIVENT être rendus : un handler orphelin garde
        une référence sur cet objet et continue d'être appelé par Windows après
        la fermeture de la vue.
        """
        self._stopping.set()
        loop = self._loop
        if loop is not None:
            try:
                # On DEMANDE la sortie, on n'arrête pas la boucle de force :
                # `loop.stop()` pendant un `await` en cours fait échouer
                # l'opération WinRT sous-jacente au lieu de la laisser finir.
                loop.call_soon_threadsafe(self._stopped.set)
            except RuntimeError:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)
        self._thread = None
        self._loop = None

    def refresh_now(self) -> None:
        """Force une relecture, sans attendre d'événement SMTC."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._schedule_refresh)
        except RuntimeError:
            pass

    # -- thread dédié ------------------------------------------------------ #

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except Exception as exc:
            logger.exception("Sonde SMTC interrompue.")
            self._report_error(f"{type(exc).__name__}: {exc}")
        finally:
            self._detach()
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            # La boucle n'est volontairement PAS fermée. Une opération WinRT
            # lancée avant l'arrêt peut encore appeler `call_soon_threadsafe`
            # depuis un thread du pool ; sur une boucle fermée cet appel lève,
            # et il lève LA-BAS, hors de portee de tout try/except d'ici. La
            # boucle ne tourne plus et le thread est daemon : la laisser
            # ouverte ne retient ni thread ni socket.

    async def _serve(self) -> None:
        """Installe les abonnements, puis attend la demande d'arrêt.

        Une seule exécution de boucle pour toute la durée de vie de la sonde :
        `stop()` n'a plus qu'à signaler un événement, et la sortie se fait
        entre deux `await` plutôt qu'au milieu de l'un d'eux.
        """
        try:
            await self._setup()
            if not self._stopping.is_set():
                await self._stopped.wait()
        finally:
            pending = self._pending
            if pending is not None and not pending.done():
                pending.cancel()

    async def _setup(self) -> None:
        self._manager = await _Manager.request_async()
        self._subscribe_manager()
        await self._refresh()

    def _subscribe_manager(self) -> None:
        token = self._manager.add_sessions_changed(self._on_winrt_event)
        self._tokens.append((self._manager, "remove_sessions_changed", token))
        token = self._manager.add_current_session_changed(self._on_winrt_event)
        self._tokens.append((self._manager, "remove_current_session_changed", token))

    def _subscribe_sessions(self, sessions: list[Any]) -> None:
        """Réabonne les sessions courantes, après avoir lâché les précédentes.

        La liste change quand un lecteur s'ouvre ou se ferme ; garder les
        anciens abonnements ferait fuir un handler par lecteur fermé.

        Volontairement PAS d'abonnement à `timeline_properties_changed` : la
        position émet un événement par seconde et par lecteur, ce qui
        relancerait une relecture complète en boucle pour une information que
        la sonde relit déjà à chaque rafraîchissement.
        """
        self._detach(keep_manager=True)
        for session in sessions:
            token = session.add_media_properties_changed(self._on_winrt_event)
            self._tokens.append((session, "remove_media_properties_changed", token))
            token = session.add_playback_info_changed(self._on_winrt_event)
            self._tokens.append((session, "remove_playback_info_changed", token))

    def _detach(self, keep_manager: bool = False) -> None:
        kept: list[tuple[Any, str, Any]] = []
        for owner, remover, token in self._tokens:
            if keep_manager and owner is self._manager:
                kept.append((owner, remover, token))
                continue
            try:
                getattr(owner, remover)(token)
            except Exception:
                logger.debug("Retrait d'abonnement SMTC impossible (%s).", remover)
        self._tokens = kept

    # -- événements -------------------------------------------------------- #

    def _on_winrt_event(self, _sender: Any, _args: Any) -> None:
        """Appelé depuis un thread du pool WinRT — rien d'autre ici que le
        renvoi vers la boucle de ce module."""
        loop = self._loop
        if loop is None or self._stopping.is_set():
            return
        try:
            loop.call_soon_threadsafe(self._schedule_refresh)
        except RuntimeError:
            pass                              # boucle déjà fermée

    def _schedule_refresh(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
        self._pending = asyncio.ensure_future(self._debounced_refresh())

    async def _debounced_refresh(self) -> None:
        try:
            await asyncio.sleep(DEBOUNCE_S)
            await self._refresh()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Relecture SMTC en échec.")
            self._report_error(f"{type(exc).__name__}: {exc}")

    # -- lecture ----------------------------------------------------------- #

    async def _refresh(self) -> None:
        sessions = list(self._manager.get_sessions())
        self._subscribe_sessions(sessions)
        current = self._manager.get_current_session()
        current_id = current.source_app_user_model_id if current is not None else None

        out: list[Session] = []
        for raw in sessions:
            out.append(await self._read_session(raw, current_id))
        if self._stopping.is_set():
            return
        self._dispatch(lambda: self._on_update(out))

    async def _read_session(self, raw: Any, current_id: Optional[str]) -> Session:
        app_id = raw.source_app_user_model_id or "?"
        session = Session(app_id=app_id, is_current=(app_id == current_id))
        try:
            info = raw.get_playback_info()
            session.status = getattr(info.playback_status, "name",
                                     str(info.playback_status))
        except Exception as exc:
            session.errors.append(f"playback_info: {exc}")
        try:
            timeline = raw.get_timeline_properties()
            session.position_ms = _ms(timeline.position)
            session.duration_ms = _ms(timeline.end_time)
        except Exception as exc:
            session.errors.append(f"timeline: {exc}")
        try:
            props = await raw.try_get_media_properties_async()
            session.title = props.title or ""
            session.artist = props.artist or ""
            session.album = props.album_title or ""
            await self._attach_thumbnail(session, props)
        except Exception as exc:
            session.errors.append(f"media_properties: {exc}")
        return session

    async def _attach_thumbnail(self, session: Session, props: Any) -> None:
        reference = props.thumbnail
        if reference is None:
            return
        key = (session.app_id, session.title, session.artist)
        cached = self._thumbs.get(key)
        if cached is not None:
            session.thumbnail, session.thumbnail_size, session.thumbnail_format = cached
            return
        try:
            stream = await reference.open_read_async()
            buffer = Buffer(stream.size)
            await stream.read_async(buffer, buffer.capacity,
                                    InputStreamOptions.READ_AHEAD)
            data = _read_all(DataReader.from_buffer(buffer), buffer.length)
        except Exception as exc:
            session.errors.append(f"thumbnail: {exc}")
            return
        size: Optional[tuple[int, int]] = None
        fmt = ""
        if Image is not None:
            try:
                with Image.open(io.BytesIO(data)) as img:
                    size, fmt = img.size, (img.format or "")
            except Exception as exc:
                session.errors.append(f"thumbnail_decode: {exc}")
        if len(self._thumbs) >= _THUMB_CACHE_MAX:
            self._thumbs.clear()
        self._thumbs[key] = (data, size, fmt)
        session.thumbnail = data
        session.thumbnail_size = size
        session.thumbnail_format = fmt

    def _report_error(self, message: str) -> None:
        if self._on_error is not None:
            self._dispatch(lambda: self._on_error(message))
