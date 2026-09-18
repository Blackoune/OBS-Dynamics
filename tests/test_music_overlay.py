"""Hub du widget musique : jeton, publication, abonnés, page.

Le hub est la seule pièce que le serveur HTTP connaît de la musique. Ce qui est
vérifié ici, c'est le contrat qu'il lui offre : un jeton stable, un dernier
état relisible, et un flux qui ne perd jamais l'état le plus récent.
"""
from __future__ import annotations

import json
import queue
import re

import pytest

from music_catalog import friendly_label, identify
from music_overlay import MusicHub, page_version
from music_smtc import Session

#: Identifiant SMTC réel de Spotify, et la clé de catalogue qu'il donne.
SPOTIFY = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
CLE = "spotify"


@pytest.fixture
def hub(tmp_path) -> MusicHub:
    instance = MusicHub(path=tmp_path / "music_widget.json")
    instance.ensure_token()
    return instance


def _session(app_id=SPOTIFY, title="Love Me", artist="JMSN", status="PLAYING",
             cover=b"PNGDATA", size=(300, 300)) -> Session:
    return Session(app_id=app_id, title=title, artist=artist, album="Soft Spot",
                   status=status, thumbnail=cover, thumbnail_size=size,
                   thumbnail_format="PNG")


# ----------------------------------------------------------------------------
# Nom d'application
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("app_id,attendu", [
    ("chrome.exe", "chrome"),
    ("msedge.exe", "msedge"),
    ("Chrome", "Chrome"),
])
def test_le_nom_dune_source_hors_catalogue_est_nettoye(app_id, attendu):
    # Ce nom s'affiche à côté d'un nom d'artiste : le « .exe » n'y a pas sa
    # place. Les lecteurs connus, eux, portent le libellé du catalogue.
    assert friendly_label(app_id) == attendu


def test_un_identifiant_degenere_ne_donne_pas_un_nom_vide():
    assert friendly_label("!") == "!"


def test_le_libelle_dun_lecteur_connu_vient_du_catalogue():
    assert identify(SPOTIFY).label == "Spotify"


# ----------------------------------------------------------------------------
# Jeton
# ----------------------------------------------------------------------------
def test_le_jeton_survit_au_redemarrage(tmp_path):
    # C'est ce qui rend le lien permanent : un jeton régénéré à chaque
    # démarrage casserait la source navigateur déjà collée dans OBS.
    chemin = tmp_path / "music_widget.json"
    premier = MusicHub(path=chemin).ensure_token()

    second = MusicHub(path=chemin).ensure_token()

    assert premier and premier == second
    assert json.loads(chemin.read_text(encoding="utf-8"))["overlay_token"] == premier


def test_un_jeton_faux_ou_vide_est_refuse(hub):
    assert hub.token_matches(hub.ensure_token()) is True
    assert hub.token_matches("autre-chose") is False
    assert hub.token_matches("") is False


def test_un_hub_sans_jeton_refuse_tout(tmp_path):
    # Personne n'a encore appelé ensure_token : rien ne doit passer.
    assert MusicHub(path=tmp_path / "x.json").token_matches("") is False


def test_un_fichier_de_jeton_illisible_nempeche_pas_le_demarrage(tmp_path):
    chemin = tmp_path / "music_widget.json"
    chemin.write_text("ceci n'est pas du json", encoding="utf-8")

    assert MusicHub(path=chemin).ensure_token()


# ----------------------------------------------------------------------------
# Publication
# ----------------------------------------------------------------------------
def test_le_dernier_etat_est_relisible(hub):
    hub.publish([_session()])

    etat = hub.snapshot(CLE)

    assert etat["title"] == "Love Me"
    assert etat["artist"] == "JMSN"
    assert etat["app"] == "Spotify"
    assert etat["playing"] is True
    assert etat["square"] is True
    assert etat["cover"]


def test_un_abonne_recoit_les_changements(hub):
    sub = hub.subscribe(CLE)

    hub.publish([_session(title="Love Me")])
    hub.publish([_session(title="Un autre morceau")])

    assert json.loads(sub.get_nowait())["title"] == "Love Me"
    assert json.loads(sub.get_nowait())["title"] == "Un autre morceau"


def test_un_abonne_ne_recoit_que_sa_source(hub):
    sub = hub.subscribe(CLE)

    hub.publish([_session(app_id="chrome.exe")])

    with pytest.raises(queue.Empty):
        sub.get_nowait()


def test_une_source_qui_disparait_previent_son_overlay(hub):
    # Sans cet événement, l'overlay resterait figé sur un morceau qui ne joue
    # plus : pire qu'un overlay vide, puisque rien à l'écran ne le signale.
    sub = hub.subscribe(CLE)
    hub.publish([_session()])
    sub.get_nowait()

    hub.publish([])

    assert json.loads(sub.get_nowait()) == {"gone": True}
    assert hub.snapshot(CLE) is None
    assert hub.cover(CLE) is None


