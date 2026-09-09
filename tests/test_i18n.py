"""i18n : chargement, fallback, placeholders, changement de langue à chaud."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import i18n

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "i18n.json"


@pytest.fixture
def cat(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps({
        "fr": {"HELLO": "Bonjour", "GREET": "Salut {name}"},
        "en": {"HELLO": "Hello", "GREET": "Hi {name}"},
    }), encoding="utf-8")
    return p


def test_missing_key_returns_key_never_raises(cat):
    inst = i18n.I18n(path=cat)
    assert inst.t("CLE_INEXISTANTE") == "CLE_INEXISTANTE"


def test_placeholder_formatting(cat):
    assert i18n.I18n(path=cat).t("GREET", name="Tristan") == "Salut Tristan"


def test_missing_placeholder_returns_template_not_crash(cat):
    """Un kwarg manquant ne doit jamais faire planter l'UI."""
    assert i18n.I18n(path=cat).t("GREET") == "Salut {name}"


def test_set_lang_switches_and_notifies(cat):
    inst = i18n.I18n(path=cat)
    seen = []
    inst.on_change(seen.append)
    assert inst.set_lang("en")
    assert inst.t("HELLO") == "Hello"
    assert seen == ["en"]


def test_set_lang_unknown_is_rejected(cat):
    inst = i18n.I18n(path=cat)
    assert not inst.set_lang("klingon")
    assert inst.current_lang == "fr"


def test_broken_catalog_does_not_crash(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{ pas du json", encoding="utf-8")
    assert i18n.I18n(path=p).t("QUOI") == "QUOI"


# --- Cohérence du vrai catalogue livré ---------------------------------- #

def test_shipped_catalog_languages_are_complete():
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    fr = set(cat["fr"])
    for lang in cat:
        missing = fr - set(cat[lang])
        assert not missing, f"{lang} : {len(missing)} clés manquantes -> {sorted(missing)[:5]}"


def test_shipped_catalog_covers_every_key_used_in_source():
    """Filet de sécurité : aucune clé t("...") du code sans traduction.

    Balaie TOUS les modules de la racine, pas seulement obs_dynamics.py :
    depuis le découpage du 2026-09-09 la majorité des libellés vit dans
    ui_dashboard.py, ui_triggers.py, ui_settings.py et ui_twitch_chat.py.
    """
    import re
    modules = sorted(p for p in ROOT.glob("*.py") if p.name != "build.py")
    assert modules, "aucun module Python trouvé à la racine du projet"
    used: set[str] = set()
    for path in modules:
        used |= set(re.findall(r"""\bt\(\s*["']([A-Za-z0-9_]+)["']""",
                               path.read_text(encoding="utf-8")))
    available = set(json.loads(CATALOG.read_text(encoding="utf-8"))["fr"])
    assert not (used - available), f"clés absentes : {sorted(used - available)}"


def test_placeholders_match_across_languages():
    """Une traduction qui oublie un {placeholder} renvoie un texte tronqué
    silencieusement — on le détecte ici plutôt qu'en production."""
    import re
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    for key, template in cat["fr"].items():
        expected = set(re.findall(r"\{(\w+)\}", template))
        for lang in cat:
            got = set(re.findall(r"\{(\w+)\}", cat[lang].get(key, "")))
            assert got == expected, f"{lang}/{key}: {got} != {expected}"
