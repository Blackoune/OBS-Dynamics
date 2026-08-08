"""In-memory ring buffer feeding the "Journal d'activité (Console)" panel
in the UI. Kept separate from Python's `logging` module output (which still
goes to obs_automation.log) so the frontend has a small, cheap, poll-able
feed instead of tailing a growing log file.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Literal

Level = Literal["SYSTEM", "INFO", "WARN", "ERROR"]

_MAX_LINES = 300
_buffer: deque[dict] = deque(maxlen=_MAX_LINES)


def push(level: Level, message: str) -> None:
    _buffer.append({"timestamp": time.time(), "level": level, "message": message})


def recent(since: float = 0.0) -> list[dict]:
    return [line for line in _buffer if line["timestamp"] > since]
