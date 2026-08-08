"""
Centralized, typed application configuration.

SECURITY: All secrets (OBS WebSocket password) MUST come from environment
variables / .env, never from a committed JSON file. obs_config.json is kept
read-only as a template with non-secret defaults only.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment / .env with strict typing."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="OBS_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- OBS WebSocket (protocol v5, default port 4455) ---
    ws_host: str = Field(default="localhost", description="OBS WebSocket host")
    ws_port: int = Field(default=4455, ge=1, le=65535, description="OBS WebSocket port")
    ws_password: str = Field(..., min_length=1, description="OBS WebSocket password (REQUIRED, no default)")
    ws_connect_timeout: float = Field(default=10.0, gt=0, description="Seconds before connect attempt times out")
    ws_reconnect_base_delay: float = Field(default=1.0, gt=0, description="Base backoff delay (s)")
    ws_reconnect_max_delay: float = Field(default=60.0, gt=0, description="Max backoff delay (s)")
    ws_reconnect_max_attempts: int = Field(default=0, ge=0, description="0 = infinite retries")
    ws_heartbeat_interval: float = Field(default=15.0, gt=0, description="Health-check ping interval (s)")

    # --- Image detection (from legacy obs_config.json, non-secret) ---
    scan_interval_seconds: float = Field(default=1.0, gt=0)
    match_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    # --- App ---
    debug: bool = Field(default=False)
    log_dir: Path = Field(default=Path("logs"))
    log_max_bytes: int = Field(default=5_000_000, gt=0)
    log_backup_count: int = Field(default=5, ge=0)

    @property
    def ws_url(self) -> str:
        return f"ws://{self.ws_host}:{self.ws_port}"

    @field_validator("ws_password")
    @classmethod
    def _reject_placeholder_password(cls, v: str) -> str:
        if v.strip().lower() in {"", "changeme", "your_password_here", "password"}:
            raise ValueError(
                "OBS_WS_PASSWORD is missing or still a placeholder. "
                "Set a real value in .env (see .env.example)."
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings singleton. Raises pydantic.ValidationError early (fail-fast) if misconfigured."""
    return Settings()
