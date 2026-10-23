"""Sous-titres, lot 2 : style, hub SSE, routes du serveur, fenêtre Style."""
from __future__ import annotations

import http.client
import json
import queue

import pytest

import subtitles_style as sstyle
from overlay_server import OverlayServer
from subtitles_overlay import _PAGE, SubtitlesHub


# -- style ----------------------------------------------------------------- #

def test_from_dict_keeps_valid_values_and_drops_bad_ones():
    s = sstyle.from_dict({
        "font_family": "Comic Sans MS", "font_size": 60.4, "font_weight": 900,
        "text_color": "#ff00AA", "outline_width": 99, "align": "right",
        "bg_mode": "band", "italic": "yes", "lines": True, "preset": "karaoke"})
    assert (s.font_family, s.font_size, s.font_weight) == ("Comic Sans MS", 60, 900)
    assert s.text_color == "#ff00AA" and s.align == "right" and s.bg_mode == "band"
    assert s.outline_width == sstyle.Style().outline_width        # hors bornes
    assert s.italic is False and s.lines == sstyle.Style().lines  # mauvais types
    assert s.preset == "karaoke"


@pytest.mark.parametrize("font", ['Arial", x', "a;}body{", "<b>", "", "x" * 80, 3])
def test_font_names_that_could_escape_css_are_refused(font):
    assert sstyle.from_dict({"font_family": font}).font_family == "Segoe UI"


@pytest.mark.parametrize("color", ["red", "#fff", "#12345G", "#1234567", "url(x)"])
def test_bad_colors_are_refused(color):
    assert sstyle.from_dict({"text_color": color}).text_color == "#FFFFFF"


def test_from_dict_survives_garbage():
    assert sstyle.from_dict(None) == sstyle.Style()
    assert sstyle.from_dict({"font_weight": True, "preset": ["x"]}) == sstyle.Style()


def test_edit_switches_to_custom_and_presets_are_valid():
    s = sstyle.edit(sstyle.PRESETS["outline"], font_size=50)
    assert s.preset == sstyle.CUSTOM and s.font_size == 50
    assert sstyle.edit(s, font_size=999) == s                     # refusé
    for key, preset in sstyle.PRESETS.items():
        assert sstyle.from_dict(preset.to_dict()) == preset
        assert preset.preset == key


# -- hub ------------------------------------------------------------------- #

def _drain(sub):
    out = []
    while True:
        try:
            out.append(json.loads(sub.get_nowait()))
        except queue.Empty:
            return out


def test_token_and_style_persist(tmp_path):
    path = tmp_path / "w.json"
    hub = SubtitlesHub(path)
    token = hub.ensure_token()
    hub.set_style(sstyle.PRESETS["band"])
    again = SubtitlesHub(path)
    assert again.ensure_token() == token and again.style == sstyle.PRESETS["band"]
    assert again.token_matches(token) and not again.token_matches("nope")
    new = again.regenerate_token()
    assert new != token and not again.token_matches(token)


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "w.json"
    path.write_text('{"style": {"text_color": "javascript:"}, "token": 5}', encoding="utf-8")
    hub = SubtitlesHub(path)
    assert hub.style == sstyle.Style() and hub.ensure_token()


