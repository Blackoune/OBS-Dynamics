"""Simple, robust JSON-file-backed storage.

Every read/write goes through an asyncio.Lock so concurrent requests never
interleave writes and corrupt the file, and writes are atomic (write to a
temp file, then os.replace) so a crash mid-write can never leave a
half-written, unparseable JSON file behind.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger("obs_dynamics.storage")


class JsonListStore:
    """Persists a JSON array of dict records to disk, keyed by "id"."""

    def __init__(self, path: Path, default: list[dict[str, Any]] | None = None) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._default = default if default is not None else []
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write_sync(self._default)

    def _read_sync(self) -> list[dict[str, Any]]:
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, list):
                logger.error("%s did not contain a JSON array; resetting.", self._path)
                return []
            return data
        except FileNotFoundError:
            return []
        except json.JSONDecodeError as exc:
            logger.error("Corrupt JSON in %s (%s); returning empty list.", self._path, exc)
            return []

    def _write_sync(self, records: list[dict[str, Any]]) -> None:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=f".{self._path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(records, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise

    async def all(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self._read_sync()

    async def get(self, record_id: str) -> dict[str, Any] | None:
        async with self._lock:
            for record in self._read_sync():
                if record.get("id") == record_id:
                    return record
        return None

    async def add(self, record: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            records = self._read_sync()
            records.append(record)
            self._write_sync(records)
        return record

    async def update(self, record_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            records = self._read_sync()
            for record in records:
                if record.get("id") == record_id:
                    record.update(patch)
                    self._write_sync(records)
                    return record
        return None

    async def delete(self, record_id: str) -> bool:
        async with self._lock:
            records = self._read_sync()
            filtered = [r for r in records if r.get("id") != record_id]
            if len(filtered) == len(records):
                return False
            self._write_sync(filtered)
        return True
