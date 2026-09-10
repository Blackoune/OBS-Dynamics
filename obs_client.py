"""Client OBS WebSocket v5 et boucle de scan qui pilote la bascule de scène."""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any, Callable, Optional

import numpy as np
import simpleobsws

import screen_match
from app_paths import logger
from env_config import EnvConfigManager
from detection import (_capture_screen_bgr, _downscale, detect_game_state,
                       is_game_active)
from games import Game, GameStore
from i18n import t


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
        try:
            response = await self._ws.call(request)
        except (OSError, ConnectionError, asyncio.TimeoutError) as exc:
            # La socket est morte (OBS fermé, réseau coupé). On bascule l'état
            # à "déconnecté" pour que le superviseur déclenche la reconnexion :
            # sans ça `_connected` restait True indéfiniment et les bascules de
            # scène échouaient en silence jusqu'à un Stop/Start manuel.
            self._connected = False
            raise OBSClientError(t("LOG_OBS_CONNECTION_LOST", error=exc)) from exc
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

    # -- Création de scènes / sources (à l'ajout d'un jeu) ------------------ #
    # Chaque helper tolère l'échec « existe déjà » : recréer un jeu déjà
    # configuré ne doit pas faire échouer l'enregistrement du formulaire.

    async def create_scene(self, scene_name: str) -> bool:
        try:
            await self.call("CreateScene", {"sceneName": scene_name})
            return True
        except OBSClientError as exc:
            logger.warning(t("LOG_OBS_SCENE_CREATE_SKIPPED", scene=scene_name, error=exc))
            return False

    async def create_input(self, scene_name: str, input_name: str,
                           input_kind: str, input_settings: dict[str, Any]) -> bool:
        try:
            await self.call("CreateInput", {
                "sceneName": scene_name,
                "inputName": input_name,
                "inputKind": input_kind,
                "inputSettings": input_settings,
            })
            return True
        except OBSClientError as exc:
            logger.warning(t("LOG_OBS_INPUT_CREATE_SKIPPED", source=input_name, error=exc))
            return False

    async def create_scene_item(self, scene_name: str, source_name: str) -> bool:
        try:
            await self.call("CreateSceneItem", {
                "sceneName": scene_name, "sourceName": source_name,
            })
            return True
        except OBSClientError as exc:
            logger.warning(t("LOG_OBS_SCENE_ITEM_SKIPPED", source=source_name,
                             scene=scene_name, error=exc))
            return False

    async def setup_game_scenes(self, game_name: str, executable: str) -> tuple[str, str]:
        """Crée les deux scènes d'un jeu ('<jeu> - Menu' et '<jeu> - En jeu')
        et y ajoute une source de capture de jeu. Retourne les deux noms de
        scènes, à réinjecter dans le formulaire."""
        menu_scene = f"{game_name} - Menu"
        ingame_scene = f"{game_name} - En jeu"
        await self.create_scene(menu_scene)
        await self.create_scene(ingame_scene)

        # game_capture n'existe que sous Windows ; ailleurs OBS refusera et le
        # helper loguera sans interrompre la création des scènes.
        settings: dict[str, Any] = {"capture_mode": "any_fullscreen"}
        if executable:
            settings = {"capture_mode": "window", "window": executable}
        await self.create_input(ingame_scene, f"{game_name} — Capture",
                                "game_capture", settings)
        return menu_scene, ingame_scene


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
        self._forced_states: dict[str, str] = {}  # rempli par les hotkeys
        # Dernière bascule en échec, pour ne pas répéter le même avertissement
        # à chaque cycle (0,5-2 s) tant que la cause n'a pas changé.
        self._last_switch_error: Optional[tuple[str, str, str]] = None
        # Deux lectures concordantes avant de confirmer un changement d'état :
        # une image ambiguë (transition, cinématique, chargement) ne doit pas
        # faire basculer OBS d'une scène à l'autre à chaque cycle.
        self._stabilizer = screen_match.Stabilizer(confirmations=2)

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
        # `cfg` est initialisé AVANT la boucle : l'ancienne version testait
        # `if "cfg" in locals()` dans le wait() final, ce qui retombait sur un
        # délai codé en dur si le tout premier chargement échouait, puis
        # gardait indéfiniment la dernière valeur chargée avec succès.
        cfg = self._config_mgr.load()
        while not self._stop_event.is_set():
            try:
                cfg = self._config_mgr.load()
                games = self._store.load()

                # UNE seule capture d'écran par cycle, partagée par tous les
                # jeux, et uniquement si au moins un jeu actif a des images
                # de référence à comparer.
                screen_small: Optional[np.ndarray] = None
                if any(g.menu_images or g.ingame_images for g in games):
                    raw = _capture_screen_bgr()
                    if raw is not None:
                        screen_small = _downscale(raw)

                results: list[dict[str, Any]] = []
                for game in games:
                    state = self._stabilizer.update(
                        game.id, detect_game_state(game, cfg.match_threshold, screen_small))
                    forced = self._forced_states.get(game.id)
                    if forced is not None and state != "inactive":
                        # Une hotkey a forcé un état : il prime tant que le
                        # jeu tourne, et se purge dès qu'il se ferme.
                        state = forced
                    results.append({
                        "id": game.id, "name": game.name, "exe": game.active_match,
                        "state": state, "active": state != "inactive",
                    })
                    self._maybe_switch_scene(game, state)

                self._prune_state_maps({g.id for g in games})
                self._post_ui(lambda r=results: on_update(r))
            except Exception:
                logger.exception("Erreur dans la boucle de surveillance.")
            self._stop_event.wait(max(0.5, cfg.scan_interval_seconds))

    def _prune_state_maps(self, live_ids: set[str]) -> None:
        """Purge les jeux supprimés. Sans ça `_last_states` grossissait sans
        fin, et un jeu supprimé puis recréé gardait son ancien état — donc la
        bascule de scène ne se redéclenchait jamais pour lui."""
        for stale in [gid for gid in self._last_states if gid not in live_ids]:
            del self._last_states[stale]
        for stale in [gid for gid in self._forced_states if gid not in live_ids]:
            del self._forced_states[stale]
            self._stabilizer.forget(stale)

    def force_state(self, state: str) -> None:
        """Force l'état de tous les jeux actuellement actifs (hotkeys)."""
        applied = False
        for game in self._store.load():
            if is_game_active(game):
                self._forced_states[game.id] = state
                applied = True
        if applied:
            logger.info(t("LOG_HOTKEY_FORCED_STATE", state=state))

    def clear_forced_states(self) -> None:
        self._forced_states.clear()

    def _maybe_switch_scene(self, game: Game, state: str) -> None:
        """Bascule la scène OBS quand l'état d'un jeu change.

        RÈGLE : `_last_states` ne doit être mis à jour QUE si la transition a
        réellement été traitée. L'ancienne version l'écrivait avant même de
        vérifier qu'OBS était joignable, ce qui consommait la transition dans
        le vide : au cycle suivant l'état n'avait plus « changé », donc la
        bascule ne se déclenchait jamais. C'était le cas nominal — le jeu
        tourne déjà, ou OBS finit de se connecter, quand la surveillance
        démarre — et c'est pourquoi le changement automatique ne marchait pas.
        """
        if state == self._last_states.get(game.id):
            return

        target_scene = self._scene_for_state(game, state)
        if not target_scene:
            # Aucune scène configurée pour cet état : il n'y a rien à rejouer
            # plus tard, la transition peut être mémorisée.
            self._last_states[game.id] = state
            return

        client = self._get_obs_client()
        if client is None or not client.is_connected:
            return  # non mémorisé : la bascule sera rejouée dès la connexion

        try:
            # .result() OBLIGATOIRE : run_coro() ne fait que planifier la
            # coroutine sur la boucle asyncio. Sans attendre le Future, une
            # erreur OBS (scène renommée, supprimée, collection changée)
            # restait totalement invisible — le try/except ne voyait rien.
            self._obs_loop.run_coro(client.set_current_scene(target_scene)).result(timeout=3)
        except Exception as exc:
            signature = (game.id, state, target_scene)
            if self._last_switch_error != signature:
                self._last_switch_error = signature
                logger.warning("Bascule vers la scène « %s » impossible pour %s : %s",
                               target_scene, game.name, exc)
            return  # non mémorisé : nouvelle tentative au cycle suivant

        self._last_switch_error = None
        self._last_states[game.id] = state
        logger.info(t("LOG_SCENE_SWITCH_OK", scene=target_scene, game=game.name, state=state))

    @staticmethod
    def _scene_for_state(game: Game, state: str) -> str:
        """Scène OBS à afficher pour un état, "" s'il ne faut rien changer."""
        scenes = {key: scene for key, _images, scene in game.detection_states()}
        if state in scenes:
            return scenes[state]
        if state == "active" and not game.reference_images():
            # Jeu lancé, mais AUCUNE image de référence : la détection visuelle
            # ne pourra jamais distinguer le menu du jeu, l'état restera
            # "active" pour toujours. Basculer sur la scène de menu au
            # lancement vaut mieux que ne rien envoyer du tout.
            #
            # La condition est volontairement stricte : si des images existent,
            # "active" veut dire « écran non reconnu » (cinématique, écran de
            # chargement) et basculer ferait clignoter la scène en pleine
            # partie. Dans ce cas on ne touche à rien.
            return game.obs_scene_menu
        return ""
