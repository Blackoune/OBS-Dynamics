"""Banque de mots des sous-titres : correction des pseudos, jeux, emotes."""
from __future__ import annotations

import pytest

from subtitles_words import Corrector, Word, from_dict, load_words, save_words


def fix(words, text):
    return Corrector(words).apply(text)


BANK = [Word("Elden Ring", kind="game"), Word("KEKW", ("kékéwé", "k k w"), "emote"),
        Word("Blackoune", ("black oune",), "name"), Word("Pokémon", kind="game"),
        Word("Spider-Man", kind="game")]


@pytest.mark.parametrize("heard, expected", [
    ("On joue à Elden Rin ce soir.", "On joue à Elden Ring ce soir."),         # approché
    ("on joue à eldenring", "on joue à Elden Ring"),                          # collé
    ("je lance l'eldenring", "je lance l'Elden Ring"),                        # apostrophe gardée
    ("Merci black oune !", "Merci Blackoune !"),                              # variante listée
    ("C'est kékéwé, franchement.", "C'est KEKW, franchement."),
    ("j'adore pokemon", "j'adore Pokémon"),                                   # accents
    ("Spider Man 2 est sorti", "Spider-Man 2 est sorti"),
])
def test_bank_fixes_what_the_engine_mishears(heard, expected):
    assert fix(BANK, heard) == expected


@pytest.mark.parametrize("text", [
    "Les valeurs de ce soir sont bonnes.",        # « valeurs » ≠ Valorant
    "Il est très noir, le black.",                # mot courant proche d'un pseudo
    "On regarde le ring de boxe.",                # un seul mot d'une entrée à deux mots
    "",
])
def test_ordinary_words_are_never_replaced(text):
    bank = BANK + [Word("Valorant", kind="game")]
    assert fix(bank, text) == text


def test_review_cases():
    # Une ponctuation entre deux mots : deux phrases, pas une entrée.
    assert fix(BANK, "C'était elden. Ring de boxe") == "C'était elden. Ring de boxe"
    # Un rapprochement n'avale pas le mot suivant qui correspond exactement.
    bank = [Word("Eldenmaster"), Word("KEKW")]
    assert fix(bank, "eldenmastr kekw") == "Eldenmaster KEKW"
    # « _ » sépare les mots comme un espace.
    assert fix([Word("Elden Ring")], "elden_ring") == "Elden Ring"


def test_hostile_file_does_not_crash(tmp_path):
    path = tmp_path / "w.json"
    path.write_text("[" * 100_000, encoding="utf-8")
    assert load_words(path) == []


def test_short_names_need_an_exact_match():
    bank = [Word("Zed")]
    assert fix(bank, "zed est là") == "Zed est là"
    assert fix(bank, "zen est là") == "zen est là"


def test_empty_bank_is_a_no_op():
    assert not Corrector([])
    assert fix([], "Elden Rin") == "Elden Rin"


def test_validation_and_round_trip(tmp_path):
    raw = [{"text": "  Elden\nRing ", "heard": ["eldenrin", 3, "", "x" * 200], "kind": "game"},
           {"text": "", "kind": "game"}, {"text": "Ok", "kind": "virus"}, "junk",
           {"text": "\x00\x07"}]
    words = from_dict(raw)
    assert words == [Word("Elden Ring", ("eldenrin", "x" * 60), "game"), Word("Ok", (), "other")]
    path = tmp_path / "w.json"
    save_words(words, path)
    assert load_words(path) == words
    path.write_text("{oops", encoding="utf-8")
    assert load_words(path) == []
    assert load_words(tmp_path / "absent.json") == []


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


def test_dialog_edits_apply_live_then_save(racine):
    from ui_subtitles_words import WordBankDialog
    calls = []
    dlg = WordBankDialog(racine, [Word("Elden Ring", (), "game")],
                         on_change=lambda w, p: calls.append((w, p)))
    racine.update()
    dlg._add_empty()
    row = dlg._rows[-1]
    row.text.insert(0, "Blackoune")
    row.heard.insert(0, "black oune, blaque houne")
    dlg.changed()
    assert calls[-1] == ([Word("Elden Ring", (), "game"),
                          Word("Blackoune", ("black oune", "blaque houne"), "other")], False)
    dlg._trial.insert(0, "salut black oune")
    dlg._update_trial()
    assert "salut Blackoune" in dlg._result.cget("text")
    dlg.remove(dlg._rows[0])
    dlg.close()
    assert calls[-1] == ([Word("Blackoune", ("black oune", "blaque houne"), "other")], True)


def test_tab_corrects_text_before_showing_it(racine, tmp_path, monkeypatch):
    import subtitles_audio as audio
    import ui_subtitles
    monkeypatch.setattr(audio, "list_devices", lambda: [])
    (tmp_path / "words.json").write_text('[{"text": "Elden Ring", "kind": "game"}]',
                                         encoding="utf-8")
    sent = []

    class _Hub:
        enabled = True
        style = None

        def publish_text(self, kind, text, uid=-1):
            sent.append(text)

        def clear(self):
            pass

    view = ui_subtitles.SubtitlesView(racine, post_ui=lambda f: f(), hub=_Hub(),
                                      settings_path=tmp_path / "s.json",
                                      models_root=tmp_path / "models",
                                      words_path=tmp_path / "words.json")
    view._on_text("final", "On lance Elden Rin.", 1)
    assert sent == ["On lance Elden Ring."]
    assert view._box.get("1.0", "end-1c") == "On lance Elden Ring.\n"
    assert ui_subtitles.t("SUBS_BTN_WORDS", count=1) == view._words_btn.cget("text")


def test_large_bank_stays_fast():
    import time
    bank = [Word(f"Streamer{i}Pseudo") for i in range(300)]
    corrector = Corrector(bank)
    sentence = "Merci beaucoup pour le follow, ça me fait vraiment plaisir que vous soyez restés"
    t0 = time.perf_counter()
    for _ in range(10):
        corrector.apply(sentence)
    assert (time.perf_counter() - t0) / 10 < 0.05          # < 50 ms par phrase
