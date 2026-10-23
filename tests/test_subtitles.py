"""Sous-titres, lot 1 : modèles, reconnaissance en flux, session, onglet.

Aucun son réel ni aucun modèle réel : sherpa-onnx et la carte son sont
remplacés par des doubles. Ce qui est vérifié, c'est la logique autour.
"""
from __future__ import annotations

import hashlib
import io
import tarfile
import threading
import time

import numpy as np
import pytest

import subtitles_audio as audio
import subtitles_engine as engine


# -- reconnaissance -------------------------------------------------------- #

class _FakeRaw:
    """Imite sherpa_onnx.OnlineRecognizer : un texte et une fin de phrase
    imposés pour chaque bloc."""

    def __init__(self, script: list[tuple[str, bool]]) -> None:
        self._script = iter(script)
        self._now = ("", False)
        self.resets = 0

    def create_stream(self):
        return self

    def accept_waveform(self, _sr, _samples) -> None:
        self._now = next(self._script)

    def is_ready(self, _s) -> bool:
        return False

    def decode_stream(self, _s) -> None:
        pass

    def get_result(self, _s) -> str:
        return self._now[0]

    def is_endpoint(self, _s) -> bool:
        return self._now[1]

    def reset(self, _s) -> None:
        self.resets += 1


def _feed_all(rec, n):
    out = []
    for _ in range(n):
        out += rec.feed(np.zeros(audio.BLOCK, np.float32))
    return out


def test_partial_only_when_text_changes_then_final_on_pause():
    raw = _FakeRaw([("", False), ("bon", False), ("bon", False),
                    ("bonjour", False), ("bonjour", True), ("", False)])
    events = _feed_all(engine.Recognizer(raw), 6)
    assert events == [("partial", "bon"), ("partial", "bonjour"),
                      ("final", "bonjour")]
    assert raw.resets == 1


def test_pause_without_words_emits_nothing():
    raw = _FakeRaw([("", True), (".", False), (".", True)])
    assert _feed_all(engine.Recognizer(raw), 3) == []


def test_new_sentence_after_final_starts_fresh():
    # Même texte avant et après la remise à zéro : il doit ressortir.
    raw = _FakeRaw([("oui", False), ("oui", True), ("oui", False)])
    events = _feed_all(engine.Recognizer(raw), 3)
    assert events[-1] == ("partial", "oui")


# -- modèles --------------------------------------------------------------- #

def _archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _model_for(blob: bytes) -> engine.Model:
    return engine.Model("fr", "test-model", len(blob), hashlib.sha256(blob).hexdigest())


