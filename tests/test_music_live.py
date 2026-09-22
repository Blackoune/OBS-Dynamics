"""Chaîne complète : enregistrer un réglage atteint l'overlay déjà ouvert.

Les autres fichiers testent les pièces séparément. Ici on monte un vrai
serveur, on s'y connecte comme le ferait une source navigateur d'OBS, et on
vérifie les deux promesses faites à l'utilisateur :

- le lien d'une source ne change JAMAIS, quoi qu'il arrive aux réglages ;
- une modification enregistrée arrive sur la page déjà connectée, sans
  recharger la source et sans attendre le morceau suivant.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from music_overlay import MusicHub
from music_smtc import Session
from music_style import Style
from overlay_server import OverlayServer

SPOTIFY_ID = "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
CLE = "spotify"


@pytest.fixture
def monte(tmp_path):
    """Un hub et un serveur sur un port libre, arrêtés à la fin du test."""
    hub = MusicHub(path=tmp_path / "music_widget.json",
                   backgrounds=tmp_path / "fonds")
    hub.ensure_token()
    serveur = OverlayServer(lambda _id: None, port=0, music_hub=hub)
    assert serveur.start(), "le serveur overlay n'a pas demarre"
    try:
        yield hub, serveur
    finally:
        serveur.stop()


#: Delai de lecture du flux. Court VOLONTAIREMENT : une socket bloquee dans
#: un `recv` ne se reveille pas sous Windows, ni en fermant le fichier, ni en
#: appelant `shutdown` dessus. Le seul levier est l echeance elle-meme, et
#: chaque test payait sinon les dix secondes du delai a sa derniere ligne.
LECTURE_S = 1.0


class _Overlay:
    """Une source navigateur : lit le flux SSE dans un thread, comme OBS."""

    def __init__(self, url: str) -> None:
        self.recus: list[dict] = []
        self._stop = threading.Event()
        self._flux = urllib.request.urlopen(url, timeout=LECTURE_S)
        self._thread = threading.Thread(target=self._lire, daemon=True)
        self._thread.start()

    def _lire(self) -> None:
        while not self._stop.is_set():
            try:
                ligne = self._flux.readline()
            except TimeoutError:
                continue          # rien a lire pour l instant : on repasse
            except Exception:
                return            # flux ferme : sortie normale
            if not ligne:
                return
            if ligne.startswith(b"data: "):
                self.recus.append(json.loads(ligne[6:]))

    def attendre(self, combien: int, delai: float = 6.0) -> None:
        """Attend d'avoir reçu `combien` messages, sinon échoue."""
        horloge = threading.Event()
        for _ in range(int(delai * 20)):
            if len(self.recus) >= combien:
                return
            horloge.wait(0.05)
        raise AssertionError(f"{len(self.recus)} message(s) recu(s), "
                             f"{combien} attendu(s) : {self.recus}")

    def fermer(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        try:
            self._flux.close()
        except Exception:
            pass


def _url_evenements(serveur: OverlayServer, hub: MusicHub, cle: str) -> str:
    return (f"{serveur.base_url()}/musicevents/{hub.ensure_token()}"
            f"?source={urllib.parse.quote(cle, safe='')}")


def _session() -> Session:
    return Session(app_id=SPOTIFY_ID, title="Love Me", artist="JMSN",
                   status="PLAYING", thumbnail=b"PNG", thumbnail_size=(300, 300))


# ----------------------------------------------------------------------------
# Le lien ne change jamais
# ----------------------------------------------------------------------------
def test_le_lien_ne_change_pas_quand_on_enregistre_un_reglage(monte):
    hub, serveur = monte
    avant = serveur.music_url(CLE)

    hub.styles.save(CLE, Style(bg="#123456", opacity=30, layout="blocs"))
    hub.styles.save(CLE, Style(bg="#654321", cover="aucune"))

    assert serveur.music_url(CLE) == avant


def test_le_lien_ne_change_pas_selon_ce_qui_joue(monte):
    hub, serveur = monte
    eteint = serveur.music_url(CLE)

    hub.publish([_session()])
    en_lecture = serveur.music_url(CLE)
    hub.publish([])

    assert en_lecture == eteint == serveur.music_url(CLE)


def test_le_lien_survit_a_un_redemarrage(monte, tmp_path):
    """Le port peut changer, pas le jeton ni la clé.

    C'est ce qui compte pour OBS : la source collée une fois doit continuer de
    répondre, exécution après exécution.
    """
    hub, serveur = monte
    premier = serveur.music_url(CLE)

    second = MusicHub(path=tmp_path / "music_widget.json",
                      backgrounds=tmp_path / "fonds")
    autre = OverlayServer(lambda _id: None, port=0, music_hub=second)
    assert autre.start()
    try:
        assert second.ensure_token() == hub.ensure_token()
        assert (autre.music_url(CLE).split("/music/")[1]
                == premier.split("/music/")[1])
    finally:
        autre.stop()


# ----------------------------------------------------------------------------
# Enregistrer atteint OBS tout de suite
# ----------------------------------------------------------------------------
def test_un_overlay_apprend_son_style_des_la_connexion(monte):
    """Régression : sans lecture en cours, l'historique était vide.

    Une source posée dans OBS avant de lancer la musique restait alors sur
    l'habillage par défaut, et on croyait ses réglages perdus.
    """
    hub, serveur = monte
    hub.styles.save(CLE, Style(bg="#123456", border="#ABCDEF", opacity=44))

    overlay = _Overlay(_url_evenements(serveur, hub, CLE))
    try:
        overlay.attendre(1)
    finally:
        overlay.fermer()

    style = overlay.recus[0]["style"]
    assert style["bg"] == "#123456"
    assert style["border"] == "#ABCDEF"
    assert style["opacity"] == 44


def test_enregistrer_pousse_le_changement_sur_un_overlay_ouvert(monte):
    hub, serveur = monte
    overlay = _Overlay(_url_evenements(serveur, hub, CLE))
    try:
        overlay.attendre(1)

        hub.styles.save(CLE, Style(bg="#FF0000", layout="blocs",
                                   cover="aucune", show_wave=False))
        hub.publish_style(CLE)

        overlay.attendre(2)
    finally:
        overlay.fermer()

    style = overlay.recus[-1]["style"]
    assert style["bg"] == "#FF0000"
    assert style["layout"] == "blocs"
    assert style["cover"] == "aucune"
    assert style["show_wave"] is False


def test_le_changement_arrive_meme_sans_lecture_en_cours(monte):
    # L'utilisateur règle son overlay AVANT de lancer la musique : le message
    # doit partir quand même, avec le drapeau qui dit qu'il ne porte pas de
    # morceau.
    hub, serveur = monte
    overlay = _Overlay(_url_evenements(serveur, hub, CLE))
    try:
        overlay.attendre(1)

        hub.styles.save(CLE, Style(bg="#0A0A0A"))
        hub.publish_style(CLE)

        overlay.attendre(2)
    finally:
        overlay.fermer()

    dernier = overlay.recus[-1]
    assert dernier["style"]["bg"] == "#0A0A0A"
    assert dernier.get("style_only") is True


def test_le_changement_garde_le_morceau_affiche(monte):
    # Régler une couleur pendant une lecture ne doit pas faire disparaître le
    # titre de l'écran le temps du réglage.
    hub, serveur = monte
    hub.publish([_session()])
    overlay = _Overlay(_url_evenements(serveur, hub, CLE))
    try:
        overlay.attendre(1)

        hub.styles.save(CLE, Style(bg="#222222"))
        hub.publish_style(CLE)

        overlay.attendre(2)
    finally:
        overlay.fermer()

    dernier = overlay.recus[-1]
    assert dernier["title"] == "Love Me"
    assert dernier["artist"] == "JMSN"
    assert dernier["style"]["bg"] == "#222222"
    assert "style_only" not in dernier


def test_deux_overlays_ouverts_recoivent_le_meme_changement(monte):
    # Une source en aperçu et une à l'antenne, sur la même URL.
    hub, serveur = monte
    url = _url_evenements(serveur, hub, CLE)
    premier, second = _Overlay(url), _Overlay(url)
    try:
        premier.attendre(1)
        second.attendre(1)

        hub.styles.save(CLE, Style(bg="#334455"))
        hub.publish_style(CLE)

        premier.attendre(2)
        second.attendre(2)
    finally:
        premier.fermer()
        second.fermer()

    assert premier.recus[-1]["style"]["bg"] == "#334455"
    assert second.recus[-1]["style"]["bg"] == "#334455"


def test_une_autre_source_nest_pas_derangee(monte):
    hub, serveur = monte
    overlay = _Overlay(_url_evenements(serveur, hub, "deezer"))
    try:
        overlay.attendre(1)

        hub.styles.save(CLE, Style(bg="#FF00FF"))
        hub.publish_style(CLE)

        # Rien de plus ne doit arriver sur Deezer : son style n'a pas bougé.
        threading.Event().wait(0.4)
    finally:
        overlay.fermer()

    assert len(overlay.recus) == 1
    assert overlay.recus[0]["style"]["bg"] == Style().bg


def test_la_reinitialisation_revient_au_style_par_defaut(monte):
    hub, serveur = monte
    hub.styles.save(CLE, Style(bg="#FF0000", opacity=5))
    overlay = _Overlay(_url_evenements(serveur, hub, CLE))
    try:
        overlay.attendre(1)

        hub.styles.reset(CLE)
        hub.publish_style(CLE)

        overlay.attendre(2)
    finally:
        overlay.fermer()

    assert overlay.recus[-1]["style"]["bg"] == Style().bg
    assert overlay.recus[-1]["style"]["opacity"] == Style().opacity


# ----------------------------------------------------------------------------
# Traversee de chemin par ?source=
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("route", ["musiclogo", "musicbg", "musiccover",
                                   "musicevents", "music"])
@pytest.mark.parametrize("forme", ["absolu", "relatif", "majuscules"])
def test_une_source_hors_forme_recoit_un_404(monte, tmp_path, route, forme):
    """Regression : `?source=C:/.../photo` servait n importe quel PNG du disque.

    Le PNG vise EXISTE : avant le correctif, `/musiclogo` le renvoyait avec
    un 200 et son contenu.
    """
    hub, serveur = monte
    (tmp_path / "photo.png").write_bytes(b"\x89PNG-contenu-prive")
    source = {"absolu": (tmp_path / "photo").as_posix(),
              "relatif": "../../../../photo",
              "majuscules": "SPOTIFY"}[forme]
    url = (f"{serveur.base_url()}/{route}/{hub.ensure_token()}"
           f"?source={urllib.parse.quote(source, safe='')}")

    with pytest.raises(urllib.error.HTTPError) as erreur:
        urllib.request.urlopen(url, timeout=10)

    assert erreur.value.code == 404


def test_une_source_legitime_passe_toujours(monte):
    hub, serveur = monte
    url = f"{serveur.base_url()}/music/{hub.ensure_token()}?source=spotify"

    with urllib.request.urlopen(url, timeout=10) as reponse:
        assert reponse.status == 200



# ----------------------------------------------------------------------------
# Régénérer les liens
# ----------------------------------------------------------------------------
def test_regenerer_coupe_les_overlays_deja_ouverts(monte):
    """Régression : le jeton n'était contrôlé qu'à la connexion. Après
    « Régénérer », un overlay ouvert avec l'ancien lien continuait de
    recevoir chaque morceau — alors que l'utilisateur venait de le révoquer."""
    hub, serveur = monte
    overlay = _Overlay(_url_evenements(serveur, hub, CLE))
    try:
        overlay.attendre(1)
        hub.regenerate_token()
        overlay._thread.join(timeout=6)        # le flux doit se fermer seul

        assert not overlay._thread.is_alive(), "le flux révoqué est resté ouvert"
    finally:
        overlay.fermer()


def test_lancien_lien_ne_repond_plus_et_le_nouveau_si(monte):
    hub, serveur = monte
    ancien = serveur.music_url(CLE)

    hub.regenerate_token()
    nouveau = serveur.music_url(CLE)

    assert nouveau != ancien
    with pytest.raises(urllib.error.HTTPError) as erreur:
        urllib.request.urlopen(ancien, timeout=10)
    assert erreur.value.code == 404
    with urllib.request.urlopen(nouveau, timeout=10) as reponse:
        assert reponse.status == 200


def test_regenerer_garde_les_styles_et_survit_au_redemarrage(monte, tmp_path):
    hub, _serveur = monte
    hub.styles.save(CLE, Style(bg="#123456"))

    neuf = hub.regenerate_token()
    relu = MusicHub(path=tmp_path / "music_widget.json",
                    backgrounds=tmp_path / "fonds")

    assert relu.ensure_token() == neuf
    assert relu.styles.get(CLE).bg == "#123456"


def test_un_echec_decriture_laisse_lancien_lien_en_service(monte, monkeypatch):
    # Rien n'est révoqué à moitié : ni l'ancien jeton perdu, ni le neuf actif.
    hub, serveur = monte
    avant = serveur.music_url(CLE)

    def disque_plein(_jeton):
        raise OSError("disque plein")
    monkeypatch.setattr(hub.styles, "replace_token", disque_plein)

    with pytest.raises(OSError):
        hub.regenerate_token()
    assert serveur.music_url(CLE) == avant
