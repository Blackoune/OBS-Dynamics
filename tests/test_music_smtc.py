"""Sonde SMTC : normalisation, tolérance aux pannes, cache de pochettes.

Aucun appel à Windows ici. Les objets de session sont factices : ce qui est
vérifié, c'est la couche de traduction entre l'API WinRT et le modèle affiché,
et le fait qu'un lecteur qui répond mal dégrade sa propre carte sans empêcher
la lecture des autres.
"""
from __future__ import annotations

import asyncio
import time

from datetime import timedelta

import pytest

import music_smtc
from music_smtc import MusicWatcher, Session, _ms, _read_all


# ----------------------------------------------------------------------------
# Doublures
# ----------------------------------------------------------------------------
class FakeProps:
    def __init__(self, title="", artist="", album="", thumbnail=None) -> None:
        self.title = title
        self.artist = artist
        self.album_title = album
        self.thumbnail = thumbnail


class FakeInfo:
    def __init__(self, status: str) -> None:
        self.playback_status = type("Status", (), {"name": status})()


class FakeTimeline:
    def __init__(self, position_s: float, end_s: float) -> None:
        self.position = timedelta(seconds=position_s)
        self.end_time = timedelta(seconds=end_s)


class FakeSession:
    """Session WinRT minimale. `broken` nomme le getter qui doit échouer."""

    def __init__(self, app_id: str, props: FakeProps, status="PLAYING",
                 position_s=12.0, end_s=200.0, broken: str = "") -> None:
        self.source_app_user_model_id = app_id
        self._props = props
        self._status = status
        self._timeline = FakeTimeline(position_s, end_s)
        self._broken = broken

    def _guard(self, name: str) -> None:
        if self._broken == name:
            raise RuntimeError(f"{name} indisponible")

    def get_playback_info(self):
        self._guard("playback_info")
        return FakeInfo(self._status)

    def get_timeline_properties(self):
        self._guard("timeline")
        return self._timeline

    async def try_get_media_properties_async(self):
        self._guard("media_properties")
        return self._props


def _watcher() -> MusicWatcher:
    return MusicWatcher(on_update=lambda _sessions: None, dispatch=lambda fn: fn())


def _read(raw: FakeSession, current_id=None) -> Session:
    return asyncio.run(_watcher()._read_session(raw, current_id))


# ----------------------------------------------------------------------------
# Conversions
# ----------------------------------------------------------------------------
def test_ms_convertit_un_timespan_projete():
    assert _ms(timedelta(seconds=273)) == 273_000
    assert _ms(timedelta(milliseconds=1500)) == 1500


def test_ms_rend_zero_quand_la_duree_est_absente():
    # Un lecteur web sans durée connue remonte None plutôt qu'un TimeSpan.
    assert _ms(None) == 0


class _ReaderRendantLesOctets:
    def read_bytes(self, length):
        return b"\x89PNG"[:length]


class _ReaderRemplissantLeTampon:
    def read_bytes(self, out):
        if not isinstance(out, bytearray):
            raise TypeError("attend un tampon")
        out[:] = b"\x89PNG"[:len(out)]


@pytest.mark.parametrize("reader", [_ReaderRendantLesOctets(),
                                    _ReaderRemplissantLeTampon()])
def test_read_all_accepte_les_deux_signatures_de_datareader(reader):
    # Les versions de winrt-runtime ne s'accordent pas sur read_bytes ; la
    # sonde doit marcher sous les deux sans épingler une version précise.
    assert _read_all(reader, 4) == b"\x89PNG"


# ----------------------------------------------------------------------------
# Normalisation d'une session
# ----------------------------------------------------------------------------
def test_les_metadonnees_sont_recopiees_dans_le_modele():
    raw = FakeSession("Spotify.exe",
                      FakeProps(title="Love Me", artist="JMSN", album="Soft Spot"),
                      position_s=3.98, end_s=273.0)

    session = _read(raw, current_id="Spotify.exe")

    assert session.app_id == "Spotify.exe"
    assert (session.title, session.artist, session.album) == ("Love Me", "JMSN",
                                                              "Soft Spot")
    assert session.status == "PLAYING"
    assert session.is_playing is True
    assert session.position_ms == 3980
    assert session.duration_ms == 273_000
    assert session.is_current is True
    assert session.errors == []


def test_une_session_en_pause_n_est_pas_en_lecture():
    raw = FakeSession("Chrome", FakeProps(title="x"), status="PAUSED")
    session = _read(raw)
    assert session.status == "PAUSED"
    assert session.is_playing is False


def test_seule_la_session_courante_porte_le_drapeau():
    raw = FakeSession("Chrome", FakeProps(title="x"))
    assert _read(raw, current_id="Spotify.exe").is_current is False