def test_non_ascii_token_is_rejected_not_crashing(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    hub.ensure_token()
    assert hub.token_matches("é") is False


def test_full_queue_is_reset_to_full_state(tmp_path):
    """Un `style` jeté de la file ne se rattrape pas : quand une page est en
    retard, elle reçoit l'état complet à la place."""
    hub = SubtitlesHub(tmp_path / "w.json")
    sub, _first = hub.subscribe()
    hub.set_style(sstyle.PRESETS["band"], persist=False)
    for i in range(100):
        hub.publish_text("partial", str(i))
    got = _drain(sub)
    assert any(m["type"] == "init" and m["style"]["bg_mode"] == "band" for m in got)
    assert got[-1] == {"type": "partial", "text": "99", "id": -1}


def test_revision_replaces_the_right_line(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    hub.publish_text("final", "Elden Rin.", 7)
    hub.publish_text("final", "Suite.", 8)
    sub, _first = hub.subscribe()
    hub.publish_text("revise", "Elden Ring.", 7)
    hub.publish_text("revise", "Inconnue.", 99)          # déjà sortie : ignorée
    assert _drain(sub) == [{"type": "revise", "text": "Elden Ring.", "id": 7}]
    assert [l["text"] for l in hub.initial_payload()["lines"]] == ["Elden Ring.", "Suite."]
    assert "revise(m.text, m.id)" in _PAGE


def test_empty_final_is_ignored(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    hub.publish_text("final", "  ")
    assert hub.initial_payload()["lines"] == []


def test_publish_reaches_subscribers_and_respects_switch(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    sub, first = hub.subscribe()
    assert first["type"] == "init"
    hub.publish_text("partial", "bon")
    hub.publish_text("final", "Bonjour.")
    assert _drain(sub) == [{"type": "partial", "text": "bon", "id": -1},
                           {"type": "final", "text": "Bonjour.", "id": -1}]
    hub.set_enabled(False)
    _drain(sub)
    hub.publish_text("final", "caché")
    assert _drain(sub) == []
    hub.unsubscribe(sub)
    assert hub.listener_count() == 0


def test_initial_payload_replays_recent_lines(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    hub.publish_text("final", "Ligne une.")
    hub.publish_text("partial", "en cours")
    init = hub.initial_payload()
    assert init["type"] == "init" and init["style"] == hub.style.to_dict()
    assert [l["text"] for l in init["lines"]] == ["Ligne une."]
    assert init["partial"] == "en cours"
    hub.clear()
    assert hub.initial_payload()["lines"] == []


def test_slow_page_drops_oldest_instead_of_blocking(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    sub, _first = hub.subscribe()
    for i in range(200):
        hub.publish_text("partial", str(i))
    got = _drain(sub)
    assert got[-1]["text"] == "199" and len(got) <= 64


def test_page_never_injects_html():
    assert "innerHTML" not in _PAGE and "insertAdjacentHTML" not in _PAGE
    assert "textContent" in _PAGE


# -- routes ---------------------------------------------------------------- #

@pytest.fixture
def served(tmp_path):
    hub = SubtitlesHub(tmp_path / "w.json")
    hub.ensure_token()
    server = OverlayServer(lambda _id: None, port=0, subtitles_hub=hub)
    assert server.start()
    try:
        yield server, hub
    finally:
        server.stop()


def _get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{server.port}"})
    return conn, conn.getresponse()


def test_page_route_needs_the_token(served):
    server, hub = served
    url = server.subtitles_url()
    assert url.endswith("/subtitles/" + hub.ensure_token())
    conn, resp = _get(server, "/subtitles/" + hub.ensure_token())
    body = resp.read().decode()
    conn.close()
    assert resp.status == 200 and "default-src 'none'" in resp.getheader("Content-Security-Policy")
    assert json.dumps(hub.ensure_token()) in body
    conn, resp = _get(server, "/subtitles/wrong")
    resp.read()
    conn.close()
    assert resp.status == 404


def test_event_stream_starts_with_style(served):
    server, hub = served
    conn, resp = _get(server, "/subevents/" + hub.ensure_token())
    assert resp.status == 200
    lines = []
    while not any(l.startswith("data: ") for l in lines):
        lines.append(resp.fp.readline().decode().strip())
    conn.close()
    assert "event: subs" in lines
    first = json.loads(next(l for l in lines if l.startswith("data: "))[6:])
    assert first["type"] == "init" and first["style"]["font_family"] == "Segoe UI"


# -- fenêtre Style --------------------------------------------------------- #

ctk = pytest.importorskip("customtkinter")


@pytest.fixture
def racine():
    try:
        root = ctk.CTk()
    except Exception as exc:
        pytest.skip(f"pas d'affichage disponible : {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


def test_style_dialog_pushes_live_then_saves_once(racine):
    from ui_subtitles_style import SubtitleStyleDialog
    calls = []
    dlg = SubtitleStyleDialog(racine, sstyle.Style(), on_change=lambda s, p: calls.append((s, p)))
    racine.update()
    dlg._slide("font_size", 70.2)
    dlg._slide("font_size", 71)
    assert [p for _s, p in calls] == [False, False]
    dlg.close()                                    # écrit ce qui attendait
    assert calls[-1][1] is True and calls[-1][0].font_size == 71
    assert calls[-1][0].preset == sstyle.CUSTOM


def test_style_dialog_preset_resets_every_control(racine):
    from ui_subtitles_style import SubtitleStyleDialog
    calls = []
    dlg = SubtitleStyleDialog(racine, sstyle.Style(), on_change=lambda s, p: calls.append(s))
    racine.update()
    dlg._apply_preset("band")
    assert calls[-1] == sstyle.PRESETS["band"]
    assert dlg._widgets["font_size"].get() == sstyle.PRESETS["band"].font_size
    dlg.close()
