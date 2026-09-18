"""
music_audio.py — Niveau audio D'UNE application, pour la forme d'onde.

Chaque overlay ne doit réagir qu'au son de SA source : celui de Spotify pour
l'overlay Spotify, celui de Deezer pour le sien. Windows tient un compteur de
niveau par session audio (`IAudioMeterInformation`, ce que lit le mélangeur de
volume) : c'est lui qu'on interroge, sur les seules sessions appartenant à
l'application visée.

Ce que ça donne, et ce que ça ne donne pas
------------------------------------------
Le compteur rend une AMPLITUDE, pas un spectre. La forme d'onde affiche donc
l'amplitude dans le temps — un oscillogramme qui défile — et non des bandes de
fréquences.

Un vrai spectre par application demanderait le flux PCM de ce seul processus,
c'est-à-dire `ActivateAudioInterfaceAsync` en mode `PROCESS_LOOPBACK` (l'API
derrière la source « Application Audio Capture » d'OBS). Elle a été essayée
ici : elle refuse l'appel depuis Python avec `E_ILLEGAL_METHOD_CALL`, quel que
soit le mode d'apartment COM, et même avec des arguments volontairement
invalides — le rejet précède la lecture des paramètres. La piste reste ouverte,
elle demande une extension native.

Le relevé est périodique : il n'existe pas d'événement pour ce compteur, et le
mélangeur de volume de Windows procède de la même façon.

Windows uniquement. Ailleurs, `available()` est faux et le reste de
l'application fonctionne sans forme d'onde.
"""
from __future__ import annotations

import threading
import time

from collections import deque
from typing import Callable, Optional

from app_paths import logger
from music_catalog import identify

try:
    import comtypes
    from pycaw.api.endpointvolume import IAudioMeterInformation
    from pycaw.pycaw import AudioUtilities
    IMPORT_ERROR: Optional[str] = None
except Exception as exc:                      # pragma: no cover - dépend de l'OS
    comtypes = None                           # type: ignore[assignment]
    IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

#: Nombre de barres publiées. Fixé par le contrat de la page.
BANDS = 48

#: Relevés par seconde. Au-delà, on dépense du CPU pour des images qu'OBS ne
#: composera pas ; en dessous, la forme d'onde saccade.
RATE_HZ = 30.0

#: Intervalle de réexamen de la liste des sessions, en secondes.
#:
#: Énumérer les sessions coûte cher et ne peut pas se faire à chaque relevé.
#: Mais la liste bouge : un navigateur ouvre une session par onglet sonore, et
#: un lecteur fermé puis rouvert en crée une nouvelle. Sans ce réexamen, la
#: forme d'onde resterait plate après un simple changement d'onglet.
REFRESH_S = 2.0

#: Sous ce pic, on considère qu'il n'y a pas de son.
_SILENCE = 1e-4

#: Décroissance de la référence, par relevé : demi-vie d'environ 20 secondes
#: à 30 Hz.
#:
#: C'est le réglage qui décide si la forme d'onde respire ou reste collee en
#: haut. Une référence qui suit le niveau instantané donne toujours 1 : chaque
#: pic devient son propre maximum. Lente, elle reste quasi constante pendant un
#: morceau, et les barres montrent alors les écarts REELS de la musique. Elle ne
#: sert plus qu'à rattraper une application globalement faible.
_REF_DECAY = 0.99885

#: Référence minimale. Un son fort culmine autour de 0,85 sur ce compteur ;
#: descendre ce plancher trop bas ferait saturer la moindre musique douce.
_REF_FLOOR = 0.15

#: Exposant appliqué au rapport niveau/référence. Sous 1, il relance un peu les
#: valeurs basses sans écraser les hautes : mesuré sur un signal aux dynamiques
#: d'une musique, il porte la médiane de 0,34 à 0,43 pour une amplitude totale
#: de 0,86 — les barres occupent la hauteur au lieu de raser le sol.
_COURBE = 0.8


def available() -> bool:
    """Les compteurs de session sont-ils lisibles ? Faux hors Windows."""
    return comtypes is not None


