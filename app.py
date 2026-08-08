"""
Main entry point for the OBS management backend.

Step 1 scope: secure config, structured logging, resilient OBS WebSocket v5
client with auto-reconnect, and a minimal FastAPI skeleton exposing a health
endpoint that reports true connection state. Frontend CRUD endpoints
(games/hotkeys) and image-detection engine are handled in later steps.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from core.config import Settings, get_settings
from core.logging_config import get_logger, setup_logging
from core.obs_client import OBSClient, OBSClientError, OBSRequestError

# --- Fail-fast config load: crash loudly at import time rather than at
# --- request time if secrets/config are missing or invalid. ---
try:
    settings: Settings = get_settings()
except ValidationError as exc:
    # Logging isn't configured yet at this point; print is intentional here.
    print(f"[FATAL] Invalid configuration, refusing to start:\n{exc}")
    raise SystemExit(1) from exc

logger = setup_logging(
    log_dir=settings.log_dir,
    level=10 if settings.debug else 20,  # DEBUG : INFO
    max_bytes=settings.log_max_bytes,
    backup_count=settings.log_backup_count,
)
app_logger = get_logger("app")

obs_client = OBSClient(settings)


async def _on_obs_event(event_type: str, event_data: dict[str, Any]) -> None:
    """Central event dispatcher. Extend here for scene/audio/recording events."""
    if event_type == "CurrentProgramSceneChanged":
        app_logger.info("Scene changed -> %s", event_data.get("sceneName"))
    elif event_type == "StreamStateChanged":
        app_logger.info("Stream state -> %s", event_data.get("outputState"))
    elif event_type == "RecordStateChanged":
        app_logger.info("Record state -> %s", event_data.get("outputState"))


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    obs_client.register_event_callback(_on_obs_event)
    try:
        await obs_client.start()
    except Exception:  # noqa: BLE001 - startup must not crash the whole app
        app_logger.exception("OBS client failed to start; API will report degraded state.")
    yield
    try:
        await obs_client.stop()
    except Exception:  # noqa: BLE001
        app_logger.exception("Error during OBS client shutdown.")


app = FastAPI(title="OBS Dynamics Control API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(OBSClientError)
async def obs_client_error_handler(_, exc: OBSClientError) -> JSONResponse:
    app_logger.warning("OBS client error surfaced to API: %s", exc)
    code = 502 if not isinstance(exc, OBSRequestError) else 422
    return JSONResponse(status_code=code, content={"error": "obs_error", "detail": str(exc)})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "api": "ok",
        "obs_connection_state": obs_client.state.value,
        "obs_connected": obs_client.is_connected,
    }


@app.get("/api/obs/version")
async def obs_version() -> dict[str, Any]:
    if not obs_client.is_connected:
        raise HTTPException(status_code=503, detail="OBS is not connected.")
    return await obs_client.call("GetVersion")


@app.get("/api/obs/scenes")
async def obs_scenes() -> dict[str, Any]:
    if not obs_client.is_connected:
        raise HTTPException(status_code=503, detail="OBS is not connected.")
    return await obs_client.call("GetSceneList")


def main() -> None:
    import uvicorn

    app_logger.info("Starting OBS manager on http://0.0.0.0:8000 (debug=%s)", settings.debug)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_config=None)


if __name__ == "__main__":
    main()
