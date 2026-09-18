"""Niveau audio par application : historique, normalisation, silence.

Aucune session audio n'est ouverte ici — les compteurs sont des doublures. Ce
qui est vérifié, c'est la promesse faite à l'utilisateur : la forme d'onde d'un
overlay ne suit que le son de SON application.
"""
from __future__ import annotations

import pytest

import music_audio

from music_audio import BANDS, AppLevels


def _moteur() -> tuple[AppLevels, list]:
    recus: list[list[float]] = []
    return AppLevels("spotify", recus.append), recus


class _Metre:
    """Compteur de session factice."""

    def __init__(self, valeur: float) -> None:
        self._valeur = valeur

    def GetPeakValue(self) -> float:      # noqa: N802 (nom imposé par COM)
        return self._valeur


class _MetreMort:
    """Session disparue : le compteur lève, comme après fermeture du lecteur."""

    def GetPeakValue(self) -> float:      # noqa: N802
        raise OSError("session fermee")


# ----------------------------------------------------------------------------
# Historique
# ----------------------------------------------------------------------------
def test_la_forme_donde_a_toujours_le_nombre_de_barres_annonce():
    moteur, recus = _moteur()

    moteur.pousser(0.5)

    assert len(recus[-1]) == BANDS


def test_le_dernier_releve_est_a_droite():
    # L'historique défile : la valeur la plus récente arrive au bout.
    moteur, recus = _moteur()

    moteur.pousser(1.0)

    assert recus[-1][-1] == pytest.approx(1.0)
    assert recus[-1][0] == pytest.approx(0.0)


def test_les_releves_successifs_remplissent_la_forme_donde():
    moteur, recus = _moteur()

    for _ in range(BANDS):
        moteur.pousser(0.8)

    assert all(valeur == pytest.approx(1.0) for valeur in recus[-1])


def test_les_valeurs_restent_entre_0_et_1():
    moteur, recus = _moteur()

    for pic in (0.01, 0.9, 0.2, 1.0, 0.05):
        moteur.pousser(pic)

    assert all(0.0 <= valeur <= 1.0 for ligne in recus for valeur in ligne)


# ----------------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------------
def test_la_forme_donde_suit_les_variations_du_son():
    """Regression : toutes les barres restaient a fond en permanence.

    La reference etait mise a jour AVANT le calcul du niveau, donc chaque pic
    devenait son propre maximum et valait exactement 1. La forme d onde ne
    bougeait plus, quel que soit le volume de la musique.
    """
    moteur, recus = _moteur()
    for _ in range(40):            # un passage fort etablit la reference
        moteur.pousser(0.8)
    recus.clear()

    for pic in (0.8, 0.6, 0.4, 0.2, 0.1):
        moteur.pousser(pic)

    derniers = [ligne[-1] for ligne in recus]
    assert derniers == sorted(derniers, reverse=True), derniers
    assert min(derniers) < 0.4, derniers
    assert max(derniers) - min(derniers) > 0.5, derniers


def test_une_musique_ne_sature_pas_toute_la_forme_donde():
    """Sur un signal qui varie, la saturation doit rester l exception.

    Mesure sur un vrai signal aux dynamiques d une musique : 6 % des releves
    touchent le plafond. Un signal PARFAITEMENT constant, lui, remplit
    legitimement toute la hauteur : il n y a aucune variation a montrer.
    """
    moteur, recus = _moteur()
    # Des coups qui decroissent, comme une mesure de batterie.
    enveloppe = [0.85, 0.62, 0.45, 0.33, 0.24, 0.18, 0.13, 0.10]
    for _ in range(8):
        for pic in enveloppe:
            moteur.pousser(pic)

    valeurs = recus[-1]
    saturees = sum(1 for valeur in valeurs if valeur >= 0.99)
    assert saturees <= BANDS // 6, valeurs
    assert max(valeurs) - min(valeurs) > 0.5, valeurs


def test_les_barres_deja_affichees_ne_bougent_plus():
    """L historique garde des valeurs deja mises a l echelle.

    Sinon un changement de reference ferait sauter d un coup toutes les barres,
    y compris celles du passe : la forme d onde se reecrirait au lieu de
    defiler.
    """
    moteur, recus = _moteur()
    moteur.pousser(0.3)
    moteur.pousser(0.2)
    avant = list(recus[-1])

    moteur.pousser(1.0)             # bouscule la reference

    assert recus[-1][:-1] == avant[1:]


def test_un_volume_bas_reste_lisible():
    # Sans référence adaptative, une application qui joue doucement laisserait
    # les barres écrasées au sol.
    fort, recus_fort = _moteur()
    faible, recus_faible = _moteur()

    fort.pousser(0.9)
    faible.pousser(0.3)

    assert recus_fort[-1][-1] == pytest.approx(recus_faible[-1][-1])


def test_la_reference_redescend_apres_un_pic():
    moteur, _ = _moteur()
    moteur.pousser(1.0)
    haute = moteur._reference

    for _ in range(400):
        moteur.pousser(0.03)

    assert moteur._reference < haute


# ----------------------------------------------------------------------------
# Silence
# ----------------------------------------------------------------------------
def test_le_silence_donne_une_ligne_plate():
    moteur, recus = _moteur()

    moteur.pousser(0.0)

    assert recus[-1] == [0.0] * BANDS


