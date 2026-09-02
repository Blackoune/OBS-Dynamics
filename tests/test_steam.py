"""SteamScanner : parsing des formats KeyValue de Valve (.vdf / .acf).

C'est la partie la plus fragile du scan : du parsing par regex sur un format
maison, avec des chemins Windows échappés en double antislash.
"""
from __future__ import annotations

import pytest


VDF = r'''
"libraryfolders"
{
    "0"
    {
        "path"        "C:\\Program Files (x86)\\Steam"
        "label"       ""
    }
    "1"
    {
        "path"        "D:\\SteamLibrary"
    }
}
'''

ACF = r'''
"AppState"
{
    "appid"        "400"
    "name"         "Portal"
    "installdir"   "Portal"
    "StateFlags"   "4"
}
'''


def test_unescape_windows_paths(app_module):
    unescape = app_module.SteamScanner._unescape
    assert unescape(r"C:\\Program Files (x86)\\Steam") == r"C:\Program Files (x86)\Steam"
    assert unescape(r'Jeu \"Special\"') == 'Jeu "Special"'


def test_path_regex_finds_every_library(app_module):
    found = [app_module.SteamScanner._unescape(m.group(1))
             for m in app_module.SteamScanner._PATH_RE.finditer(VDF)]
    assert found == [r"C:\Program Files (x86)\Steam", r"D:\SteamLibrary"]


def test_acf_fields(app_module):
    scanner = app_module.SteamScanner
    assert scanner._APPID_RE.search(ACF).group(1) == "400"
    assert scanner._NAME_RE.search(ACF).group(1) == "Portal"
    assert scanner._INSTALLDIR_RE.search(ACF).group(1) == "Portal"


def test_name_regex_handles_escaped_quotes(app_module):
    acf = r'"name" "Tom Clancy\"s Splinter Cell"'
    raw = app_module.SteamScanner._NAME_RE.search(acf).group(1)
    assert app_module.SteamScanner._unescape(raw) == 'Tom Clancy"s Splinter Cell'


def test_scan_reads_manifests_from_all_libraries(app_module, tmp_path, monkeypatch):
    """Bout en bout sur une arborescence Steam factice : deux bibliothèques,
    un manifeste chacune."""
    root = tmp_path / "Steam"
    lib2 = tmp_path / "SteamLibrary"
    for base, appid, name in ((root, "400", "Portal"), (lib2, "620", "Portal 2")):
        apps = base / "steamapps"
        (apps / "common" / name).mkdir(parents=True)
        (apps / f"appmanifest_{appid}.acf").write_text(
            f'"AppState"{{"appid" "{appid}" "name" "{name}" "installdir" "{name}"}}',
            encoding="utf-8")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"{{"0"{{"path" "{str(lib2)}"}}}}'.replace("\\", "\\\\"),
        encoding="utf-8")

    scanner = app_module.SteamScanner()
    monkeypatch.setattr(scanner, "find_steam_root", lambda: root)
    games = scanner.scan_installed_games()

    assert sorted(g["name"] for g in games) == ["Portal", "Portal 2"]
    assert {g["appid"] for g in games} == {"400", "620"}
    for g in games:
        assert g["install_dir"].endswith(g["name"])


def test_scan_returns_empty_when_steam_absent(app_module, monkeypatch):
    scanner = app_module.SteamScanner()
    monkeypatch.setattr(scanner, "find_steam_root", lambda: None)
    assert scanner.scan_installed_games() == []


def test_incomplete_manifest_is_skipped(app_module, tmp_path, monkeypatch):
    """Un .acf tronqué (téléchargement en cours) ne doit pas produire une
    entrée bancale ni faire échouer tout le scan."""
    root = tmp_path / "Steam"
    apps = root / "steamapps"
    apps.mkdir(parents=True)
    (apps / "appmanifest_999.acf").write_text('"AppState"{"appid" "999"}', encoding="utf-8")

    scanner = app_module.SteamScanner()
    monkeypatch.setattr(scanner, "find_steam_root", lambda: root)
    assert scanner.scan_installed_games() == []


def test_library_paths_always_include_root(app_module, tmp_path):
    root = tmp_path / "Steam"
    (root / "steamapps").mkdir(parents=True)
    assert app_module.SteamScanner().find_library_paths(root) == [root]