def test_les_champs_vides_ne_deviennent_jamais_None():
    # Les libellés partent directement dans des widgets Tk : un None y
    # lèverait une exception au moment de l'affichage, pas ici.
    session = _read(FakeSession("Deezer", FakeProps()))
    assert (session.title, session.artist, session.album) == ("", "", "")


# ----------------------------------------------------------------------------
# Tolérance aux pannes
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("broken", ["playback_info", "timeline", "media_properties"])
def test_un_getter_en_echec_est_signale_sans_perdre_la_session(broken):
    # Un lecteur qui répond mal doit rester visible dans la liste, avec son
    # erreur : le masquer ferait disparaître la source sans explication.
    raw = FakeSession("Lecteur.exe", FakeProps(title="Love Me"), broken=broken)

    session = _read(raw)

    assert session.app_id == "Lecteur.exe"
    assert any(err.startswith(broken) for err in session.errors)


def test_une_panne_de_metadonnees_laisse_l_etat_de_lecture():
    raw = FakeSession("Lecteur.exe", FakeProps(title="Love Me"),
                      status="PLAYING", broken="media_properties")
    session = _read(raw)
    assert session.status == "PLAYING"
    assert session.title == ""


# ----------------------------------------------------------------------------
# Cache des pochettes
# ----------------------------------------------------------------------------
class _FluxFactice:
    """Référence de pochette qui se lit sans WinRT.

    Associée à `_brancher_un_flux_factice`, elle permet de traverser
    réellement `_attach_thumbnail` — y compris la mise en cache et son
    plafond — sur une machine où les projections WinRT sont absentes.
    """

    size = 4

    async def open_read_async(self):
        return self

    async def read_async(self, _buffer, _count, _options):
        return None


def _brancher_un_flux_factice(monkeypatch) -> None:
    tampon = type("Tampon", (), {"capacity": 4, "length": 4})
    monkeypatch.setattr(music_smtc, "Buffer", lambda _size: tampon(), raising=False)
    monkeypatch.setattr(music_smtc, "InputStreamOptions",
                        type("Options", (), {"READ_AHEAD": 0}), raising=False)
    monkeypatch.setattr(music_smtc, "DataReader",
                        type("Lecteur", (), {"from_buffer": staticmethod(lambda _b: None)}),
                        raising=False)
    monkeypatch.setattr(music_smtc, "_read_all", lambda _reader, _length: b"PNG!")
    # Pillow décoderait ces quatre octets en erreur : la taille reste None,
    # ce qui n'est pas le sujet de ces deux tests.
    monkeypatch.setattr(music_smtc, "Image", None)


class _RefQuiRefuseDEtreOuverte:
    """Référence de pochette qui échoue si on tente de la relire."""

    async def open_read_async(self):
        raise AssertionError("la pochette ne devait pas etre relue")


def test_une_pochette_deja_lue_n_est_pas_relue():
    # Sans ce cache, chaque événement de lecture/pause redéclencherait la
    # lecture des centaines de kilo-octets de la pochette courante.
    raw = FakeSession("Spotify.exe",
                      FakeProps(title="Love Me", artist="JMSN",
                                thumbnail=_RefQuiRefuseDEtreOuverte()))
    watcher = _watcher()
    watcher._thumbs[("Spotify.exe", "Love Me", "JMSN")] = (b"PNGDATA", (300, 300), "PNG")

    session = asyncio.run(watcher._read_session(raw, None))

    assert session.thumbnail == b"PNGDATA"
    assert session.thumbnail_size == (300, 300)
    assert session.thumbnail_format == "PNG"
    assert session.errors == []


def test_une_pochette_illisible_est_signalee_sans_faire_tomber_la_session():
    class _RefEnPanne:
        async def open_read_async(self):
            raise RuntimeError("flux ferme")

    raw = FakeSession("Spotify.exe",
                      FakeProps(title="Love Me", artist="JMSN",
                                thumbnail=_RefEnPanne()))

    session = _read(raw)

    assert session.title == "Love Me"
    assert session.thumbnail is None
    assert any(err.startswith("thumbnail") for err in session.errors)


def test_une_session_sans_pochette_reste_lisible():
    session = _read(FakeSession("Chrome", FakeProps(title="x", thumbnail=None)))
    assert session.thumbnail is None
    assert session.thumbnail_size is None
    assert session.errors == []


