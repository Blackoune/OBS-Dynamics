"""
Structured logging setup: console + rotating file handler.

Design goals:
- Never raise on setup (fallback to console-only if file handler fails, e.g.
  read-only filesystem or missing permissions) -> no silent crash of the app
  because logging itself failed.
- Consistent formatter across all loggers so log parsing/alerting is reliable.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_dir: Path, level: int = logging.INFO,
                   max_bytes: int = 5_000_000, backup_count: int = 5) -> logging.Logger:
    """Idempotent logging setup. Safe to call multiple times."""
    global _CONFIGURED
    root = logging.getLogger("obs_manager")
    root.setLevel(level)

    if _CONFIGURED:
        return root

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    # Console handler — always succeeds.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root.addHandler(console_handler)

    # Rotating file handler — best-effort, never fatal.
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_dir / "obs_automation.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("File logging disabled (%s) — continuing with console-only logs.", exc)

    # Prevent double logging via root logger propagation in some deployments.
    root.propagate = False

    # Catch anything that slips past normal exception handling at the top level.
    def _log_uncaught_exceptions(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        root.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

    sys.excepthook = _log_uncaught_exceptions

    _CONFIGURED = True
    return root


def get_logger(name: str) -> logging.Logger:
    """Child logger under the 'obs_manager' namespace."""
    return logging.getLogger(f"obs_manager.{name}")
