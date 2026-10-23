"""Sous-titres CC : découpe, préparation du texte, file d'envoi."""
from __future__ import annotations

import pytest

import subtitles_cc as cc
from subtitles_cc import CaptionQueue, CaptionSettings, prepare, wrap


def test_wrap_respects_width_and_lines():
    text = "Bonjour à tous et bienvenue sur le stream de ce soir, on va bien rigoler"
    chunks = wrap(text, 32, 2)
    for chunk in chunks:
        rows = chunk.split("\n")
        assert len(rows) <= 2 and all(len(r) <= 32 for r in rows)
    assert " ".join(" ".join(c.split("\n")) for c in chunks) == text


def test_wrap_cuts_a_word_longer_than_the_line():
    assert wrap("a " + "x" * 40, 32, 3) == ["a\n" + "x" * 32 + "\n" + "x" * 8]
    assert wrap("", 32, 2) == []


def test_lines_are_centred_on_the_32_columns():
    assert cc.center_lines("Salut") == " " * 13 + "Salut"
    chunk = cc.center_lines("x" * 32 + "\n" + "abcd")
    assert chunk == "x" * 32 + "\n" + " " * 14 + "abcd"
    sent = []
    q = CaptionQueue(sent.append, CaptionSettings(enabled=True))   # centré par défaut
    q.push("Bonjour")
    q.tick()
    assert sent == [" " * 12 + "Bonjour"]


def test_prepare_options():
    s = CaptionSettings()
    assert prepare("  C'est   la  FIN  ", s) == "C'est la FIN"
    assert prepare("Oh putain, c'est génial", s) == "Oh ***, c'est génial"
    assert prepare("Enculé !", s) == "*** !"
    assert prepare("Oh putain", CaptionSettings(filter_profanity=False)) == "Oh putain"
    assert prepare("Élodie a gagné", CaptionSettings(keep_accents=False)) == "Elodie a gagne"
    assert prepare("ça va", CaptionSettings(uppercase=True)) == "ÇA VA"
    # Un mot qui en CONTIENT un autre n'est pas masqué.
    assert prepare("Le shitake est bon", s) == "Le shitake est bon"


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _queue(**kw):
    sent, clock = [], _Clock()
    q = CaptionQueue(sent.append, CaptionSettings(enabled=True, center=False),
                     clock=clock, **kw)
    return q, sent, clock


def test_fast_mode_sends_finals_at_once_and_spaces_chunks():
    q, sent, clock = _queue()
    q.on_text("partial", "bon", 1)                 # jamais envoyé
    q.on_text("final", "Bonjour à tous et bienvenue sur le stream de ce soir, "
                       "on va bien rigoler ensemble, et en plus il y a un cadeau "
                       "pour les premiers abonnés", 1)
    q.tick()
    assert len(sent) == 1
    q.tick()
    assert len(sent) == 1                          # le premier morceau reste lisible
    clock.now += cc.SHOW_MAX_S
    q.tick()
    assert len(sent) == 2


def test_accurate_mode_waits_for_the_correction():
    q, sent, clock = _queue(wait_revise=True)
    q.on_text("final", "Elden Rin ce soir", 5)
    q.tick()
    assert sent == []
    q.on_text("revise", "Elden Ring ce soir", 5)
    q.tick()
    assert sent == ["Elden Ring ce soir"]
    q.on_text("revise", "Elden Ring, ce soir", 5)  # seconde correction : ignorée
    clock.now += 10
    q.tick()
    assert sent == ["Elden Ring ce soir"]


def test_accurate_mode_gives_up_waiting():
    q, sent, clock = _queue(wait_revise=True)
    q.on_text("final", "Phrase sans correction", 7)
    clock.now += cc.REVISE_WAIT_S + 0.1
    q.tick()
    assert sent == ["Phrase sans correction"]
    q.on_text("revise", "Phrase corrigée trop tard", 7)
    clock.now += 10
    q.tick()
    assert sent == ["Phrase sans correction"]


