"""Cadrage et choix de source des jaquettes (cover_service)."""
import io

import pytest
from PIL import Image

from cover_service import (COVER_HEIGHT, COVER_WIDTH, GameCoverService,
                           _MAX_IMAGE_BYTES, _fetch_bounded)


class _FakeBody:
    """Réponse en flux minimale : ce que `_fetch_bounded` consomme."""

    def __init__(self, payload=b"", status=200):
        self.status_code = status
        self._payload = payload

    def iter_content(self, taille):
        for debut in range(0, len(self._payload), taille):
            yield self._payload[debut:debut + taille]

    def close(self):
        pass


def _png(width: int, height: int, color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def service(tmp_path):
    svc = GameCoverService(tmp_path / "covers")
    yield svc
    svc.stop()


@pytest.mark.parametrize("size", [(600, 900), (460, 215), (616, 353), (1920, 1080)])
def test_process_image_always_yields_the_2_3_poster_size(service, size):
    out = service._process_image(_png(*size))
    assert out is not None
    assert out.size == (COVER_WIDTH, COVER_HEIGHT)


def test_landscape_source_is_letterboxed_not_center_cropped(service):
    """Un header 460x215 tient EN ENTIER dans la vignette : l'ancien recadrage
    centré n'en gardait qu'une bande, d'où des jaquettes hors cadre."""
    out = service._process_image(_png(460, 215))
    band_h = round(215 * (COVER_WIDTH / 460))
    top = (COVER_HEIGHT - band_h) // 2
    assert out.getpixel((COVER_WIDTH // 2, COVER_HEIGHT // 2)) == (200, 30, 30)  # visuel centré
    assert out.getpixel((COVER_WIDTH // 2, max(0, top - 20))) != (200, 30, 30)   # fond flouté au-dessus


def test_portrait_cover_comes_before_landscape_fallbacks(service):
    urls = service._find_image_urls(session=None, name="", appid="440")
    assert all("440" in u for u in urls)
    first_landscape = next(i for i, u in enumerate(urls) if "header" in u or "capsule" in u)
    assert all("library_600x900" in u for u in urls[:first_landscape])
    assert first_landscape > 0


def test_downloads_run_in_parallel(tmp_path):
    """Une bibliothèque entière doit se remplir d'un bloc après un scan Steam,
    pas jaquette par jaquette."""
    import cover_service

    svc = GameCoverService(tmp_path / "covers")
    try:
        assert len(svc._workers) == cover_service.WORKER_THREADS > 1
        assert all(w.is_alive() for w in svc._workers)
    finally:
        svc.stop()

    for w in svc._workers:
        w.join(timeout=5)
    assert not any(w.is_alive() for w in svc._workers), \
        "une sentinelle par worker est nécessaire, sinon certains restent bloqués"


# -- Jeux récents : URL derrière un hachage ------------------------------ #

class _FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
    def json(self):
        return self._payload


def test_appdetails_returns_the_real_urls(service):
    """Depuis 2025, les jeux récents ne servent plus leurs images sur le
    chemin historique : chaque fichier vit derrière un hachage de contenu
    imprévisible. Vérifié en réel sur MECCHA CHAMELEON et Mouse X, dont tous
    les chemins devinables renvoient 404."""
    hashed = ("https://shared.akamai.steamstatic.com/store_item_assets/steam/"
              "apps/4704690/163e2a742e5f/header.jpg?t=1785908480")

    class Session:
        def get(self, url, timeout=None, stream=False):
            assert "appdetails" in url
            return _FakeResponse(payload={"4704690": {"success": True, "data": {
                "header_image": hashed, "capsule_image": hashed.replace("header", "capsule")}}})

    urls = service._appdetails_urls(Session(), "4704690")
    assert urls[0] == hashed
    assert len(urls) == 2


def test_appdetails_failure_is_not_fatal(service):
    class Session:
        def get(self, url, timeout=None, stream=False):
            raise OSError("réseau coupé")
    assert service._appdetails_urls(Session(), "4704690") == []


def test_appdetails_handles_an_unknown_appid(service):
    class Session:
        def get(self, url, timeout=None, stream=False):
            return _FakeResponse(payload={"999": {"success": False}})
    assert service._appdetails_urls(Session(), "999") == []


def test_appdetails_is_only_queried_when_the_guessable_paths_fail(service, tmp_path):
    """L'API est fortement limitée en débit : elle ne doit pas être appelée
    pour une bibliothèque entière, seulement pour les jeux récents."""
    appels = {"appdetails": 0}

    class Session:
        def get(self, url, timeout=None, stream=False):
            if "appdetails" in url:
                appels["appdetails"] += 1
                return _FakeResponse(payload={"620": {"success": False}})
            if "library_600x900_2x" in url:      # le chemin historique répond
                return _FakeBody(_png(600, 900, (30, 60, 90)))
            return _FakeBody(b"", status=404)

    game = type("G", (), {"id": "g1", "name": "Portal 2", "appid": "620"})()
    assert service._fetch_and_cache(Session(), game) is not None
    assert appels["appdetails"] == 0, "appdetails appelé alors que le CDN a répondu"


# -- Plafond de téléchargement -------------------------------------------- #

def test_an_oversized_download_is_dropped():
    """Un hôte tiers ne doit pas pouvoir faire grossir la mémoire sans fin :
    au-delà du plafond, la lecture s'arrête et l'image est abandonnée."""
    class Session:
        def get(self, url, timeout=None, stream=False):
            return _FakeBody(b"x" * (_MAX_IMAGE_BYTES + 1))

    assert _fetch_bounded(Session(), "http://exemple/enorme.jpg") is None


def test_a_normal_download_passes_through():
    """Le plafond ne doit pas écarter une jaquette de taille normale."""
    image = _png(600, 900)
    assert len(image) < _MAX_IMAGE_BYTES

    class Session:
        def get(self, url, timeout=None, stream=False):
            return _FakeBody(image)

    assert _fetch_bounded(Session(), "http://exemple/ok.jpg") == image