def test_une_source_absente_na_ni_etat_ni_pochette(hub):
    assert hub.snapshot("jamais-vu") is None
    assert hub.cover("jamais-vu") is None


def test_un_desabonnement_arrete_la_livraison(hub):
    sub = hub.subscribe(CLE)
    hub.unsubscribe(CLE, sub)

    hub.publish([_session()])

    assert hub.listener_count(CLE) == 0
    with pytest.raises(queue.Empty):
        sub.get_nowait()


# ----------------------------------------------------------------------------
# Pochette
# ----------------------------------------------------------------------------
def test_la_pochette_est_servie_telle_quelle(hub):
    hub.publish([_session(cover=b"des-octets-bruts")])
    assert hub.cover(CLE) == b"des-octets-bruts"


def test_lempreinte_change_avec_la_pochette(hub):
    # L'empreinte fait partie de l'URL de l'image : si elle ne bougeait pas, le
    # navigateur garderait la pochette du morceau précédent en cache.
    hub.publish([_session(cover=b"premiere")])
    avant = hub.snapshot(CLE)["cover"]

    hub.publish([_session(cover=b"seconde")])

    assert hub.snapshot(CLE)["cover"] != avant


def test_une_session_sans_pochette_a_une_empreinte_vide(hub):
    hub.publish([_session(cover=None, size=None)])
    assert hub.snapshot(CLE)["cover"] == ""


def test_une_video_est_signalee_comme_non_carree(hub):
    # C'est ce drapeau qui décide, dans la page, entre le vinyle qui tourne et
    # le rectangle posé à plat.
    hub.publish([_session(app_id="chrome.exe", size=(1280, 720))])
    assert hub.snapshot("chrome-exe")["square"] is False


# ----------------------------------------------------------------------------
# File saturée
# ----------------------------------------------------------------------------
def test_un_overlay_lent_perd_les_etats_intermediaires_jamais_le_dernier(hub):
    # Un overlay figé sur un morceau périmé serait faux à l'écran ; en retard
    # d'un état intermédiaire, il ne l'est pas.
    sub = hub.subscribe(CLE)
    for index in range(40):
        hub.publish([_session(title=f"Morceau {index}")])

    recus = []
    while True:
        try:
            recus.append(json.loads(sub.get_nowait())["title"])
        except queue.Empty:
            break

    assert recus[-1] == "Morceau 39"
    assert len(recus) < 40


# ----------------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------------
def test_la_page_porte_son_jeton_et_sa_source(hub):
    token = hub.ensure_token()

    html = hub.page_html(token, SPOTIFY)

    assert token in html
    assert "musicevents" in html
    assert "/musiccover/" in html
    # L'identifiant part par json.dumps : le « ! » et les points ne peuvent
    # pas casser le script.
    assert json.dumps(SPOTIFY) in html


def test_le_libelle_dattente_est_echappe(hub):
    html = hub.page_html("jeton", "chrome.exe", wait_text='<script>"x"')

    assert '<script>"x"' not in html
    assert "&lt;script&gt;" in html


def test_la_carte_est_centree_dans_la_source(hub):
    """Une source plus grande que la carte laisse du vide AUTOUR d elle.

    La carte etait calee en haut a gauche : qui saisissait une taille
    approximative retrouvait son widget colle dans un coin de la scene.
    """
    html = hub.page_html("jeton", SPOTIFY)

    # `^\s*body\{` et pas `body\{` : la page ouvre sur une regle
    # `html,body{...}` qui ne porte que les marges.
    corps = re.search(r"^\s*body\{([^}]*)\}", html, re.M)
    assert corps is not None, "aucune regle body dans la page"
    regle = corps.group(1)
    assert "display:flex" in regle
    assert "align-items:center" in regle
    assert "justify-content:center" in regle


def test_la_page_et_ses_messages_portent_la_meme_empreinte(hub):
    """Une source ouverte avant une mise a jour doit pouvoir s en rendre compte.

    Elle garde l ancien script et continue de recevoir les evenements : sans
    ce reperage, elle afficherait les nouveaux reglages avec les moyens de
    l ancienne page — un modele sans ses pastilles ni sa progression.
    """
    empreinte = page_version()
    assert empreinte in hub.page_html("jeton", SPOTIFY)
    assert hub.initial_payload("spotify")["page"] == empreinte

    hub.publish([_session()])
    assert hub.initial_payload("spotify")["page"] == empreinte


def test_lempreinte_change_avec_la_page(monkeypatch):
    import music_overlay

    monkeypatch.setattr(music_overlay, "_PAGE_VERSION", None)
    monkeypatch.setattr(music_overlay, "_PAGE_HTML",
                        music_overlay._PAGE_HTML + "<!-- x -->")
    apres = music_overlay.page_version()

    monkeypatch.undo()
    assert apres != page_version()
