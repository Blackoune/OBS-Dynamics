"""OBS Dynamics — main entry point.

Run with:
    uvicorn app:app --host 0.0.0.0 --port 8000

The original file was a two-line stub that printed a message and exited —
none of the functionality described by index.html (auth, game/hotkey
management, script start/pause/stop, OBS scene switching, live scanning)
actually existed. This file wires all of that up on top of the modules in
core/.
"""
from __future__ import annotations

import logging
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from core import logs
from core.config import BASE_DIR, DATA_DIR, IMAGES_DIR, MEDIA_DIR, ensure_data_dirs, settings
from core.hotkeys import hotkey_manager
from core.obs_client import ObsConnectionError, obs_manager
from core.scanner import ScanEngine, ScriptState
from core.security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    authenticate,
    create_session_token,
    create_user,
    get_current_user,
    public_user,
    users_store,
)
from core.storage import JsonListStore

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "obs_automation.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("obs_dynamics.app")

ensure_data_dirs()

games_store = JsonListStore(DATA_DIR / "games.json")
hotkeys_store = JsonListStore(DATA_DIR / "hotkeys.json")
scan_engine = ScanEngine(games_store)

app = FastAPI(title="OBS Dynamics", version="1.0.0")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SetSceneRequest(BaseModel):
    scene_name: str = Field(min_length=1, max_length=256)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    logs.push("SYSTEM", "Interface initialisée. Prêt pour la connexion OBS WebSocket.")
    try:
        await obs_manager.connect()
    except ObsConnectionError:
        pass  # already logged; the app stays usable without OBS connected
    hotkey_manager.rebuild(await hotkeys_store.all())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await scan_engine.stop()
    hotkey_manager.stop()
    await obs_manager.disconnect()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
@app.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, response: Response) -> dict:
    user = await create_user(payload.email, payload.password)
    token = create_session_token(user["id"])
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax"
    )
    logs.push("SYSTEM", f"Nouveau compte créé : {user['email']}")
    return {"user": public_user(user)}


@app.post("/api/auth/login")
async def login(payload: LoginRequest, response: Response) -> dict:
    user = await authenticate(payload.email, payload.password)
    token = create_session_token(user["id"])
    response.set_cookie(
        SESSION_COOKIE_NAME, token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax"
    )
    logs.push("SYSTEM", f"Connexion : {user['email']}")
    return {"user": public_user(user)}


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(get_current_user)) -> dict:
    return {"user": public_user(user)}


@app.get("/api/auth/has-account")
async def has_account() -> dict:
    """Lets the frontend default to the login form vs. the register form."""
    return {"has_account": len(await users_store.all()) > 0}


# ---------------------------------------------------------------------------
# Script control (Lancer / Pause / Arrêter / Dossier Racine)
# ---------------------------------------------------------------------------
@app.get("/api/status")
async def get_status(user: dict = Depends(get_current_user)) -> dict:
    return {"state": scan_engine.state.value, "obs_connected": obs_manager.connected}


@app.post("/api/script/start")
async def script_start(user: dict = Depends(get_current_user)) -> dict:
    if scan_engine.state == ScriptState.PAUSED:
        await scan_engine.resume()
    else:
        await scan_engine.start()
    return {"state": scan_engine.state.value}


@app.post("/api/script/pause")
async def script_pause(user: dict = Depends(get_current_user)) -> dict:
    await scan_engine.pause()
    return {"state": scan_engine.state.value}


@app.post("/api/script/stop")
async def script_stop(user: dict = Depends(get_current_user)) -> dict:
    await scan_engine.stop()
    return {"state": scan_engine.state.value}


@app.post("/api/script/folder")
async def open_root_folder(user: dict = Depends(get_current_user)) -> dict:
    system = platform.system()
    try:
        if system == "Windows":
            subprocess.Popen(["explorer", str(BASE_DIR)])
        elif system == "Darwin":
            subprocess.Popen(["open", str(BASE_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(BASE_DIR)])
        logs.push("INFO", "Dossier racine ouvert.")
        return {"ok": True, "path": str(BASE_DIR)}
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Could not open file explorer: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Impossible d'ouvrir le dossier.") from exc


@app.get("/api/logs")
async def get_logs(since: float = 0.0, user: dict = Depends(get_current_user)) -> dict:
    return {"logs": logs.recent(since)}


@app.post("/api/scan")
async def manual_scan(user: dict = Depends(get_current_user)) -> dict:
    results = await scan_engine.scan_once()
    return {"results": results}


# ---------------------------------------------------------------------------
# OBS scenes
# ---------------------------------------------------------------------------
@app.get("/api/obs/scenes")
async def obs_scenes(user: dict = Depends(get_current_user)) -> dict:
    try:
        scenes = await obs_manager.list_scenes()
        return {"scenes": scenes, "connected": True}
    except ObsConnectionError:
        return {"scenes": [], "connected": False}


@app.post("/api/obs/scene")
async def obs_set_scene(payload: SetSceneRequest, user: dict = Depends(get_current_user)) -> dict:
    try:
        await obs_manager.set_current_scene(payload.scene_name)
        return {"ok": True}
    except ObsConnectionError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"OBS injoignable : {exc}") from exc


# ---------------------------------------------------------------------------
# Games
# ---------------------------------------------------------------------------
def _save_images(game_id: str, kind: Literal["menu", "ingame"], files: list[UploadFile]) -> list[str]:
    target_dir = IMAGES_DIR / game_id / kind
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for upload in files:
        if not upload.filename:
            continue
        if upload.content_type not in ("image/png", "application/octet-stream"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"'{upload.filename}' n'est pas un PNG.")
        safe_name = f"{uuid.uuid4().hex}.png"
        dest = target_dir / safe_name
        contents = upload.file.read()
        if not contents:
            continue
        dest.write_bytes(contents)
        saved.append(safe_name)
    return saved


