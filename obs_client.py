"""OBS WebSocket connection manager.

Bug fixed vs. the original project: requirements.txt pinned
`obs-websocket-py`, a client for OBS WebSocket protocol **v4** (default
port 4444), while obs_config.json's port (4455) is the **v5** default and
config.json's port (4444) was the stale v4 leftover. Those two facts
disagreed and neither client could actually reach modern OBS (28+) which
ships protocol v5. This module standardizes on protocol v5 via
`obsws-python` and obs_config.json's port (4455).

`obsws_python.ReqClient` is a synchronous client, so every call is executed
in a worker thread via `asyncio.to_thread` and serialized behind a lock —
a single OBS websocket connection is not meant to be hit concurrently.
"""
from __future__ import annotations

import asyncio
import logging

import obsws_python as obs

from core import logs
from core.config import settings

logger = logging.getLogger("obs_dynamics.obs_client")


class ObsConnectionError(RuntimeError):
    pass


class ObsManager:
    def __init__(self) -> None:
        self._client: obs.ReqClient | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def connect(self) -> None:
        async with self._lock:
            if self._client is not None:
                return
            try:
                self._client = await asyncio.to_thread(
                    obs.ReqClient,
                    host=settings.obs_host,
                    port=settings.obs_port,
                    password=settings.obs_password,
                    timeout=3,
                )
                logger.info("Connected to OBS WebSocket at %s:%s", settings.obs_host, settings.obs_port)
                logs.push("SYSTEM", f"Connecté à OBS WebSocket ({settings.obs_host}:{settings.obs_port}).")
            except Exception as exc:  # noqa: BLE001 - surfacing any client/connection failure
                self._client = None
                logger.error("Failed to connect to OBS WebSocket: %s", exc)
                logs.push("ERROR", f"Connexion à OBS impossible : {exc}")
                raise ObsConnectionError(str(exc)) from exc

    async def disconnect(self) -> None:
        async with self._lock:
            if self._client is not None:
                try:
                    await asyncio.to_thread(self._client.disconnect)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Error while disconnecting from OBS: %s", exc)
                self._client = None

    async def _call(self, fn_name: str, *args, **kwargs):
        if self._client is None:
            await self.connect()
        assert self._client is not None
        try:
            fn = getattr(self._client, fn_name)
            return await asyncio.to_thread(fn, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OBS call %s failed (%s); dropping connection for retry.", fn_name, exc)
            self._client = None
            raise ObsConnectionError(str(exc)) from exc

    async def list_scenes(self) -> list[str]:
        resp = await self._call("get_scene_list")
        scenes = getattr(resp, "scenes", [])
        return [s["sceneName"] for s in scenes]

    async def set_current_scene(self, scene_name: str) -> None:
        await self._call("set_current_program_scene", scene_name)
        logs.push("INFO", f"Scène OBS changée -> {scene_name}")

    async def create_scene(self, scene_name: str) -> None:
        try:
            await self._call("create_scene", scene_name)
            logs.push("INFO", f"Scène OBS créée -> {scene_name}")
        except ObsConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - e.g. scene already exists
            logger.info("create_scene(%s) skipped: %s", scene_name, exc)


obs_manager = ObsManager()
