"""Déclencheurs : modèle, persistance, combinaisons, serveur overlay."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

import hotkeys
from overlay_server import OverlayServer
from triggers import TriggerRule, TriggerStore


# --- Modèle & persistance ---------------------------------------------- #

@pytest.fixture
def store(tmp_path):
    return TriggerStore(tmp_path / "triggers.json")


def test_new_rules_are_inserted_on_top(store):
    """Exigence UI : une nouvelle règle apparaît EN HAUT de la liste."""
    first = store.add()
    second = store.add()
    assert [r.id for r in store.load()] == [second.id, first.id]


def test_rule_is_incomplete_without_hotkey_or_media():
    assert not TriggerRule(id="a").is_complete
    assert not TriggerRule(id="a", hotkey="f5").is_complete
    assert not TriggerRule(id="a", media_path="x.png").is_complete
    assert TriggerRule(id="a", hotkey="f5", media_path="x.png").is_complete


def test_unknown_media_type_falls_back_to_image():
    assert TriggerRule.from_dict({"id": "a", "media_type": "hologramme"}).media_type == "image"


def test_duration_is_clamped():
    assert TriggerRule.from_dict({"id": "a", "duration_ms": 10}).duration_ms == 100
    assert TriggerRule.from_dict({"id": "a", "duration_ms": 999_999}).duration_ms == 60_000
    assert TriggerRule.from_dict({"id": "a", "duration_ms": "nan"}).duration_ms == 3000


def test_roundtrip_and_upsert(store):
    rule = store.add()
    rule.hotkey = "ctrl+shift+a"
    rule.media_path = "C:/img.png"
    assert store.upsert(rule)
    reloaded = store.get(rule.id)
    assert reloaded.hotkey == "ctrl+shift+a"
    assert reloaded.media_path == "C:/img.png"


def test_delete(store):
    a, b = store.add(), store.add()
    store.delete(a.id)
    assert [r.id for r in store.load()] == [b.id]


def test_conflict_detection(store):
    a = store.add()
    a.hotkey = "ctrl+a"
    store.upsert(a)
    b = store.add()
    assert store.conflicting("ctrl+a", exclude_id=b.id).id == a.id
    assert store.conflicting("ctrl+a", exclude_id=a.id) is None   # soi-même
    assert store.conflicting("") is None


def test_corrupted_file_returns_empty(tmp_path):
    p = tmp_path / "triggers.json"
    p.write_text("{{{ casse", encoding="utf-8")
    assert TriggerStore(p).load() == []


# --- Combinaisons de touches -------------------------------------------- #

class _Key:
    def __init__(self, name=None, char=None):
        if name is not None:
            self.name = name
        if char is not None:
            self.char = char


def test_combo_order_is_canonical():
    """ctrl+shift+a et shift+ctrl+a doivent produire la MÊME chaîne, sinon
    deux règles identiques ne se reconnaîtraient pas."""
    assert hotkeys.build_combo({"shift", "ctrl"}, "a") == "ctrl+shift+a"
    assert hotkeys.build_combo({"ctrl", "shift"}, "a") == "ctrl+shift+a"


def test_left_right_modifiers_are_merged():
    assert hotkeys.canonical_modifier("ctrl_l") == "ctrl"
    assert hotkeys.canonical_modifier("ctrl_r") == "ctrl"
    assert hotkeys.canonical_modifier("alt_gr") == "alt"


def test_format_combo_is_readable():
    assert hotkeys.format_combo("ctrl+shift+a") == "Ctrl + Shift + A"
    assert hotkeys.format_combo("f1") == "F1"
    assert hotkeys.format_combo("") == ""


def test_listener_builds_combo_from_held_modifiers():
    got = []
    lst = hotkeys.ComboListener(on_combo=got.append)
    lst._on_press(_Key(name="ctrl_l"))
    lst._on_press(_Key(name="shift"))
    lst._on_press(_Key(char="A"))
    assert got == ["ctrl+shift+a"]


def test_listener_releases_modifiers():
    got = []
    lst = hotkeys.ComboListener(on_combo=got.append)
    lst._on_press(_Key(name="ctrl_l"))
    lst._on_release(_Key(name="ctrl_l"))
    lst._on_press(_Key(char="a"))
    assert got == ["a"]      # plus de ctrl actif


def test_modifier_alone_emits_nothing():
    got = []
    hotkeys.ComboListener(on_combo=got.append)._on_press(_Key(name="shift"))
    assert got == []


def test_recorder_captures_once_then_stops():
    got = []
    rec = hotkeys.ComboRecorder(on_captured=got.append)
    rec._on_press(_Key(name="ctrl_l"))
    rec._on_press(_Key(name="f5"))
    rec._on_press(_Key(name="f6"))       # ignoré : capture déjà terminée
    assert got == ["ctrl+f5"]


def test_recorder_escape_cancels_with_empty_string():
    got = []
    hotkeys.ComboRecorder(on_captured=got.append)._on_press(_Key(name="esc"))
    assert got == [""]


# --- Serveur overlay ----------------------------------------------------- #

@pytest.fixture
def served(tmp_path):
    media = tmp_path / "m.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    rule = TriggerRule(id="rule1", hotkey="ctrl+a", media_type="image",
                       media_path=str(media), duration_ms=1234)
    srv = OverlayServer(lambda rid: rule if rid == rule.id else None, port=0)
    assert srv.start()
    yield srv, rule
    srv.stop()


def _get(srv, path):
    with urllib.request.urlopen(srv.base_url() + path, timeout=5) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def test_overlay_page_embeds_rule_parameters(served):
    srv, rule = served
    status, ctype, body = _get(srv, f"/overlay/{rule.id}")
    assert status == 200 and "text/html" in ctype
    assert b'"rule1"' in body and b"1234" in body and b'"image"' in body


def test_overlay_template_has_no_unsubstituted_token(served):
    """Régression : le template utilisait %-formatting, cassé par le "100%"
    du CSS. On vérifie qu'aucun jeton ne subsiste."""
    srv, rule = served
    _, _, body = _get(srv, f"/overlay/{rule.id}")
    assert b"__RULE_ID__" not in body
    assert b"__MEDIA_TYPE__" not in body
    assert b"__DURATION__" not in body
    assert b"100%" in body       # le CSS est bien intact


