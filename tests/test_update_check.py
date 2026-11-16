import hashlib

import pytest
import requests

import update_check


def test_parse_version():
    assert update_check.parse_version("v1.2.3") == (1, 2, 3)
    assert update_check.parse_version("1.10") == (1, 10, 0)
    assert update_check.parse_version("v2.0.0-test") == (2, 0, 0)
    assert update_check.parse_version("") == (0, 0, 0)


def test_newer_version_compares_numbers_not_text():
    assert update_check.newer_version("1.9.0", "v1.10.0") == "1.10.0"
    assert update_check.newer_version("1.0.3", "v1.0.3") is None
    assert update_check.newer_version("1.1.0", "v1.0.9") is None


def test_install_page_by_language():
    assert update_check.install_page("fr", "win32").endswith("/installation/#mise-a-jour")
    assert "/en/installation/" in update_check.install_page("de", "win32")


def test_install_page_on_mac_points_to_the_mac_tab():
    assert update_check.install_page("fr", "darwin").endswith(
        "/installation/#titre-mise-a-jour-mac")


def test_no_check_without_embedded_version(tmp_path, monkeypatch):
    monkeypatch.setattr(update_check, "get_base_path", lambda: tmp_path)
    assert update_check.current_version() is None
    (tmp_path / "version.txt").write_text("0.0.0", encoding="utf-8")
    assert update_check.current_version() is None
    (tmp_path / "version.txt").write_text("1.0.3\n", encoding="utf-8")
    assert update_check.current_version() == "1.0.3"


def test_check_never_raises_on_network_error(monkeypatch):
    monkeypatch.setattr(update_check, "current_version", lambda: "1.0.0")

    def boom(*_a, **_k):
        raise requests.ConnectionError("hors ligne")

    monkeypatch.setattr(update_check.requests, "get", boom)
    assert update_check.check() is None


SUMS = "aa11  Dynamics.exe\n{h}  OBS-Dynamics-Setup.exe\nbb22  OBS-Dynamics.dmg\n"


def _fake_fetch(contenu: bytes, sommes: str):
    def fetch(url, sortie=None):
        assert url.startswith("https://github.com/Blackoune/OBS-Dynamics/releases/download/v1.1.0/")
        if sortie is None:
            return sommes.encode(), ""
        sortie.write_bytes(contenu)
        return b"", hashlib.sha256(contenu).hexdigest()
    return fetch


def test_install_runs_the_installer_when_sha256_matches():
    contenu, lances = b"installeur", []
    sommes = SUMS.format(h=hashlib.sha256(contenu).hexdigest())
    update_check.install_update("1.1.0", fetch=_fake_fetch(contenu, sommes),
                                launch=lambda cmd, **_k: lances.append(cmd))
    assert len(lances) == 1 and lances[0][0].endswith("OBS-Dynamics-Setup.exe")


def test_install_refuses_a_wrong_sha256_and_never_launches():
    lances = []
    with pytest.raises(update_check.UpdateError):
        update_check.install_update("1.1.0", fetch=_fake_fetch(b"piege", SUMS.format(h="0" * 64)),
                                    launch=lambda cmd, **_k: lances.append(cmd))
    assert not lances


def test_install_refuses_a_version_that_is_not_a_version():
    with pytest.raises(update_check.UpdateError):
        update_check.install_update("../../evil", fetch=None, launch=None)


def test_check_returns_newer_release(monkeypatch):
    monkeypatch.setattr(update_check, "current_version", lambda: "1.0.0")

    class Reponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "v1.1.0"}

    monkeypatch.setattr(update_check.requests, "get", lambda *_a, **_k: Reponse())
    assert update_check.check() == "1.1.0"
