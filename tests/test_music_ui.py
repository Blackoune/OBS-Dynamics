"""Onglet Musique et fenêtre Widget : ce qui doit rester fluide.

Deux gels mesurés à la main : l'onglet reconstruisait ses huit cartes à
chaque évènement SMTC (~200 ms), et le curseur d'opacité redessinait
l'aperçu et dix vignettes à chaque cran (~190 ms). Ces tests vérifient que
seul le nécessaire est refait.
"""
from __future__ import annotations

import time

import pytest

ctk = pytest.importorskip("customtkinter")

import ui_music                                   # noqa: E402
import ui_music_style                             # noqa: E402
from music_smtc import Session                    # noqa: E402
from music_style import StyleStore                # noqa: E402


class _SansSonde:
    """Remplace MusicWatcher : aucun appel à Windows pendant les tests."""

    def __init__(self, **_kw) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture
def racine():
    try:
        root = ctk.CTk()
    except Exception as exc:                       # pas de serveur graphique
        pytest.skip(f"pas d'affichage disponible : {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        root.destroy()


@pytest.fixture
def vue(racine, monkeypatch):
    monkeypatch.setattr(ui_music, "available", lambda: True)
    monkeypatch.setattr(ui_music, "MusicWatcher", _SansSonde)
    view = ui_music.MusicView(racine, post_ui=lambda f: f())
    view.pack()
    racine.update()
    return view


def _spotify(titre: str) -> Session:
    return Session(app_id="Spotify.exe", title=titre, status="PLAYING")


def _cartes(vue) -> dict:
    return {cle: carte for cle, (_s, carte) in vue._cartes.items()}


def test_un_evenement_identique_ne_reconstruit_aucune_carte(vue):
    vue._apply_sessions([_spotify("A")])
    avant = _cartes(vue)

    vue._apply_sessions([_spotify("A")])

    assert _cartes(vue) == avant


def test_seule_la_carte_qui_change_est_refaite(vue):
    vue._apply_sessions([_spotify("A")])
    avant = _cartes(vue)

    vue._apply_sessions([_spotify("B")])

    apres = _cartes(vue)
    assert apres["spotify"] is not avant["spotify"]
    assert not avant["spotify"].winfo_exists()
    autres = set(avant) - {"spotify"}
    assert autres and all(apres[cle] is avant[cle] for cle in autres)


def test_une_source_qui_disparait_perd_sa_carte(vue):
    vue._apply_sessions([Session(app_id="chrome.exe", title="x",
                                 status="PLAYING")])
    carte = vue._cartes["chrome-exe"][1]

    vue._apply_sessions([])

    assert "chrome-exe" not in vue._cartes
    assert not carte.winfo_exists()


def test_changer_de_langue_reconstruit_tout(vue):
    avant = _cartes(vue)

    vue.refresh_labels()

    assert all(vue._cartes[cle][1] is not carte for cle, carte in avant.items())


def test_le_curseur_ne_redessine_pas_les_vignettes_a_chaque_cran(racine,
                                                                 tmp_path):
    store = StyleStore(tmp_path / "w.json", tmp_path / "fonds")
    dialogue = ui_music_style.StyleDialog(racine, "spotify", "Spotify", store,
                                          lambda *_: None)
    racine.update()
    appels = []
    origine = dialogue._marquer_selection
    dialogue._marquer_selection = lambda: (appels.append(1), origine())

    for cran in range(30, 60):
        dialogue._set_opacity(cran)

    assert appels == []                    # rien pendant le glissé
    assert dialogue._style.opacity == 59
    fin = time.monotonic() + 2
    while not appels and time.monotonic() < fin:
        racine.update()
        time.sleep(0.02)
    assert appels == [1]                   # une seule fois, à l'arrêt
    dialogue.destroy()


def test_la_molette_sur_une_carte_fait_glisser_toute_la_liste(racine, monkeypatch):
    """Les cartes arrivent après la création de la liste, au fil des
    évènements SMTC : la molette doit quand même les faire défiler, en
    douceur, via le binding posé sur la fenêtre."""
    import time
    monkeypatch.setattr(ui_music, "available", lambda: True)
    monkeypatch.setattr(ui_music, "MusicWatcher", _SansSonde)
    racine.deiconify()
    racine.geometry("900x420")
    view = ui_music.MusicView(racine, post_ui=lambda f: f())
    view.pack(fill="both", expand=True)
    view._apply_sessions([_spotify("A")])
    racine.update()
    canvas = view._list._parent_canvas
    assert canvas.yview() != (0.0, 1.0), "liste trop courte pour défiler"

    carte = next(iter(_cartes(view).values()))
    carte.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
    deadline = time.monotonic() + 3
    while view._scroller.busy and time.monotonic() < deadline:
        racine.update()
        time.sleep(0.005)
    assert canvas.canvasy(0) > 0, "la molette sur une carte n'a rien fait défiler"


class _Hub:
    def __init__(self, styles):
        self.styles = styles

    def publish(self, *_a):
        pass

    def publish_style(self, *_a):
        pass


class _Overlay:
    def music_url(self, cle):
        return f"http://127.0.0.1:8765/music/{cle}?t=x"


def test_une_carte_complete_tient_en_peu_de_fenetres_tk(racine, monkeypatch, tmp_path):
    """Chaque fenêtre Tk se repeint seule pendant le défilement : une carte
    en comptait jusqu'à 45, d'où les rayures. Elle est désormais peinte dans
    un canvas, seul le champ du lien reste un vrai widget."""
    monkeypatch.setattr(ui_music, "available", lambda: True)
    monkeypatch.setattr(ui_music, "MusicWatcher", _SansSonde)
    view = ui_music.MusicView(racine, post_ui=lambda f: f(),
                              hub=_Hub(StyleStore(tmp_path / "w.json", tmp_path / "f")),
                              overlay=_Overlay())
    view.pack(fill="both", expand=True)
    view._apply_sessions([_spotify("A")])
    racine.update()

    def compte(widget):
        return 1 + sum(compte(enfant) for enfant in widget.winfo_children())

    for carte in _cartes(view).values():
        assert compte(carte) <= 3

    carte = _cartes(view)["spotify"]
    carte._copy_link()
    assert racine.clipboard_get() == "http://127.0.0.1:8765/music/spotify?t=x"
    assert carte.itemcget(carte._lien_txt, "text") == ui_music.t("MUSIC_BTN_LINK_COPIED")