@app.get("/api/games")
async def list_games(user: dict = Depends(get_current_user)) -> dict:
    return {"games": await games_store.all()}


@app.post("/api/games", status_code=status.HTTP_201_CREATED)
async def create_game(
    name: str = Form(...),
    exe: str = Form(...),
    create_scene: bool = Form(False),
    create_group: bool = Form(False),
    target_scene: str | None = Form(None),
    menu_images: list[UploadFile] = File(default_factory=list),
    ingame_images: list[UploadFile] = File(default_factory=list),
    user: dict = Depends(get_current_user),
) -> dict:
    game_id = uuid.uuid4().hex
    menu_saved = _save_images(game_id, "menu", menu_images)
    ingame_saved = _save_images(game_id, "ingame", ingame_images)

    if not menu_saved and not ingame_saved:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Au moins une image de détection (menu ou en jeu) est requise.",
        )

    record = {
        "id": game_id,
        "name": name.strip(),
        "exe": exe.strip(),
        "menu_images": menu_saved,
        "ingame_images": ingame_saved,
        "create_scene": create_scene,
        "create_group": create_group,
        "target_scene": target_scene.strip() if target_scene else None,
    }
    await games_store.add(record)

    if create_scene and target_scene:
        try:
            await obs_manager.create_scene(target_scene)
            await obs_manager.create_scene(f"{target_scene} - Menu")
        except ObsConnectionError as exc:
            logs.push("WARN", f"Jeu créé, mais la scène OBS n'a pas pu être créée : {exc}")

    logs.push("SYSTEM", f"Jeu ajouté : {record['name']} ({record['exe']})")
    return {"game": record}


@app.put("/api/games/{game_id}")
async def update_game(
    game_id: str,
    name: str | None = Form(None),
    exe: str | None = Form(None),
    create_scene: bool | None = Form(None),
    create_group: bool | None = Form(None),
    target_scene: str | None = Form(None),
    menu_images: list[UploadFile] = File(default_factory=list),
    ingame_images: list[UploadFile] = File(default_factory=list),
    user: dict = Depends(get_current_user),
) -> dict:
    existing = await games_store.get(game_id)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jeu introuvable.")

    patch: dict = {}
    if name is not None:
        patch["name"] = name.strip()
    if exe is not None:
        patch["exe"] = exe.strip()
    if create_scene is not None:
        patch["create_scene"] = create_scene
    if create_group is not None:
        patch["create_group"] = create_group
    if target_scene is not None:
        patch["target_scene"] = target_scene.strip() or None

    new_menu = _save_images(game_id, "menu", menu_images)
    if new_menu:
        patch["menu_images"] = [*existing.get("menu_images", []), *new_menu]
    new_ingame = _save_images(game_id, "ingame", ingame_images)
    if new_ingame:
        patch["ingame_images"] = [*existing.get("ingame_images", []), *new_ingame]

    updated = await games_store.update(game_id, patch)
    logs.push("SYSTEM", f"Jeu modifié : {updated.get('name')}")
    return {"game": updated}


@app.delete("/api/games/{game_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_game(game_id: str, user: dict = Depends(get_current_user)) -> Response:
    existing = await games_store.get(game_id)
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Jeu introuvable.")
    await games_store.delete(game_id)

    # Also drop hotkeys bound to this game so the UI never shows orphaned entries.
    for hotkey in await hotkeys_store.all():
        if hotkey.get("game_id") == game_id:
            await hotkeys_store.delete(hotkey["id"])
    hotkey_manager.rebuild(await hotkeys_store.all())

    game_dir = IMAGES_DIR / game_id
    if game_dir.exists():
        import shutil

        shutil.rmtree(game_dir, ignore_errors=True)

    logs.push("SYSTEM", f"Jeu supprimé : {existing.get('name')}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Hotkeys
# ---------------------------------------------------------------------------
@app.get("/api/hotkeys")
async def list_hotkeys(user: dict = Depends(get_current_user)) -> dict:
    return {"hotkeys": await hotkeys_store.all()}


@app.post("/api/hotkeys", status_code=status.HTTP_201_CREATED)
async def create_hotkey(
    game_id: str = Form(...),
    action: str = Form(...),
    key: str = Form(...),
    media: UploadFile | None = File(None),
    user: dict = Depends(get_current_user),
) -> dict:
    if not await games_store.get(game_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Jeu associé introuvable.")

    media_path: str | None = None
    if media is not None and media.filename:
        safe_name = f"{uuid.uuid4().hex}_{Path(media.filename).name}"
        dest = MEDIA_DIR / safe_name
        dest.write_bytes(media.file.read())
        media_path = safe_name

    record = {
        "id": uuid.uuid4().hex,
        "game_id": game_id,
        "action": action.strip(),
        "key": key.strip(),
        "media_path": media_path,
    }
    await hotkeys_store.add(record)
    hotkey_manager.rebuild(await hotkeys_store.all())
    logs.push("SYSTEM", f"Raccourci ajouté : {record['action']} ({record['key']})")
    return {"hotkey": record}


@app.delete("/api/hotkeys/{hotkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_hotkey(hotkey_id: str, user: dict = Depends(get_current_user)) -> Response:
    deleted = await hotkeys_store.delete(hotkey_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Raccourci introuvable.")
    hotkey_manager.rebuild(await hotkeys_store.all())
    logs.push("SYSTEM", "Raccourci supprimé.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


@app.get("/")
async def serve_index() -> FileResponse:
    return FileResponse(str(BASE_DIR / "index.html"))


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


def main() -> None:
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=settings.debug)


if __name__ == "__main__":
    main()
