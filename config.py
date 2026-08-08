"""Centralized, validated configuration loading.

Fixes vs. the original project:
- The OBS websocket password is NO LONGER read from obs_config.json (it was
  committed to git in plaintext). It now comes exclusively from the
  OBS_PASSWORD environment variable / .env file.
- config.json's duplicate/conflicting "obs_server"/"port" fields (which
  pointed at the legacy OBS WebSocket v4 default port 4444, while
  obs_config.json pointed at the v5 default port 4455) have been removed.
  obs_config.json is now the single source of truth for the OBS connection.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("obs_dynamics.config")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
IMAGES_DIR = DATA_DIR / "images"
MEDIA_DIR = DATA_DIR / "media"

load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    obs_host: str
    obs_port: int
    obs_password: str
    scan_interval_seconds: float
    match_threshold: float
    debug: bool
    secret_key: str
    app_hotkeys: dict[str, str]


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        logger.warning("Config file %s not found, using defaults.", path)
        return {}
    except json.JSONDecodeError as exc:
        logger.error("Config file %s is not valid JSON: %s", path, exc)
        return {}


def load_settings() -> Settings:
    obs_cfg = _read_json(BASE_DIR / "obs_config.json")
    app_cfg = _read_json(BASE_DIR / "config.json")

    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        # Never crash the app for a missing dev secret: generate an ephemeral
        # one and warn loudly, since a rotating key just means sessions are
        # invalidated on restart, not a security hole.
        secret_key = secrets.token_hex(32)
        logger.warning(
            "SECRET_KEY not set in environment/.env — using a random, "
            "ephemeral key. Sessions will not survive a restart. Set "
            "SECRET_KEY in .env for production use."
        )

    obs_password = os.getenv("OBS_PASSWORD", "")
    if not obs_password:
        logger.warning(
            "OBS_PASSWORD not set — will attempt an unauthenticated OBS "
            "WebSocket connection. Set it in .env if your OBS server "
            "requires a password."
        )

    return Settings(
        obs_host=str(obs_cfg.get("host", "localhost")),
        obs_port=int(obs_cfg.get("port", 4455)),
        obs_password=obs_password,
        scan_interval_seconds=float(obs_cfg.get("scan_interval_seconds", 1.0)),
        match_threshold=float(obs_cfg.get("match_threshold", 0.8)),
        debug=bool(obs_cfg.get("debug", False)),
        secret_key=secret_key,
        app_hotkeys=dict(app_cfg.get("app_hotkeys", {})),
    )


def ensure_data_dirs() -> None:
    for directory in (DATA_DIR, IMAGES_DIR, MEDIA_DIR):
        directory.mkdir(parents=True, exist_ok=True)


settings = load_settings()