def test_media_is_served_with_guessed_mime(served):
    srv, rule = served
    status, ctype, body = _get(srv, f"/media/{rule.id}")
    assert status == 200 and ctype == "image/png" and body.startswith(b"\x89PNG")


def test_unknown_rule_returns_404(served):
    srv, _ = served
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(srv, "/overlay/inconnu")
    assert exc.value.code == 404


def test_health_endpoint(served):
    srv, _ = served
    assert _get(srv, "/health")[0] == 200


def test_fire_reaches_connected_source(served):
    srv, rule = served
    received: list[str] = []

    def listen():
        with urllib.request.urlopen(srv.base_url() + f"/events/{rule.id}", timeout=10) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    received.append(line)
                    return

    threading.Thread(target=listen, daemon=True).start()
    deadline = time.time() + 5
    while srv.listener_count(rule.id) == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert srv.listener_count(rule.id) == 1

    assert srv.fire(rule.id) == 1
    deadline = time.time() + 5
    while not received and time.time() < deadline:
        time.sleep(0.05)
    assert received and json.loads(received[0][5:].strip())["id"] == rule.id


def test_fire_without_source_reports_zero(served):
    """0 = la source navigateur n'est pas ouverte dans OBS : c'est le
    diagnostic principal quand « ça ne marche pas »."""
    srv, rule = served
    assert srv.fire(rule.id) == 0


def test_port_fallback_when_busy(served):
    """Si le port est pris, le serveur en prend un libre au lieu d'échouer."""
    srv, _ = served
    second = OverlayServer(lambda rid: None, port=srv.port)
    try:
        assert second.start()
        assert second.port != srv.port
    finally:
        second.stop()
