"""Raccourcis d'état de jeu : touche seule ET combinaison.

Les touches pynput sont simulées : ces tests n'ouvrent aucun listener clavier
et tournent donc en CI sans périphérique.
"""
from __future__ import annotations

import json

import pytest

import hotkeys as hk


class FakeKey:
    """Imite une touche pynput : `name` pour les spéciales, `char` sinon."""
    def __init__(self, name=None, char=None):
        if name is not None:
            self.name = name
        if char is not None:
            self.char = char


def _manager():
    fired: list[str] = []
    mgr = hk.HotkeyManager(on_hotkey=fired.append, bindings={
        "f1": "in_game",
        "ctrl+f2": "menu",
        "ctrl+shift+a": "inactive",
    })
    return mgr, fired


def test_single_key_still_works():
    """Régression : les hotkeys.json existants ne doivent pas casser."""
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="f1"))
    assert fired == ["in_game"]


def test_modifier_plus_key_fires_the_combo():
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="ctrl_l"))
    mgr._on_press(FakeKey(name="f2"))
    assert fired == ["menu"]


def test_two_modifiers():
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="ctrl_l"))
    mgr._on_press(FakeKey(name="shift_r"))
    mgr._on_press(FakeKey(char="A"))
    assert fired == ["inactive"]


def test_modifier_order_does_not_matter():
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="shift_l"))
    mgr._on_press(FakeKey(name="ctrl_r"))
    mgr._on_press(FakeKey(char="a"))
    assert fired == ["inactive"]


def test_bare_key_does_not_fire_when_a_modifier_is_held():
    """Ctrl+F1 ne doit PAS déclencher l'action liée à F1 seul."""
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="ctrl_l"))
    mgr._on_press(FakeKey(name="f1"))
    assert fired == []


def test_releasing_the_modifier_restores_the_bare_key():
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="ctrl_l"))
    mgr._on_release(FakeKey(name="ctrl_l"))
    mgr._on_press(FakeKey(name="f1"))
    assert fired == ["in_game"]


@pytest.mark.parametrize("written,expected", [
    ("Ctrl+Shift+F1", "ctrl+shift+f1"),
    ("shift+ctrl+f1", "ctrl+shift+f1"),
    ("CTRL + SHIFT + F1", "ctrl+shift+f1"),
    ("f1", "f1"),
    ("ctrl_r+a", "ctrl+a"),
])
def test_normalize_combo(written, expected):
    assert hk.normalize_combo(written) == expected


@pytest.mark.parametrize("bad", ["", "ctrl+alt", "+", "a+b"])
def test_normalize_combo_rejects_nonsense(bad):
    assert hk.normalize_combo(bad) == ""


def test_load_bindings_normalizes_and_drops_invalid(tmp_path):
    path = tmp_path / "hotkeys.json"
    path.write_text(json.dumps({
        "Shift+Ctrl+F5": "menu",     # ordre inversé + majuscules
        "f6": "in_game",
        "ctrl+alt": "menu",          # pas de touche principale -> rejeté
        "f7": "pas_un_etat",         # état inconnu -> rejeté
    }), encoding="utf-8")
    assert hk.load_bindings(path) == {"ctrl+shift+f5": "menu", "f6": "in_game"}


def test_stop_clears_stuck_modifiers():
    """Un modificateur encore 'enfoncé' au redémarrage fausserait tout."""
    mgr, fired = _manager()
    mgr._on_press(FakeKey(name="ctrl_l"))
    mgr.stop()
    mgr._on_press(FakeKey(name="f1"))
    assert fired == ["in_game"]