def test_le_silence_nest_publie_quune_seule_fois():
    # Répéter des zéros trente fois par seconde n'apporte rien à l'image et
    # occupe la file des abonnés pour rien.
    moteur, recus = _moteur()

    for _ in range(6):
        moteur.pousser(0.0)

    assert len(recus) == 1


def test_le_retour_du_son_republie_apres_un_silence():
    moteur, recus = _moteur()
    moteur.pousser(0.0)

    moteur.pousser(0.7)

    assert len(recus) == 2
    assert recus[-1][-1] > 0.5


# ----------------------------------------------------------------------------
# Lecture des compteurs
# ----------------------------------------------------------------------------
def test_le_pic_retenu_est_le_plus_fort_des_sessions():
    # Un navigateur ouvre une session par onglet sonore : c'est la plus forte
    # qui représente ce qu'on entend de cette application.
    moteur, recus = _moteur()

    moteur.relever([_Metre(0.1), _Metre(0.8), _Metre(0.3)])

    assert recus[-1][-1] == pytest.approx(1.0)


def test_une_session_disparue_ne_fait_pas_tomber_le_releve():
    # Fermer puis rouvrir un lecteur invalide ses compteurs : situation
    # normale, pas une panne.
    moteur, recus = _moteur()

    moteur.relever([_MetreMort(), _Metre(0.6)])

    assert recus[-1][-1] == pytest.approx(1.0)


def test_sans_aucune_session_la_forme_donde_est_plate():
    moteur, recus = _moteur()

    moteur.relever([])

    assert recus[-1] == [0.0] * BANDS


# ----------------------------------------------------------------------------
# Rattachement à une application
# ----------------------------------------------------------------------------
class _Processus:
    def __init__(self, nom: str) -> None:
        self._nom = nom

    def name(self) -> str:
        return self._nom


class _Session:
    def __init__(self, nom: str) -> None:
        self.Process = _Processus(nom)
        self._ctl = self

    def QueryInterface(self, _interface):  # noqa: N802 (nom imposé par COM)
        return f"metre-{self.Process.name()}"


def test_seules_les_sessions_de_lapplication_visee_sont_retenues(monkeypatch):
    """Le cœur de la demande : l'overlay Spotify n'écoute que Spotify.

    Un navigateur qui joue une vidéo à côté ne doit pas faire bouger sa forme
    d'onde.
    """
    monkeypatch.setattr(music_audio.AudioUtilities, "GetAllSessions",
                        staticmethod(lambda: [
                            _Session("Spotify.exe"), _Session("chrome.exe"),
                            _Session("Discord.exe"), _Session("Spotify.exe")]))

    metres = music_audio.session_meters("spotify")

    assert metres == ["metre-Spotify.exe", "metre-Spotify.exe"]


def test_chaque_lecteur_connu_retrouve_ses_propres_sessions(monkeypatch):
    monkeypatch.setattr(music_audio.AudioUtilities, "GetAllSessions",
                        staticmethod(lambda: [
                            _Session("Spotify.exe"), _Session("Deezer.exe"),
                            _Session("TIDAL.exe")]))

    assert music_audio.session_meters("deezer") == ["metre-Deezer.exe"]
    assert music_audio.session_meters("tidal") == ["metre-TIDAL.exe"]
    assert music_audio.session_meters("itunes") == []


def test_une_session_sans_processus_est_ignoree(monkeypatch):
    # Le son système n'appartient à aucun processus.
    orpheline = _Session("x")
    orpheline.Process = None
    monkeypatch.setattr(music_audio.AudioUtilities, "GetAllSessions",
                        staticmethod(lambda: [orpheline]))

    assert music_audio.session_meters("spotify") == []


def test_un_processus_disparu_pendant_lenumeration_est_ignore(monkeypatch):
    class _Volatile:
        def __init__(self) -> None:
            self.Process = self
            self._ctl = self

        def name(self):
            raise OSError("processus termine")

    monkeypatch.setattr(music_audio.AudioUtilities, "GetAllSessions",
                        staticmethod(lambda: [_Volatile()]))

    assert music_audio.session_meters("spotify") == []


# ----------------------------------------------------------------------------
# Cycle de vie
# ----------------------------------------------------------------------------
def test_sans_bindings_le_releve_ne_demarre_pas(monkeypatch):
    # Hors Windows, l'application doit fonctionner sans forme d'onde.
    monkeypatch.setattr(music_audio, "comtypes", None)
    moteur, _ = _moteur()

    moteur.start()

    assert moteur.is_running is False


def test_un_arret_sans_demarrage_ne_fait_rien():
    _moteur()[0].stop()


def test_un_thread_encore_vivant_interdit_un_second_releve(monkeypatch):
    class _ThreadQuiNeMeurtPas:
        def is_alive(self) -> bool:
            return True

        def join(self, timeout=None) -> None:
            return None

    moteur, _ = _moteur()
    moteur._thread = _ThreadQuiNeMeurtPas()

    moteur.stop()
    assert moteur._thread is not None      # la référence reste gardée

    monkeypatch.setattr(
        music_audio.threading, "Thread",
        lambda *a, **k: pytest.fail("un second thread de releve a ete cree"))
    moteur.start()
