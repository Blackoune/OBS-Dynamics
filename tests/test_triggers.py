"""Déclencheurs : modèle, persistance, combinaisons, serveur overlay."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

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
    assert TriggerRule.from_dict({"id": "a", "duration_ms": 10}).duration_ms == 50
    assert TriggerRule.from_dict({"id": "a", "duration_ms": 999_999}).duration_ms == 60_000
    assert TriggerRule.from_dict({"id": "a", "duration_ms": "nan"}).duration_ms == 3000


def test_zero_duration_means_hold_and_escapes_the_floor():
    """0 n'est pas une durée invalide à corriger : c'est le mode maintien."""
    from triggers import DURATION_HOLD
    assert TriggerRule.from_dict({"id": "a", "duration_ms": 0}).duration_ms == DURATION_HOLD
    assert TriggerRule.from_dict({"id": "a", "duration_ms": -5}).duration_ms == DURATION_HOLD


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
    lst._handle_release(_Key(name="ctrl_l"))
    lst._on_press(_Key(char="a"))
    assert got == ["a"]      # plus de ctrl actif


def test_key_repeat_does_not_refire():
    """Windows répète l'événement press tant que la touche est maintenue :
    sans garde, un maintien enverrait des dizaines de déclenchements/seconde."""
    got = []
    lst = hotkeys.ComboListener(on_combo=got.append)
    for _ in range(20):
        lst._on_press(_Key(name="f5"))
    assert got == ["f5"]


def test_release_emits_the_combo_that_was_fired():
    """Le relâchement est ancré sur la touche principale : lâcher Ctrl avant
    la lettre ne doit pas empêcher le hide de partir."""
    pressed, released = [], []
    lst = hotkeys.ComboListener(on_combo=pressed.append, on_release=released.append)
    lst._on_press(_Key(name="ctrl_l"))
    lst._on_press(_Key(char="m"))
    lst._handle_release(_Key(name="ctrl_l"))   # modificateur lâché en premier
    lst._handle_release(_Key(char="m"))
    assert pressed == ["ctrl+m"]
    assert released == ["ctrl+m"]


def test_release_without_prior_press_emits_nothing():
    released = []
    lst = hotkeys.ComboListener(on_combo=lambda c: None, on_release=released.append)
    lst._handle_release(_Key(name="f7"))
    assert released == []


def test_press_after_release_fires_again():
    got = []
    lst = hotkeys.ComboListener(on_combo=got.append)
    lst._on_press(_Key(name="f5"))
    lst._handle_release(_Key(name="f5"))
    lst._on_press(_Key(name="f5"))
    assert got == ["f5", "f5"]


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


def test_overlay_preloads_media_without_cache_buster(served):
    """Régression latence : le média doit être chargé UNE fois au chargement
    de la page. L'ancienne version réassignait src avec ?t=<timestamp> à
    chaque déclenchement, imposant un aller-retour HTTP + un redécodage avant
    le moindre pixel — ressenti comme un temps de réaction."""
    srv, rule = served
    _, _, body = _get(srv, f"/overlay/{rule.id}")
    # Pas d'horodatage : ce paramètre-là changeait à CHAQUE déclenchement et
    # forçait un rechargement réseau avant le premier pixel.
    assert b"?t=" not in body
    assert b"Date.now()" not in body
    # Le média est chargé une fois au chargement de la page...
    assert b"loadMedia();" in body
    # ...et l'URL ne porte qu'une empreinte STABLE, qui ne bouge que si le
    # fichier lui-même a changé.
    assert b'"?v=" + encodeURIComponent(mediaStamp)' in body
    # Afficher/masquer n'est qu'une bascule CSS.
    assert b"visibility" in body
    assert b'classList.add("on")' in body


def test_overlay_handles_hide_action(served):
    srv, rule = served
    _, _, body = _get(srv, f"/overlay/{rule.id}")
    assert b'action === "hide"' in body
    assert b"function hide()" in body


def test_fire_carries_the_action(served):
    srv, rule = served
    received: list[str] = []

    def listen():
        with urllib.request.urlopen(srv.base_url() + f"/events/{rule.id}", timeout=10) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    received.append(line[5:].strip())
                    if len(received) == 2:
                        return

    threading.Thread(target=listen, daemon=True).start()
    deadline = time.time() + 5
    while srv.listener_count(rule.id) == 0 and time.time() < deadline:
        time.sleep(0.05)

    srv.fire(rule.id, action="show")
    srv.fire(rule.id, action="hide")
    deadline = time.time() + 5
    while len(received) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert [json.loads(r)["action"] for r in received] == ["show", "hide"]


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


def test_event_carries_current_duration(served):
    """Régression signalée par l'utilisateur : changer la durée dans l'app
    n'avait aucun effet sur une source OBS déjà ouverte. La page était figée
    sur la valeur reçue au chargement, donc une règle passée un jour par
    « Maintien » restait en maintien pour toujours. La config courante doit
    voyager avec CHAQUE événement."""
    srv, rule = served
    received: list[dict] = []

    def listen(n):
        with urllib.request.urlopen(srv.base_url() + f"/events/{rule.id}", timeout=10) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    received.append(json.loads(line[5:].strip()))
                    if len(received) == n:
                        return

    threading.Thread(target=listen, args=(2,), daemon=True).start()
    deadline = time.time() + 5
    while srv.listener_count(rule.id) == 0 and time.time() < deadline:
        time.sleep(0.05)

    rule.duration_ms = 10_000          # l'utilisateur choisit 10 s
    srv.fire(rule.id)
    rule.duration_ms = 0               # puis bascule sur Maintien
    srv.fire(rule.id)

    deadline = time.time() + 5
    while len(received) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert [e["duration"] for e in received] == [10_000, 0]


def test_event_carries_media_stamp_and_kind(served):
    """L'empreinte du média permet à la page de détecter un fichier remplacé
    et de le recharger, sans perdre le préchargement le reste du temps."""
    srv, rule = served
    payload_seen: list[dict] = []

    def listen():
        with urllib.request.urlopen(srv.base_url() + f"/events/{rule.id}", timeout=10) as r:
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data:"):
                    payload_seen.append(json.loads(line[5:].strip()))
                    return

    threading.Thread(target=listen, daemon=True).start()
    deadline = time.time() + 5
    while srv.listener_count(rule.id) == 0 and time.time() < deadline:
        time.sleep(0.05)
    srv.fire(rule.id)
    deadline = time.time() + 5
    while not payload_seen and time.time() < deadline:
        time.sleep(0.05)

    ev = payload_seen[0]
    assert ev["kind"] == "image"
    assert ev["media"] and "-" in ev["media"]     # mtime-taille


def test_media_stamp_changes_when_file_changes(served, tmp_path):
    from overlay_server import OverlayServer
    srv, rule = served
    before = OverlayServer.media_stamp(rule)
    Path(rule.media_path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 999)
    assert OverlayServer.media_stamp(rule) != before


def test_media_stamp_is_empty_for_missing_file():
    from overlay_server import OverlayServer
    assert OverlayServer.media_stamp(TriggerRule(id="x", media_path="nexistepas.png")) == ""


def test_overlay_page_uses_mutable_config_not_constants(served):
    """La page doit lire des variables réassignables, pas des const figées."""
    srv, rule = served
    _, _, body = _get(srv, f"/overlay/{rule.id}")
    assert b"let duration =" in body
    assert b"let kind =" in body
    assert b"let mediaStamp =" in body
    assert b"const DURATION" not in body      # l'ancienne constante a disparu
    assert b"d.duration" in body               # et la valeur vient de l'événement


def test_saturated_queue_evicts_oldest_not_newest():
    """Sécurité du mode maintien : si la file sature, c'est le plus ANCIEN
    événement qui saute. Jeter le plus récent pourrait perdre un « hide » et
    laisser l'overlay collé à l'écran par-dessus le jeu."""
    from overlay_server import _Broker
    broker = _Broker()
    q = broker.subscribe("r")
    for i in range(50):                       # bien au-delà de maxsize
        broker.publish("r", {"n": i, "action": "show"})
    broker.publish("r", {"n": 999, "action": "hide"})

    drained = []
    while not q.empty():
        drained.append(json.loads(q.get_nowait()))
    assert drained[-1]["action"] == "hide"    # le plus récent a survécu
    assert drained[-1]["n"] == 999


def test_port_fallback_when_busy(served):
    """Si le port est pris, le serveur en prend un libre au lieu d'échouer."""
    srv, _ = served
    second = OverlayServer(lambda rid: None, port=srv.port)
    try:
        assert second.start()
        assert second.port != srv.port
    finally:
        second.stop()
