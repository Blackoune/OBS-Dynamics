"""
OBS Dynamics — Backend FastAPI.
Sert le dashboard (index.html), expose les endpoints jeux/hotkeys/OBS.

ATTENTION: les imports core.config / core.obs_client / core.scanner supposent
une API (get_settings, obs_client.connect/disconnect, scan_running_games).
Adapte les appels ci-dessous si tes fichiers core/ exposent une signature différente.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from core.config import get_settings
from core.obs_client import OBSClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("obs_dynamics")

BASE_DIR = Path(__file__).parent
app = FastAPI(title="OBS Dynamics")

# --- Static: sert index.html + assets/ ---
app.mount("/assets", StaticFiles(directory=BASE_DIR / "assets"), name="assets")

# Instance unique du client OBS (OBSClient est une classe, pas un singleton pré-fait)
obs_client = OBSClient(get_settings())


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(BASE_DIR / "index.html")


@app.on_event("startup")
async def startup_event() -> None:
    # start() = lance le superviseur avec reconnexion auto, ne bloque pas
    # même si OBS n'est pas encore ouvert (voir core/obs_client.py)
    await obs_client.start()
    logger.info("Superviseur de connexion OBS démarré.")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await obs_client.stop()


@app.post("/api/obs/reconnect")
async def reconnect_obs() -> dict[str, str]:
    """Recharge .env (OBS_WS_HOST/PORT/PASSWORD) et relance le client OBS."""
    global obs_client
    try:
        get_settings.cache_clear()  # get_settings() est en lru_cache -> vide le cache pour relire .env
        new_settings = get_settings()
        await obs_client.stop()
        obs_client = OBSClient(new_settings)
        await obs_client.start()
        return {"status": "reconnected"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/games/scan")
async def scan_games() -> list[dict]:
    """Scanne les processus actifs et retourne l'état de chaque jeu configuré.
    Import différé: core/scanner.py n'existe pas encore dans le projet."""
    try:
        from core.scanner import scan_running_games
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="core/scanner.py introuvable — module de scan pas encore implémenté.",
        )
    try:
        return await scan_running_games()
    except Exception as exc:
        logger.exception("Échec scan jeux")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