def test_backlog_speeds_up_instead_of_drifting():
    assert cc.show_time("x" * 200, backlog=0) == cc.SHOW_MAX_S
    assert cc.show_time("x" * 200, backlog=cc.BACKLOG_MAX + 1) == 1.0


def test_pending_sentences_leave_on_stop():
    q, sent, _clock = _queue(wait_revise=True)
    q.on_text("final", "Dernière phrase", 3)
    q.flush_pending()
    q.tick()
    assert sent == ["Dernière phrase"]


def test_clear_and_test_phrase():
    q, sent, _clock = _queue(wait_revise=True)
    q.on_text("final", "en attente", 1)
    q.clear()
    assert q.idle
    q.push("Phrase test")
    q.tick()
    assert sent == ["Phrase test"]


ctk = pytest.importorskip("customtkinter")


class _Done:
    """Future déjà résolue, comme celles de `AsyncLoopThread.run_coro`."""

    def __init__(self, value=None, error=None):
        self.value, self.error = value, error

    def result(self):
        if self.error:
            raise self.error
        return self.value

    def exception(self):
        return self.error

    def cancelled(self):
        return False

    def add_done_callback(self, fn):
        fn(self)


class _FakeOBS:
    def __init__(self, live):
        self.live, self.sent, self.is_connected = live, [], True

    async def stream_active(self):
        return self.live

    async def send_stream_caption(self, text):
        self.sent.append(text)


class _Loop:
    def run_coro(self, coro):
        try:
            coro.send(None)
        except StopIteration as stop:
            return _Done(stop.value)
        raise AssertionError("coroutine inattendue")


def _view(root, tmp_path, monkeypatch, obs):
    import subtitles_audio as audio
    import ui_subtitles
    monkeypatch.setattr(audio, "list_devices", lambda: [])
    cc.save_settings(CaptionSettings(enabled=True), tmp_path / "cc.json")
    return ui_subtitles.SubtitlesView(root, post_ui=lambda f: f(),
                                      settings_path=tmp_path / "s.json",
                                      models_root=tmp_path / "m",
                                      words_path=tmp_path / "w.json",
                                      cc_path=tmp_path / "cc.json",
                                      obs_client_getter=lambda: obs, obs_loop=_Loop())


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


def test_tab_sends_finished_sentences_to_obs_when_live(racine, tmp_path, monkeypatch):
    import ui_subtitles
    obs = _FakeOBS(live=True)
    view = _view(racine, tmp_path, monkeypatch, obs)       # sonde au démarrage
    assert view._cc_status.cget("text") == ui_subtitles.t("SUBS_CC_LIVE")
    view._on_text("partial", "Bonjour", 1)
    view._on_text("final", "Bonjour à tous.", 1)
    view._cc_queue.tick()
    assert [s.strip() for s in obs.sent] == ["Bonjour à tous."]


def test_tab_sends_nothing_when_obs_is_not_live(racine, tmp_path, monkeypatch):
    import ui_subtitles
    obs = _FakeOBS(live=False)
    view = _view(racine, tmp_path, monkeypatch, obs)
    assert view._cc_status.cget("text") == ui_subtitles.t("SUBS_CC_NOT_LIVE")
    view._on_text("final", "Personne ne voit ça.", 1)
    view._cc_tick()
    assert obs.sent == [] and view._cc_test_btn.cget("state") == "disabled"


def test_settings_round_trip_and_validation(tmp_path):
    path = tmp_path / "cc.json"
    assert cc.load_settings(path) == CaptionSettings()
    s = CaptionSettings(True, 24, 4, True, False, False, False)
    cc.save_settings(s, path)
    assert cc.load_settings(path) == s
    path.write_text('{"enabled": "yes", "width": 99, "lines": true, "uppercase": 1}',
                    encoding="utf-8")
    assert cc.load_settings(path) == CaptionSettings()