def test_le_cache_de_pochettes_ne_grossit_pas_sans_fin(monkeypatch):
    """Le plafond doit se declencher tout seul, pendant une vraie lecture.

    Une longue diffusion enchaine des centaines de morceaux ; sans ce
    vidage, chaque pochette gardee ajouterait ses centaines de kilo-octets
    en memoire jusqu'a la fermeture de l'application.
    """
    _brancher_un_flux_factice(monkeypatch)
    watcher = _watcher()
    for index in range(music_smtc._THUMB_CACHE_MAX):
        watcher._thumbs[("app", f"titre{index}", "artiste")] = (b"x", None, "PNG")

    asyncio.run(watcher._attach_thumbnail(
        Session(app_id="app", title="nouveau", artist="artiste"),
        FakeProps(thumbnail=_FluxFactice())))

    assert len(watcher._thumbs) == 1
    assert ("app", "nouveau", "artiste") in watcher._thumbs


def test_une_pochette_lue_est_mise_en_cache(monkeypatch):
    _brancher_un_flux_factice(monkeypatch)
    watcher = _watcher()
    session = Session(app_id="app", title="Love Me", artist="JMSN")

    asyncio.run(watcher._attach_thumbnail(session, FakeProps(thumbnail=_FluxFactice())))

    assert session.thumbnail == b"PNG!"
    assert watcher._thumbs[("app", "Love Me", "JMSN")][0] == b"PNG!"
# ----------------------------------------------------------------------------
# Rapport de la vignette : pochette carrée ou image de vidéo
# ----------------------------------------------------------------------------
def _avec_vignette(largeur: int, hauteur: int) -> Session:
    return Session(app_id="Chrome", thumbnail=b"PNG!",
                   thumbnail_size=(largeur, hauteur))


@pytest.mark.parametrize("largeur,hauteur", [(300, 300), (150, 150), (301, 300),
                                             (640, 640)])
def test_une_pochette_dalbum_est_carree(largeur, hauteur):
    # 301x300 compris : un pixel d'écart vient de l'encodeur, pas d'un
    # changement de format, et ne doit pas basculer l'affichage.
    assert _avec_vignette(largeur, hauteur).is_square_art is True


@pytest.mark.parametrize("largeur,hauteur", [(1280, 720), (480, 360), (640, 480)])
def test_une_miniature_de_video_n_est_pas_carree(largeur, hauteur):
    assert _avec_vignette(largeur, hauteur).is_square_art is False


def test_une_vignette_de_taille_inconnue_est_traitee_comme_carree():
    # Sans Pillow, la taille reste None : le carré est le format le plus
    # fréquent, donc le pari le moins coûteux.
    assert Session(app_id="Chrome", thumbnail=b"PNG!").is_square_art is True


def test_une_hauteur_nulle_ne_provoque_pas_de_division_par_zero():
    assert _avec_vignette(320, 0).is_square_art is True
def test_la_taille_daffichage_conserve_le_rapport_dune_video():
    # Le bug d'origine : une miniature 16:9 forcée dans un carré était
    # compressée en largeur, donc déformée.
    from ui_music import fitted_cover_size

    largeur, hauteur = fitted_cover_size(_avec_vignette(1280, 720))

    assert hauteur == 76
    assert abs(largeur / hauteur - 16 / 9) < 0.05


def test_la_taille_daffichage_dune_pochette_reste_carree():
    from ui_music import fitted_cover_size

    assert fitted_cover_size(_avec_vignette(300, 300)) == (76, 76)


def test_une_image_panoramique_est_plafonnee_en_largeur():
    # Sans plafond, elle repousserait le texte de la carte hors de l'écran.
    from ui_music import _COVER_MAX_W, fitted_cover_size

    largeur, _ = fitted_cover_size(_avec_vignette(4000, 300))

    assert largeur == _COVER_MAX_W
# ----------------------------------------------------------------------------
# Arrêt
# ----------------------------------------------------------------------------
@pytest.mark.skipif(not music_smtc.available(),
                    reason="projections WinRT absentes (hors Windows)")
@pytest.mark.parametrize("delai_s", [0.0, 0.05])
def test_un_arret_immediat_ne_laisse_ni_erreur_ni_thread(delai_s):
    """Régression : fermer la fenêtre juste après l'ouverture.

    La sonde était alors en train d'attendre `request_async()`. Arrêter la
    boucle à ce moment faisait échouer l'opération WinRT en cours, puis la
    projection rappelait une boucle fermée depuis son propre thread — une
    exception qu'aucun try/except de ce module ne pouvait intercepter.
    """
    erreurs = []
    watcher = MusicWatcher(on_update=lambda _s: None, dispatch=lambda fn: fn(),
                           on_error=erreurs.append)
    watcher.start()
    time.sleep(delai_s)

    watcher.stop()

    assert watcher._thread is None
    assert erreurs == []
    assert watcher._tokens == []
