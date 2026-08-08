"""
Robust OBS WebSocket v5 client wrapper.

Guarantees:
- No silent crash: every OBS call is wrapped, logged, and raises a typed
  OBSClientError (never lets a raw library exception bubble up unlogged).
- Auto-reconnect with exponential backoff + jitter, capped at ws_reconnect_max_delay.
- Connection state is always consistent: `is_connected` reflects reality even
  if the underlying socket dies mid-request; no stale "connected" flag.
- Event-driven: OBS events (scene changes, stream state, etc.) are dispatched
  to registered async callbacks instead of being polled.
"""
from __future__ import annotations

import asyncio
import random
from enum import Enum
from typing import Any, Awaitable, Callable

import simpleobsws

from core.config import Settings
from core.logging_config import get_logger

logger = get_logger("obs_client")

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    SHUTTING_DOWN = "shutting_down"


class OBSClientError(Exception):
    """Raised for any recoverable OBS communication failure."""


class OBSRequestError(OBSClientError):
    """Raised when OBS returns a non-success response to a request."""

    def __init__(self, request_type: str, code: int | None, comment: str | None):
        self.request_type = request_type
        self.code = code
        self.comment = comment
        super().__init__(f"OBS request '{request_type}' failed (code={code}): {comment}")


class OBSClient:
    """
    Manages a single resilient connection to obs-websocket v5.

    Usage:
        client = OBSClient(settings)
        await client.start()   # connects + spawns reconnect/heartbeat supervisor
        ...
        await client.stop()    # graceful shutdown, cancels background tasks
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ws: simpleobsws.WebSocketClient | None = None
        self._state: ConnectionState = ConnectionState.DISCONNECTED
        self._state_lock = asyncio.Lock()
        self._supervisor_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._event_callbacks: list[EventCallback] = []
        self._stop_requested = False
        self._reconnect_attempt = 0

    # ------------------------------------------------------------------ #
    # Public state
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED and self._ws is not None

    def register_event_callback(self, callback: EventCallback) -> None:
        self._event_callbacks.append(callback)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        """Start the connection supervisor. Non-blocking: returns once the
        first connection attempt has been scheduled (does not guarantee an
        immediate successful connection — reconnect logic handles that)."""
        self._stop_requested = False
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(
                self._supervisor_loop(), name="obs_supervisor"
            )
        logger.info("OBS client supervisor started (target=%s)", self._settings.ws_url)

    async def stop(self) -> None:
        """Graceful shutdown: cancels background tasks and closes the socket cleanly."""
        self._stop_requested = True
        await self._set_state(ConnectionState.SHUTTING_DOWN)

        for task in (self._heartbeat_task, self._supervisor_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001 - log, never let shutdown crash
                    logger.exception("Error while cancelling background task during shutdown")

        await self._disconnect_socket()
        await self._set_state(ConnectionState.DISCONNECTED)
        logger.info("OBS client stopped cleanly.")

    # ------------------------------------------------------------------ #
    # Connection supervisor (reconnect with exponential backoff + jitter)
    # ------------------------------------------------------------------ #
    async def _supervisor_loop(self) -> None:
        while not self._stop_requested:
            try:
                await self._connect_once()
                self._reconnect_attempt = 0  # reset backoff on success
                await self._run_until_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never let supervisor die silently
                logger.error("OBS connection attempt failed: %s", exc, exc_info=self._settings.debug)

            if self._stop_requested:
                break

            max_attempts = self._settings.ws_reconnect_max_attempts
            self._reconnect_attempt += 1
            if max_attempts and self._reconnect_attempt > max_attempts:
                logger.critical(
                    "OBS reconnection abandoned after %d attempts. "
                    "Manual intervention required.", max_attempts
                )
                await self._set_state(ConnectionState.DISCONNECTED)
                return

            delay = self._compute_backoff_delay(self._reconnect_attempt)
            await self._set_state(ConnectionState.RECONNECTING)
            logger.warning(
                "Reconnecting to OBS in %.1fs (attempt %d%s)...",
                delay, self._reconnect_attempt,
                f"/{max_attempts}" if max_attempts else "",
            )
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                raise

    def _compute_backoff_delay(self, attempt: int) -> float:
        base = self._settings.ws_reconnect_base_delay
        cap = self._settings.ws_reconnect_max_delay
        raw = min(cap, base * (2 ** min(attempt, 10)))
        jitter = random.uniform(0, raw * 0.25)
        return round(raw + jitter, 2)

    async def _connect_once(self) -> None:
        await self._set_state(ConnectionState.CONNECTING)
        params = simpleobsws.IdentificationParameters(ignoreNonFatalRequestChecks=False)
        ws = simpleobsws.WebSocketClient(
            url=self._settings.ws_url,
            password=self._settings.ws_password,
            identification_parameters=params,
        )
        ws.register_event_callback(self._on_raw_event)

        try:
            await asyncio.wait_for(ws.connect(), timeout=self._settings.ws_connect_timeout)
            await asyncio.wait_for(ws.wait_until_identified(), timeout=self._settings.ws_connect_timeout)
        except (asyncio.TimeoutError, OSError, ConnectionRefusedError) as exc:
            raise OBSClientError(f"Could not connect to OBS at {self._settings.ws_url}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - simpleobsws may raise its own error types
            raise OBSClientError(f"OBS identification failed: {exc}") from exc

        self._ws = ws
        await self._set_state(ConnectionState.CONNECTED)
        logger.info("Connected and identified with OBS WebSocket at %s", self._settings.ws_url)

        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(), name="obs_heartbeat")

    async def _run_until_disconnected(self) -> None:
        """Blocks while the connection is alive; returns (without raising) once
        the underlying socket drops, letting the supervisor reconnect."""
        while not self._stop_requested and self._ws is not None:
            try:
                connected = getattr(self._ws, "is_identified", None)
                still_up = connected() if callable(connected) else self.is_connected
            except Exception:  # noqa: BLE001
                still_up = False
            if not still_up:
                logger.warning("OBS connection lost.")
                break
            await asyncio.sleep(1.0)
        await self._disconnect_socket()
        if not self._stop_requested:
            await self._set_state(ConnectionState.DISCONNECTED)

    async def _heartbeat_loop(self) -> None:
        """Lightweight liveness probe so a half-open TCP socket is detected
        proactively instead of waiting for a user-triggered request to fail."""
        interval = self._settings.ws_heartbeat_interval
        while not self._stop_requested:
            try:
                await asyncio.sleep(interval)
                if self.is_connected:
                    await self.call("GetVersion", raise_on_error=True)
            except asyncio.CancelledError:
                raise
            except OBSClientError as exc:
                logger.warning("Heartbeat check failed, connection likely stale: %s", exc)
                await self._disconnect_socket()
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error in heartbeat loop")

    async def _disconnect_socket(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.disconnect()
            except Exception:  # noqa: BLE001 - disconnect must never raise
                logger.debug("Error while disconnecting OBS socket (ignored).", exc_info=True)

    async def _set_state(self, new_state: ConnectionState) -> None:
        async with self._state_lock:
            if self._state != new_state:
                logger.debug("OBS state: %s -> %s", self._state.value, new_state.value)
                self._state = new_state

    # ------------------------------------------------------------------ #
    # Requests
    # ------------------------------------------------------------------ #
    async def call(self, request_type: str, request_data: dict[str, Any] | None = None,
                    *, raise_on_error: bool = True) -> dict[str, Any]:
        """Send a typed request to OBS. Returns the response data dict.

        Raises:
            OBSClientError: if not connected.
            OBSRequestError: if OBS returns a non-success status and raise_on_error=True.
        """
        if not self.is_connected or self._ws is None:
            raise OBSClientError(f"Cannot call '{request_type}': not connected to OBS.")

        request = simpleobsws.Request(request_type, request_data or {})
        try:
            response = await self._ws.call(request)
        except Exception as exc:  # noqa: BLE001 - normalize all library errors
            logger.error("OBS request '%s' raised: %s", request_type, exc, exc_info=self._settings.debug)
            raise OBSClientError(f"Request '{request_type}' failed: {exc}") from exc

        ok = getattr(response, "ok", lambda: False)()
        if not ok:
            status = getattr(response, "requestStatus", None)
            code = getattr(status, "code", None)
            comment = getattr(status, "comment", None)
            logger.error("OBS request '%s' returned error: code=%s comment=%s", request_type, code, comment)
            if raise_on_error:
                raise OBSRequestError(request_type, code, comment)
        return getattr(response, "responseData", None) or {}

    # ------------------------------------------------------------------ #
    # Events
    # ------------------------------------------------------------------ #
    async def _on_raw_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        logger.debug("OBS event: %s | %s", event_type, event_data)
        for callback in list(self._event_callbacks):
            try:
                await callback(event_type, event_data)
            except Exception:  # noqa: BLE001 - one bad callback must not kill event dispatch
                logger.exception("Event callback raised for event '%s'", event_type)
