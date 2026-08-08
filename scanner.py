"""Background scan engine.

State machine driven by the "Lancer / Pause / Arrêter" buttons in the UI:
- stopped: no background loop running.
- running: every `scan_interval_seconds`, for each configured game whose
  .exe is currently running (via psutil), a screenshot is taken and
  template-matched (OpenCV) against that game's saved "menu" and "en jeu"
  reference images. Whichever side wins (score above match_threshold) and
  differs from the last known state triggers an OBS scene switch.
- paused: loop is alive but skips the detection work each tick.

Defensive by design: screenshot capture requires a display. On a headless
host (e.g. a CI box or this sandbox) that will raise; we catch it, log once
per transition instead of spamming, and keep the loop alive so the app
never crashes just because it currently has no monitor attached.
"""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import psutil

from core import logs
from core.config import IMAGES_DIR, settings
from core.obs_client import ObsConnectionError, obs_manager
from core.storage import JsonListStore

logger = logging.getLogger("obs_dynamics.scanner")


class ScriptState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


def _load_template_scores(screenshot_bgr: np.ndarray, image_dir: Path) -> float:
    """Best match score of `screenshot_bgr` against every PNG in `image_dir`."""
    best = 0.0
    if not image_dir.is_dir():
        return best
    for image_path in image_dir.glob("*.png"):
        template = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if template is None:
            continue
        th, tw = template.shape[:2]
        sh, sw = screenshot_bgr.shape[:2]
        if th > sh or tw > sw:
            continue  # template larger than the screenshot: cannot match
        result = cv2.matchTemplate(screenshot_bgr, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        best = max(best, float(max_val))
    return best


class ScanEngine:
    def __init__(self, games_store: JsonListStore) -> None:
        self._games_store = games_store
        self._state = ScriptState.STOPPED
        self._task: asyncio.Task | None = None
        self._last_detected: dict[str, str] = {}  # game_id -> "menu" | "ingame"
        self._capture_warned = False

    @property
    def state(self) -> ScriptState:
        return self._state

    async def start(self) -> None:
        if self._state == ScriptState.RUNNING:
            return
        self._state = ScriptState.RUNNING
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="obs-scan-loop")
        logs.push("SYSTEM", "Script démarré.")

    async def pause(self) -> None:
        if self._state == ScriptState.STOPPED:
            logs.push("WARN", "Impossible de mettre en pause : le script n'est pas lancé.")
            return
        self._state = ScriptState.PAUSED
        logs.push("SYSTEM", "Script en pause.")

    async def resume(self) -> None:
        if self._state != ScriptState.PAUSED:
            return
        self._state = ScriptState.RUNNING
        logs.push("SYSTEM", "Script relancé.")

    async def stop(self) -> None:
        self._state = ScriptState.STOPPED
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._last_detected.clear()
        logs.push("SYSTEM", "Script arrêté.")

    async def _loop(self) -> None:
        try:
            while self._state != ScriptState.STOPPED:
                if self._state == ScriptState.RUNNING:
                    try:
                        await self.scan_once()
                    except Exception as exc:  # noqa: BLE001 - never let the loop die
                        logger.exception("Unhandled error during scan cycle: %s", exc)
                await asyncio.sleep(max(settings.scan_interval_seconds, 0.1))
        except asyncio.CancelledError:
            pass

    async def scan_once(self) -> list[dict[str, Any]]:
        """Runs one detection pass immediately. Returns per-game results."""
        games = await self._games_store.all()
        if not games:
            return []

        running_exes = {p.info.get("name") for p in psutil.process_iter(["name"])}

        screenshot_bgr = self._capture_screen()
        results: list[dict[str, Any]] = []
        if screenshot_bgr is None:
            return results

        for game in games:
            exe = game.get("exe")
            if exe not in running_exes:
                continue

            game_dir = IMAGES_DIR / game["id"]
            menu_score = _load_template_scores(screenshot_bgr, game_dir / "menu")
            ingame_score = _load_template_scores(screenshot_bgr, game_dir / "ingame")

            detected: str | None = None
            if max(menu_score, ingame_score) >= settings.match_threshold:
                detected = "menu" if menu_score >= ingame_score else "ingame"

            results.append(
                {
                    "game_id": game["id"],
                    "name": game.get("name"),
                    "menu_score": round(menu_score, 3),
                    "ingame_score": round(ingame_score, 3),
                    "detected": detected,
                }
            )

            if detected and detected != self._last_detected.get(game["id"]):
                self._last_detected[game["id"]] = detected
                await self._handle_transition(game, detected)

        return results

    async def _handle_transition(self, game: dict[str, Any], detected: str) -> None:
        target_scene = game.get("target_scene")
        if not game.get("create_scene") or not target_scene:
            return
        scene_name = target_scene if detected == "ingame" else f"{target_scene} - Menu"
        try:
            scenes = await obs_manager.list_scenes()
            if scene_name in scenes:
                await obs_manager.set_current_scene(scene_name)
            else:
                logs.push(
                    "WARN",
                    f"Scène OBS '{scene_name}' introuvable pour {game.get('name')} — changement ignoré.",
                )
        except ObsConnectionError as exc:
            logs.push("ERROR", f"Impossible de changer de scène OBS : {exc}")

    def _capture_screen(self) -> np.ndarray | None:
        try:
            import pyautogui  # imported lazily: requires a display at import time

            screenshot = pyautogui.screenshot()
            rgb = np.array(screenshot)
            self._capture_warned = False
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        except Exception as exc:  # noqa: BLE001 - e.g. no display attached
            if not self._capture_warned:
                logger.warning("Screen capture unavailable: %s", exc)
                logs.push("WARN", f"Capture d'écran indisponible : {exc}")
                self._capture_warned = True
            return None
