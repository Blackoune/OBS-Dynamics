"""Catalogue des lecteurs : reconnaissance, stabilité des clés, logos.

La garantie qui compte ici : la clé d'un lecteur ne dépend pas de ce qui tourne
sur la machine. C'est elle qui figure dans l'URL de l'overlay, et une URL qui
changerait obligerait à refaire la source navigateur dans OBS.
"""
from __future__ import annotations

import pytest

import music_catalog

from music_catalog import BUILT_IN, MusicApp, by_key, identify, logo_path

#: Identifiants relevés sur une vraie machine, ou publiés par ces applications.
IDENTIFIANTS = {
    "spotify": ["SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "Spotify.exe"],
    "apple_music": ["AppleInc.AppleMusicWin_nzyj5cx40ttqa!App"],
    "itunes": ["iTunes.exe"],
    "deezer": ["Deezer.exe", "Deezer.Deezer_8wekyb3d8bbwe!Deezer"],
    "tidal": ["TIDAL.exe"],
    "amazon_music": ["AmazonMusic.exe", "Amazon.AmazonMusic_8wekyb3d8bbwe!App"],
    "soundcloud": ["SoundCloud.exe"],
    "youtube_music": ["YouTube Music.exe", "com.github.th-ch.youtube-music"],
}


# ----------------------------------------------------------------------------
# Contenu livré par défaut
# ----------------------------------------------------------------------------
def test_les_huit_services_demandes_sont_livres():
    attendus = {"spotify", "apple_music", "itunes", "deezer", "tidal",
                "amazon_music", "soundcloud", "youtube_music"}
    assert {app.key for app in BUILT_IN} == attendus


def test_chaque_entree_livree_a_un_libelle_une_couleur_et_un_motif():
    for app in BUILT_IN:
        assert app.label and app.color.startswith("#") and app.patterns
        assert app.built_in is True


def test_les_cles_livrees_sont_uniques():
    cles = [app.key for app in BUILT_IN]
    assert len(cles) == len(set(cles))


@pytest.mark.parametrize("cle", sorted(IDENTIFIANTS))
def test_chaque_service_se_reconnait_a_son_identifiant(cle):
    for app_id in IDENTIFIANTS[cle]:
        assert identify(app_id).key == cle, app_id


def test_itunes_et_apple_music_ne_se_confondent_pas():
    # Les deux viennent d'Apple et jouent de la musique ; les mélanger
    # afficherait le mauvais nom à l'antenne.
    assert identify("iTunes.exe").key == "itunes"
    assert identify("AppleInc.AppleMusicWin_nzyj5cx40ttqa!App").key == "apple_music"


# ----------------------------------------------------------------------------
# Stabilité des clés
# ----------------------------------------------------------------------------
def test_la_cle_dun_lecteur_ne_depend_pas_de_ce_qui_tourne():
    # Le cœur de la demande : fermer Spotify ne doit pas changer son lien. La
    # clé est une constante du catalogue, pas un effet de l'état courant.
    assert by_key("spotify") is not None
    assert by_key("spotify").key == "spotify"
    assert identify("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify").key == "spotify"


def test_une_source_hors_catalogue_garde_une_cle_stable():
    # Un navigateur n'est pas livré par défaut, mais tant qu'il joue son lien
    # ne doit pas changer d'une lecture à l'autre.
    assert identify("chrome.exe").key == identify("chrome.exe").key
    assert identify("chrome.exe").key == "chrome-exe"


def test_une_source_hors_catalogue_est_marquee_comme_telle():
    assert identify("un-lecteur-inconnu.exe").built_in is False


def test_une_cle_hors_catalogue_nest_pas_confondue_avec_une_livree():
    assert by_key(identify("chrome.exe").key) is None


def test_un_identifiant_sans_caractere_utile_donne_quand_meme_une_cle():
    # Une clé vide finirait dans une URL, où elle ne désignerait rien.
    assert identify("///").key == "source"


def test_une_cle_reste_utilisable_dans_une_url():
    for app_id in ("SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "chrome.exe",
                   "Un Lecteur Tres Long Avec Beaucoup Trop De Mots Dedans.exe"):
        cle = identify(app_id).key
        assert cle == cle.strip("-")
        assert " " not in cle and "!" not in cle and len(cle) <= 40


# ----------------------------------------------------------------------------
# Monogramme
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("libelle,attendu", [
    ("Spotify", "SP"),
    ("Apple Music", "AM"),
    ("YouTube Music", "YM"),
    ("Amazon Music", "AM"),
    ("iTunes", "IT"),
])
def test_le_monogramme_tient_en_deux_lettres(libelle, attendu):
    # Il remplace le logo tant qu'aucun fichier n'a été déposé : au-delà de
    # deux lettres il ne tiendrait plus dans la pastille.
    assert MusicApp("x", libelle, "#000000").monogram == attendu


def test_un_libelle_vide_ne_fait_pas_tomber_le_monogramme():
    assert MusicApp("x", "", "#000000").monogram == "?"


# ----------------------------------------------------------------------------
# Logos
# ----------------------------------------------------------------------------
def test_chaque_lecteur_livre_a_son_logo():
    """Le monogramme ne doit plus apparaître pour un lecteur du catalogue.

    Les fichiers sont produits par `tools/generer_logos_musique.py` ; les
    oublier ferait retomber l'application et l'overlay sur les initiales.
    """
    for app in BUILT_IN:
        chemin = logo_path(app.key)
        assert chemin is not None, app.key
        assert chemin.is_file() and chemin.stat().st_size > 0, app.key


def test_les_logos_livres_sont_des_png_transparents():
    # Posés sur la carte de l'onglet comme sur l'overlay : un fond opaque
    # laisserait un carré autour de chaque pastille.
    from PIL import Image

    for app in BUILT_IN:
        with Image.open(logo_path(app.key)) as image:
            assert image.format == "PNG", app.key
            assert image.mode == "RGBA", app.key
            assert image.getpixel((0, 0))[3] == 0, app.key


def test_une_source_hors_catalogue_na_pas_de_logo():
    # Un navigateur ou un lecteur non listé retombe sur son monogramme.
    assert logo_path("chrome-exe") is None


def test_un_logo_depose_est_trouve(tmp_path, monkeypatch):
    monkeypatch.setattr(music_catalog, "LOGOS_DIR", tmp_path)
    (tmp_path / "spotify.png").write_bytes(b"PNG-de-test")

    assert logo_path("spotify") == tmp_path / "spotify.png"
    assert logo_path("deezer") is None


def test_un_dossier_de_logos_absent_ne_fait_rien_tomber(tmp_path, monkeypatch):
    monkeypatch.setattr(music_catalog, "LOGOS_DIR", tmp_path / "jamais-cree")
    assert logo_path("spotify") is None


# ----------------------------------------------------------------------------
# Cles venues d une URL
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("cle", [
    "../../secret", "..\\..\\secret", "C:/Users/x/photo", "/etc/passwd",
    "spotify/../../x", "SPOTIFY", "", "a" * 41, "spo tify", "spotify.png",
])
def test_une_cle_hors_forme_ne_designe_aucun_fichier(cle, tmp_path, monkeypatch):
    """Regression : `?source=` finissait tel quel dans un chemin de fichier.

    `LOGOS_DIR / "C:/.../photo.png"` rend le chemin absolu : pathlib jette le
    dossier de base. La route servait alors n importe quel PNG du disque.
    """
    monkeypatch.setattr(music_catalog, "LOGOS_DIR", tmp_path / "logos")
    (tmp_path / "secret.png").write_bytes(b"PNG")

    assert logo_path(cle) is None


def test_un_png_hors_du_dossier_des_logos_reste_inaccessible(tmp_path, monkeypatch):
    # Le fichier EXISTE : avant le correctif, ces deux cles le designaient.
    monkeypatch.setattr(music_catalog, "LOGOS_DIR", tmp_path / "logos")
    (tmp_path / "logos").mkdir()
    (tmp_path / "secret.png").write_bytes(b"PNG")

    assert logo_path("../secret") is None
    assert logo_path((tmp_path / "secret").as_posix()) is None


@pytest.mark.parametrize("cle", [app.key for app in BUILT_IN]
                         + ["chrome-exe", "source", "vlc_2"])
def test_les_cles_legitimes_restent_valides(cle):
    assert music_catalog.is_valid_key(cle)


def test_toute_cle_fabriquee_pour_une_source_inconnue_est_valide():
    # Une cle refusee couperait le lien d overlay de cette source.
    for app_id in ("chrome.exe",
                   "Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic",
                   "!!!", "x" * 200, "Été à Noël.exe"):
        assert music_catalog.is_valid_key(identify(app_id).key), app_id
