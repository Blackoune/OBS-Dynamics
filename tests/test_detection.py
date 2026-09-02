"""Détection : cache de templates, downscale, machine à états, hotkeys."""
from __future__ import annotations

import numpy as np
import pytest

import hotkeys


# --- Downscale --------------------------------------------------------- #

def test_downscale_halves_dimensions(app_module):
    img = np.zeros((1440, 2560, 3), dtype=np.uint8)
    small = app_module._downscale(img)
    assert small.shape[:2] == (720, 1280)


def test_downscale_is_noop_at_scale_1(app_module):
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    assert app_module._downscale(img, 1.0) is img


def test_downscale_never_collapses_to_zero(app_module):
    """Une image minuscule ne doit pas produire une dimension nulle, qui
    ferait planter matchTemplate en aval."""
    small = app_module._downscale(np.zeros((1, 1, 3), dtype=np.uint8), 0.1)
    assert small.shape[0] >= 1 and small.shape[1] >= 1


# --- Cache de templates ------------------------------------------------- #

@pytest.fixture
def png(tmp_path):
    import cv2
    p = tmp_path / "tpl.png"
    cv2.imwrite(str(p), np.full((200, 300, 3), 128, dtype=np.uint8))
    return p


def test_template_is_cached_and_prescaled(app_module, png):
    cache = app_module._TemplateCache()
    first = cache.get(str(png))
    assert first is not None
    assert first.shape[:2] == (100, 150)      # déjà réduit
    assert cache.get(str(png)) is first        # même objet -> pas de relecture


def test_template_cache_invalidated_on_change(app_module, png):
    import cv2
    cache = app_module._TemplateCache()
    first = cache.get(str(png))
    cv2.imwrite(str(png), np.full((100, 150, 3), 64, dtype=np.uint8))
    second = cache.get(str(png))
    assert second is not None and second.shape != first.shape


def test_missing_template_returns_none(app_module, tmp_path):
    assert app_module._TemplateCache().get(str(tmp_path / "absent.png")) is None


# --- Machine à états ---------------------------------------------------- #

def test_inactive_when_process_absent(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "is_game_active", lambda g: False)
    game = app_module.Game(id="a", name="X", source="manual", active_match="x.exe")
    assert app_module.detect_game_state(game, 0.8) == "inactive"


def test_active_when_running_without_reference_images(app_module, monkeypatch):
    """Sans image de référence on ne peut pas distinguer menu/en-jeu : on
    reste sur 'active' plutôt que de deviner."""
    monkeypatch.setattr(app_module, "is_game_active", lambda g: True)
    game = app_module.Game(id="a", name="X", source="manual", active_match="x.exe")
    assert app_module.detect_game_state(game, 0.8) == "active"


def test_no_screen_capture_when_no_reference_images(app_module, monkeypatch):
    """Régression perf : ne jamais capturer l'écran si aucun jeu n'a d'image
    à comparer."""
    monkeypatch.setattr(app_module, "is_game_active", lambda g: True)
    called = []
    monkeypatch.setattr(app_module, "_capture_screen_bgr", lambda: called.append(1))
    game = app_module.Game(id="a", name="X", source="manual", active_match="x.exe")
    app_module.detect_game_state(game, 0.8)
    assert called == []


def test_state_label_covers_every_state(app_module):
    for state in ("inactive", "active", "menu", "in_game"):
        label = app_module.state_label(state)
        assert label and not label.startswith("GAME_STATE_")  # clé traduite


def test_state_label_unknown_falls_back(app_module):
    assert app_module.state_label("etat_inconnu") == app_module.state_label("inactive")


# --- Hotkeys ------------------------------------------------------------ #

class _Key:
    def __init__(self, name=None, char=None):
        if name is not None:
            self.name = name
        if char is not None:
            self.char = char


def test_function_key_normalised_lowercase():
    """Régression : pynput expose Key.f1.name == 'f1' en minuscule.
    L'ancienne implémentation indexait sur 'F1' et ne matchait jamais."""
    assert hotkeys.HotkeyManager._key_name(_Key(name="f1")) == "f1"


def test_char_key_normalised():
    assert hotkeys.HotkeyManager._key_name(_Key(char="K")) == "k"


def test_press_routes_to_state():
    got = []
    hotkeys.HotkeyManager(on_hotkey=got.append)._on_press(_Key(name="f2"))
    assert got == ["menu"]


def test_unbound_key_is_ignored():
    got = []
    hotkeys.HotkeyManager(on_hotkey=got.append)._on_press(_Key(name="f12"))
    assert got == []


def test_bindings_legacy_empty_list(tmp_path):
    p = tmp_path / "hk.json"
    p.write_text("[]", encoding="utf-8")
    assert hotkeys.load_bindings(p) == hotkeys.DEFAULT_BINDINGS


def test_bindings_reject_invalid_state(tmp_path):
    import json
    p = tmp_path / "hk.json"
    p.write_text(json.dumps({"f5": "menu", "f6": "PAS_UN_ETAT"}), encoding="utf-8")
    assert hotkeys.load_bindings(p) == {"f5": "menu"}


def test_bindings_roundtrip(tmp_path):
    p = tmp_path / "hk.json"
    assert hotkeys.save_bindings(p, {"f9": "in_game"})
    assert hotkeys.load_bindings(p) == {"f9": "in_game"}
