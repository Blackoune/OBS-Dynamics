"""Global hotkey listener backing the "Touches & Overlay Dynamiques" tab.

Converts the human-friendly combo strings used in the UI (e.g. "Ctrl+Shift+S",
"G", "Shift+Alt+C") into pynput's `<ctrl>+<shift>+s` syntax and registers a
`pynput.keyboard.GlobalHotKeys` listener. The listener is rebuilt whenever
hotkeys are added/removed so the running app always reflects the latest
config without a restart.

Defensive by design: pynput's global listener needs OS-level input hooks
(a display server on Linux, Accessibility permissions on macOS). On a
headless host this raises at start() time; we catch it, log once, and keep
the rest of the app fully functional.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from core import logs

logger = logging.getLogger("obs_dynamics.hotkeys")

try:
    from pynput import keyboard  # requires an X/Wayland/Win/macOS input backend
except Exception as _import_exc:  # noqa: BLE001 - e.g. no display server available
    keyboard = None  # type: ignore[assignment]
    logger.warning("pynput unavailable on this host (%s); global hotkeys disabled.", _import_exc)

_ALIASES = {
    "ctrl": "<ctrl>",
    "control": "<ctrl>",
    "alt": "<alt>",
    "shift": "<shift>",
    "cmd": "<cmd>",
    "win": "<cmd>",
    "super": "<cmd>",
}


def _to_pynput_combo(combo: str) -> str:
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty hotkey combo")
    out = []
    for part in parts:
        lower = part.lower()
        out.append(_ALIASES.get(lower, lower if len(lower) > 1 else part.lower()))
    return "+".join(out)


class HotkeyManager:
    def __init__(self) -> None:
        self._listener: Any = None
        self._lock = threading.Lock()
        self._available = keyboard is not None
        if not self._available:
            logs.push("WARN", "Raccourcis globaux indisponibles (aucun serveur d'affichage détecté).")

    def rebuild(self, hotkeys: list[dict]) -> None:
        """Re-registers the OS-level listener from the current hotkey list."""
        if not self._available:
            return
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._listener = None

            mapping: dict[str, Any] = {}
            for hotkey in hotkeys:
                combo_raw = hotkey.get("key", "")
                action = hotkey.get("action", "action")
                hotkey_id = hotkey.get("id", "")
                try:
                    combo = _to_pynput_combo(combo_raw)
                except ValueError:
                    logger.warning("Skipping malformed hotkey combo %r", combo_raw)
                    continue
                mapping[combo] = self._make_callback(hotkey_id, action)

            if not mapping:
                return
            try:
                self._listener = keyboard.GlobalHotKeys(mapping)
                self._listener.start()
                logs.push("SYSTEM", f"{len(mapping)} raccourci(s) global(aux) actif(s).")
            except Exception as exc:  # noqa: BLE001 - no display/permissions, etc.
                self._available = False
                self._listener = None
                logger.warning("Global hotkeys unavailable on this host: %s", exc)
                logs.push("WARN", f"Raccourcis globaux indisponibles sur cet hôte : {exc}")

    @staticmethod
    def _make_callback(hotkey_id: str, action: str):
        def _callback() -> None:
            logs.push("INFO", f"Raccourci déclenché : {action}")

        return _callback

    def stop(self) -> None:
        with self._lock:
            if self._listener is not None:
                try:
                    self._listener.stop()
                except Exception:  # noqa: BLE001
                    pass
                self._listener = None


hotkey_manager = HotkeyManager()