def session_meters(key: str) -> list:
    """Les compteurs des sessions audio appartenant à ce lecteur.

    Le rapprochement passe par `music_catalog.identify`, qui reconnaît un nom
    d'exécutable comme il reconnaît un AppUserModelId : `Spotify.exe` et
    `SpotifyAB.SpotifyMusic_…!Spotify` donnent la même clé. Une seule table
    d'identification pour les deux mondes.

    Plusieurs sessions pour une même application est le cas NORMAL : un
    navigateur en ouvre une par onglet qui joue du son.
    """
    metres = []
    for session in AudioUtilities.GetAllSessions():
        processus = session.Process
        if processus is None:
            continue
        try:
            nom = processus.name()
        except Exception:
            continue                  # le processus vient de disparaître
        if identify(nom).key != key:
            continue
        try:
            metres.append(session._ctl.QueryInterface(IAudioMeterInformation))
        except Exception:
            logger.debug("Pas de compteur audio pour %s.", nom)
    return metres


class AppLevels:
    """Suit le niveau d'UNE application et publie `BANDS` valeurs entre 0 et 1.

    Les valeurs sont l'historique récent du niveau : la plus ancienne à gauche,
    la plus récente à droite. La forme d'onde défile donc avec la musique de
    cette application, et d'elle seule.

    `on_levels` est appelée depuis le thread de relevé : l'appelant se charge
    de repasser sur sa boucle s'il en a une.
    """

    def __init__(self, key: str,
                 on_levels: Callable[[list[float]], None]) -> None:
        self._key = key
        self._on_levels = on_levels
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._historique: deque[float] = deque([0.0] * BANDS, maxlen=BANDS)
        self._reference = _REF_FLOOR
        self._silencieux = False

    # -- cycle de vie ------------------------------------------------------ #

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        # Un thread précédent encore vivant interdit d'en lancer un second :
        # deux relevés concurrents sur les mêmes compteurs COM n'apportent
        # rien et doublent le coût.
        if self.is_running or not available():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run,
                                        name=f"audio-{self._key}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Demande l'arrêt et attend la fin du thread.

        La référence n'est lâchée que si le thread est bien mort, pour que
        `start()` n'en empile pas un second par-dessus.
        """
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=2)
        if thread.is_alive():
            logger.warning("Le relevé audio de %s ne s'est pas arrêté dans le "
                           "délai.", self._key)
            return
        self._thread = None

    # -- boucle ------------------------------------------------------------ #

    def _run(self) -> None:
        """Relève le niveau, et se remet des disparitions de session.

        Fermer puis rouvrir un lecteur invalide ses compteurs : c'est une
        situation NORMALE. On les redemande au prochain réexamen plutôt que de
        laisser l'exception remonter jusqu'au serveur.
        """
        try:
            comtypes.CoInitialize()
        except Exception:
            logger.exception("Initialisation COM du relevé audio impossible.")
            return
        try:
            metres: list = []
            prochain_examen = 0.0
            periode = 1.0 / RATE_HZ
            while not self._stop.is_set():
                maintenant = time.monotonic()
                if maintenant >= prochain_examen:
                    prochain_examen = maintenant + REFRESH_S
                    try:
                        metres = session_meters(self._key)
                    except Exception:
                        logger.debug("Sessions audio illisibles pour %s.",
                                     self._key, exc_info=True)
                        metres = []
                self.relever(metres)
                self._stop.wait(periode)
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def relever(self, metres: list) -> None:
        """Lit le pic des compteurs fournis et le pousse dans l'historique."""
        pic = 0.0
        for metre in metres:
            try:
                pic = max(pic, float(metre.GetPeakValue()))
            except Exception:
                # Session disparue : le prochain réexamen refera la liste.
                continue
        self.pousser(pic)

    def pousser(self, pic: float) -> None:
        """Ajoute un relevé et publie l'historique normalisé."""
        if pic < _SILENCE and not any(v > 0.01 for v in self._historique):
            # Ligne plate déjà publiée : rien de neuf à envoyer.
            if self._silencieux:
                return
            self._silencieux = True
            self._reference = _REF_FLOOR
            self._historique = deque([0.0] * BANDS, maxlen=BANDS)
            self._on_levels([0.0] * BANDS)
            return

        self._silencieux = False
        # Le niveau se calcule avec la référence PRÉCÉDENTE, avant de la mettre
        # à jour. La calculer d'abord faisait que tout nouveau maximum valait
        # exactement 1 : la forme d'onde restait collée en haut en permanence.
        niveau = min(1.0, pic / self._reference) ** _COURBE
        self._reference = max(pic, self._reference * _REF_DECAY, _REF_FLOOR)

        # L'historique garde des valeurs DÉJÀ mises à l'échelle : sinon un
        # changement de référence ferait sauter d'un coup toutes les barres
        # déjà affichées, y compris celles du passé.
        self._historique.append(round(niveau, 3))
        self._on_levels(list(self._historique))