_GOOD = {f"test-model/{f}": f.encode() for f in
         ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")}


def test_download_verifies_and_extracts_only_model_files(tmp_path):
    blob = _archive(_GOOD | {"test-model/test_wavs/0.wav": b"x",
                             "../../evil.py": b"boom"})
    model = _model_for(blob)
    seen = []
    engine.download(model, tmp_path, lambda d, t: seen.append((d, t)),
                    chunks=lambda _url: [blob[:10], blob[10:]])
    folder = engine.model_dir(model, tmp_path)
    assert engine.is_installed(model, tmp_path)
    assert sorted(p.name for p in folder.iterdir()) == sorted(engine._MODEL_FILES)
    assert not (tmp_path.parent / "evil.py").exists()
    assert seen[-1] == (len(blob), len(blob))
    assert not list(tmp_path.glob("*.part"))


def test_download_refuses_wrong_checksum(tmp_path):
    blob = _archive(_GOOD)
    model = engine.Model("fr", "test-model", len(blob), "0" * 64)
    with pytest.raises(ValueError):
        engine.download(model, tmp_path, chunks=lambda _url: [blob])
    assert not engine.is_installed(model, tmp_path)
    assert not list(tmp_path.glob("*.part"))


def test_download_refuses_incomplete_archive(tmp_path):
    blob = _archive({"test-model/encoder.onnx": b"e"})
    model = _model_for(blob)
    with pytest.raises(ValueError):
        engine.download(model, tmp_path, chunks=lambda _url: [blob])
    assert not engine.is_installed(model, tmp_path)


def test_download_can_be_cancelled(tmp_path):
    blob = _archive(_GOOD)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(engine.DownloadCancelled):
        engine.download(_model_for(blob), tmp_path, cancel=cancel,
                        chunks=lambda _url: [blob])
    assert not list(tmp_path.glob("*.part"))


def test_catalog_checksums_are_well_formed():
    for model in engine.MODELS.values():
        assert len(model.sha256) == 64 and int(model.sha256, 16) >= 0
        assert model.name.startswith("sherpa-onnx-streaming-")


# -- session --------------------------------------------------------------- #

class _Rec:
    def reset(self):
        pass

    def feed(self, block):
        return [("partial", "salut")] if block[0] > 0 else []


def test_transcriber_waits_for_device_then_resumes():
    dev = audio.Device("Casque", audio.KIND_LOOPBACK, "id-1")
    opened = []
    texts, states = [], []
    done = threading.Event()

    def blocks(device):
        opened.append(device.id)
        if len(opened) == 1:
            raise OSError("unplugged")
        yield np.ones(audio.BLOCK, np.float32)
        done.set()
        while True:
            yield np.zeros(audio.BLOCK, np.float32)

    tr = engine.Transcriber(dev, engine.MODELS_DIR, on_text=lambda k, t, _u: texts.append((k, t)),
                            on_state=lambda s, _d: states.append(s),
                            load=lambda _f: _Rec(), blocks=blocks,
                            devices=lambda: [audio.Device("Casque", audio.KIND_LOOPBACK, "id-2")])
    tr.RETRY_S = 0.01
    tr.start()
    assert done.wait(2)
    tr.stop()
    assert opened == ["id-1", "id-2"]          # nouvel id après rebranchement
    assert texts == [("partial", "salut")]
    assert states[:4] == [engine.ST_LOADING, engine.ST_LISTENING,
                          engine.ST_WAITING_DEVICE, engine.ST_LISTENING]
    assert states[-1] == engine.ST_STOPPED


def test_transcriber_reports_model_load_error():
    states = []

    def load(_f):
        raise RuntimeError("bad model")

    tr = engine.Transcriber(audio.Device("x", audio.KIND_MIC), engine.MODELS_DIR,
                            on_text=lambda *_: None,
                            on_state=lambda s, d: states.append((s, d)), load=load)
    tr.start()
    deadline = time.time() + 2
    while tr.running and time.time() < deadline:
        time.sleep(0.01)
    assert states[-1][0] == engine.ST_ERROR and "bad model" in states[-1][1]


# -- réglages et niveau ---------------------------------------------------- #

def test_final_keeps_the_sentence_audio_for_the_second_pass():
    raw = _FakeRaw([("", False)] * 30 + [("bon", False), ("bonjour", True)])
    rec = engine.Recognizer(raw)
    _feed_all(rec, 32)
    # 2 s de silence d'avance au plus, plus les deux blocs parlés.
    assert len(rec.last_utterance) == 22 * audio.BLOCK


def test_trim_silence_keeps_speech_and_a_margin():
    sr = audio.SAMPLE_RATE
    speech = np.full(sr, 0.2, np.float32)
    noisy = np.concatenate([np.full(2 * sr, 1e-4, np.float32), speech,
                            np.full(sr + sr // 2, 1e-4, np.float32)])
    out = engine.trim_silence(noisy)
    assert len(out) == sr + 2 * int(0.3 * sr)          # parole + 0,3 s de chaque côté
    assert np.all(out[int(0.3 * sr):int(0.3 * sr) + sr] == 0.2)
    assert len(engine.trim_silence(np.zeros(sr, np.float32))) == sr    # rien à couper


def test_next_sentence_start_is_split_off():
    """Régression : la phrase suivante commençait avant que la précédente soit
    figée ; son début restait collé à la fin, et le second passage tronquait
    « s'inscriront » en « s'inscrir »."""
    sr = audio.SAMPLE_RATE
    loud = lambda s: np.full(int(s * sr), 0.2, np.float32)      # noqa: E731
    quiet = lambda s: np.full(int(s * sr), 1e-4, np.float32)    # noqa: E731
    head, tail = engine.split_next_sentence(
        np.concatenate([loud(2), quiet(0.2), loud(1), quiet(0.9), loud(0.3)]), gap_s=0.8)
    assert abs(len(head) / sr - (3.2 + 0.4)) < 0.02             # pause courte gardée
    assert abs(len(tail) / sr - (0.5 + 0.3)) < 0.02
    head, tail = engine.split_next_sentence(np.concatenate([loud(2), quiet(1)]))
    assert tail.size == 0 and len(head) == 3 * sr
    # Cas réel : 2 s de silence avant la reprise ; seule la parole compte
    # dans la limite de 1,5 s.
    head, tail = engine.split_next_sentence(
        np.concatenate([loud(7), quiet(2), loud(0.3)]), gap_s=0.6)
    assert abs(len(head) / sr - 7.3) < 0.02 and tail.size


def test_breath_inside_a_sentence_is_never_a_cut():
    """Régression (relecture) : une respiration de 0,5 s au milieu d'une
    phrase coupait la phrase en deux, et sa fin passait à la phrase suivante."""
    sr = audio.SAMPLE_RATE
    loud = lambda s: np.full(int(s * sr), 0.2, np.float32)      # noqa: E731
    quiet = lambda s: np.full(int(s * sr), 1e-4, np.float32)    # noqa: E731
    sentence = np.concatenate([loud(1.5), quiet(0.5), loud(1), quiet(0.9)])
    assert engine.split_next_sentence(sentence, gap_s=0.8)[1].size == 0
    # Plus de 1,5 s de parole après la pause : c'est la phrase elle-même.
    long_tail = np.concatenate([loud(1), quiet(1), loud(2)])
    assert engine.split_next_sentence(long_tail, gap_s=0.8)[1].size == 0


def test_sentence_numbers_never_repeat_across_sessions():
    """Régression (relecture) : après Arrêter puis Démarrer, la numérotation
    repartait de 0 et une correction pouvait remplacer une ligne de la
    session précédente."""
    dev = audio.Device("m", audio.KIND_MIC)
    a = engine.Transcriber(dev, engine.MODELS_DIR, on_text=lambda *_: None)
    b = engine.Transcriber(dev, engine.MODELS_DIR, on_text=lambda *_: None)
    assert a._uid != b._uid


class _InstantRefiner:
    def __init__(self, on_revise):
        self.on_revise, self.closed = on_revise, False

    def submit(self, uid, samples):
        self.on_revise(uid, f"corrigé {uid} ({len(samples)})")

    def close(self):
        self.closed = True


def test_transcriber_sends_each_final_to_the_second_pass():
    texts, done = [], threading.Event()
    refiners = []

    class _TwoSentences:
        def __init__(self):
            self.n = 0
            self.last_utterance = np.ones(5, np.float32)

        def reset(self):
            pass

        def feed(self, _block):
            self.n += 1
            return [("final", f"phrase {self.n}")] if self.n <= 2 else []

    def blocks(_device):
        for _ in range(3):
            yield np.zeros(audio.BLOCK, np.float32)
        done.set()
        while True:
            yield np.zeros(audio.BLOCK, np.float32)

    def refine(cb):
        refiners.append(_InstantRefiner(cb))
        return refiners[-1]

    tr = engine.Transcriber(audio.Device("m", audio.KIND_MIC), engine.MODELS_DIR,
                            on_text=lambda k, t, u: texts.append((k, t, u)),
                            load=lambda _f: _TwoSentences(), refine=refine, blocks=blocks)
    first = tr._uid
    tr.start()
    assert done.wait(2)
    tr.stop()
    second = texts[2][2]
    assert second > first
    assert texts == [("final", "phrase 1", first), ("revise", f"corrigé {first} (5)", first),
                     ("final", "phrase 2", second), ("revise", f"corrigé {second} (5)", second)]
    assert refiners[0].closed


def test_settings_round_trip_and_validation(tmp_path):
    path = tmp_path / "subtitles.json"
    assert engine.load_settings(path) == engine.Settings()
    s = engine.Settings("Micro USB", audio.KIND_MIC, "en", engine.PRECISION_FAST, 0.5)
    engine.save_settings(s, path)
    assert engine.load_settings(path) == s
    path.write_text('{"precision": "turbo", "end_pause": 9, "lang": "fr"}', encoding="utf-8")
    assert engine.load_settings(path) == engine.Settings()
    path.write_text('{"device_name": 3, "device_kind": "x", "lang": "zz"}', encoding="utf-8")
    assert engine.load_settings(path) == engine.Settings()
    # Valeurs non hachables : `in MODELS` levait TypeError et empêchait
    # l'application entière de démarrer.
    path.write_text('{"lang": ["fr"], "device_kind": {}}', encoding="utf-8")
    assert engine.load_settings(path) == engine.Settings()
    path.write_text("not json", encoding="utf-8")
    assert engine.load_settings(path) == engine.Settings()


def test_level_bounds():
    assert audio.level(np.zeros(0, np.float32)) == 0.0
    assert audio.level(np.zeros(100, np.float32)) == 0.0
    assert audio.level(np.ones(100, np.float32)) == 1.0


def test_find_device_matches_name_and_kind():
    devs = [audio.Device("A", audio.KIND_MIC, "1"), audio.Device("A", audio.KIND_LOOPBACK, "2")]
    assert audio.find_device(devs, "A", audio.KIND_LOOPBACK).id == "2"
    assert audio.find_device(devs, "B", audio.KIND_MIC) is None


# -- onglet ---------------------------------------------------------------- #

ctk = pytest.importorskip("customtkinter")


@pytest.fixture
def vue(tmp_path, monkeypatch):
    import ui_subtitles
    try:
        root = ctk.CTk()
    except Exception as exc:
        pytest.skip(f"pas d'affichage disponible : {exc}")
    root.withdraw()
    monkeypatch.setattr(audio, "list_devices", lambda: [
        audio.Device("Micro USB", audio.KIND_MIC, "m"),
        audio.Device("Micro casque", audio.KIND_MIC, "c"),
        audio.Device("Écran", audio.KIND_LOOPBACK, "e")])
    monkeypatch.setattr(audio, "default_input_name", lambda: "Micro casque")
    view = ui_subtitles.SubtitlesView(root, post_ui=lambda f: f(),
                                      settings_path=tmp_path / "s.json",
                                      models_root=tmp_path / "models")
    view.pack()
    root.update()
    try:
        yield view
    finally:
        root.destroy()


def test_view_defaults_to_windows_microphone_and_needs_model(vue, tmp_path):
    """Régression : le premier choix était une sortie son (l'écran), donc
    rien de ce que disait le streamer n'était transcrit."""
    saved = engine.load_settings(tmp_path / "s.json")
    assert (saved.device_name, saved.device_kind) == ("Micro casque", audio.KIND_MIC)
    assert vue._start_btn.cget("state") == "disabled"       # modèle absent
    assert vue._download_btn.cget("state") == "normal"


def test_silent_source_shows_a_warning(vue, monkeypatch):
    import ui_subtitles
    vue._transcriber = object()                     # session en cours
    vue._on_state(engine.ST_LISTENING, "")
    vue._on_tick(0.0, 1.0)
    assert vue._state_lbl.cget("text") == ui_subtitles.t("SUBS_ST_LISTENING")
    vue._last_sound -= ui_subtitles.SILENCE_WARN_S + 1
    vue._on_tick(0.0, 1.0)
    assert vue._state_lbl.cget("text") == ui_subtitles.t("SUBS_NO_SIGNAL")
    vue._on_tick(0.3, 1.0)                          # le son revient
    assert vue._state_lbl.cget("text") == ui_subtitles.t("SUBS_ST_LISTENING")
    vue._transcriber = None


def test_late_message_from_stopped_session_is_ignored(vue, monkeypatch):
    """Arrêter puis relancer : le « stopped » en retard de l'ancienne session
    ne doit pas détacher la nouvelle de l'interface."""
    sessions = []

    class _Tr:
        def __init__(self, *_a, **kw):
            self.kw = kw
            sessions.append(self)

        def start(self):
            pass

        def stop(self, timeout=0):
            pass

    queued = []
    vue._post_ui = queued.append
    monkeypatch.setattr(engine, "Transcriber", _Tr)
    monkeypatch.setattr(engine, "is_installed", lambda *_a: True)
    vue._toggle_run()
    old = sessions[0]
    vue._toggle_run()                      # arrêt
    vue._toggle_run()                      # relance
    old.kw["on_state"](engine.ST_STOPPED, "")
    old.kw["on_text"]("final", "vieux texte", 0)
    for job in queued:
        job()
    assert vue._transcriber is sessions[1]
    assert "vieux texte" not in vue._box.get("1.0", "end")


def test_view_replaces_a_sentence_in_place_when_corrected(vue):
    vue._on_text("final", "Elden Rin ce soir.", 0)
    vue._on_text("final", "Deuxième phrase.", 1)
    vue._on_text("partial", "en cours", 2)
    vue._on_text("revise", "Elden Ring ce soir.", 0)
    assert vue._box.get("1.0", "end-1c") == "Elden Ring ce soir.\nDeuxième phrase.\nen cours"
    vue._on_text("revise", "Rien.", 42)                  # phrase inconnue : ignorée
    assert vue._box.get("1.0", "end-1c").startswith("Elden Ring")


def test_precision_and_pause_are_saved(vue, tmp_path):
    import ui_subtitles
    vue._on_precision_chosen(ui_subtitles.t("SUBS_PREC_FAST"))
    vue._on_pause_moved(0.47)
    saved = engine.load_settings(tmp_path / "s.json")
    assert (saved.precision, saved.end_pause) == (engine.PRECISION_FAST, 0.5)


def test_view_shows_partial_then_replaces_it_with_final(vue):
    vue._on_text("partial", "bon")
    vue._on_text("partial", "bonjour à")
    assert vue._box.get("1.0", "end-1c") == "bonjour à"
    vue._on_text("final", "Bonjour à tous.")
    vue._on_text("partial", "ça")
    assert vue._box.get("1.0", "end-1c") == "Bonjour à tous.\nça"
    assert "partial" in vue._box.tag_names("2.0")
    assert "final" in vue._box.tag_names("1.0")
