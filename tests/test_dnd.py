"""Glisser-déposer de médias sur la zone de dépôt."""
from __future__ import annotations

import pytest

ctk = pytest.importorskip("customtkinter")


@pytest.mark.parametrize("raw,expected", [
    ("C:/a.png", ["C:/a.png"]),
    ("{C:/mon dossier/image un.png}", ["C:/mon dossier/image un.png"]),
    ("{C:/mon dossier/a.png} C:/b.mp4", ["C:/mon dossier/a.png", "C:/b.mp4"]),
    ("C:/a.png C:/b.png", ["C:/a.png", "C:/b.png"]),
    ("", []),
    (None, []),
])
def test_parse_dropped_files(app_module, raw, expected):
    """Tcl entoure d'accolades les chemins contenant un espace."""
    assert app_module.parse_dropped_files(raw) == expected


def test_drop_targets_cover_the_whole_widget_subtree(app_module):
    """Régression : les cibles tkdnd sont par fenêtre et ne remontent pas au
    parent. N'enregistrer que le CTkButton laissait son canvas interne — celui
    qui est réellement sous le curseur — refuser le dépôt."""
    pytest.importorskip("tkinterdnd2")
    try:
        root = ctk.CTk()
    except Exception as exc:
        pytest.skip(f"pas d'affichage disponible : {exc}")
    try:
        zone = ctk.CTkButton(root, text="Déposer ici", width=300, height=100)
        zone.pack()
        root.update()

        assert app_module._try_enable_dnd(zone, lambda files: None)

        def registered(widget):
            names = widget.tk.call("tkdnd::drop_target", "names", widget._w)
            return bool(names)

        assert registered(zone), "le conteneur n'est pas cible de dépôt"
        children = zone.winfo_children()
        assert children, "le CTkButton devrait avoir des enfants (canvas, label)"
        assert all(registered(c) for c in children), \
            "un enfant sous le curseur refuserait le dépôt"
    finally:
        root.destroy()


def test_missing_package_is_not_fatal(app_module, monkeypatch):
    """Sans tkinterdnd2 l'app doit démarrer : le bouton Parcourir suffit."""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinterdnd2":
            raise ImportError("simulé")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert app_module._try_enable_dnd(object(), lambda files: None) is False
