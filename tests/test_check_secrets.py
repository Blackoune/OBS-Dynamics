"""Le contrôle de commit reconnaît les secrets recopiés hors d'une affectation.

La clé de stream du commit 833d1df n'était pas dans un `.env` : elle
était au milieu d'une URL, dans un message d'erreur, dans un journal. Aucune
règle ne la voyait. Ces tests fixent ce qui doit désormais être refusé.

Les valeurs ci-dessous sont factices ; le dossier `tests/` est exempté des
règles de forme justement pour pouvoir les écrire.
"""
from __future__ import annotations

import pytest

import check_secrets

from check_secrets import CHEMINS_INTERDITS, analyser

CLE_FACTICE = "abcd-efgh-ijkl-mnop-qrst"


def _refuse(chemin: str) -> bool:
    return any(motif.search(chemin) for motif in CHEMINS_INTERDITS)


# ----------------------------------------------------------------------------
# Chemins
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("chemin", [
    "data.old/obs_dynamics.log", "data.old/games.json", "obs_dynamics.log",
    "logs/app.log", "obs_dynamics.log.1",
])
def test_journaux_et_anciennes_donnees_ne_se_versionnent_pas(chemin):
    assert _refuse(chemin)


@pytest.mark.parametrize("chemin", ["music_style.py", "README.md",
                                    "tests/test_catalog.py", "logo.png"])
def test_les_fichiers_du_projet_restent_versionnables(chemin):
    assert not _refuse(chemin)


# ----------------------------------------------------------------------------
# Formes de secrets
# ----------------------------------------------------------------------------
@pytest.mark.parametrize("texte", [
    f"400 Bad Request for url: https://x/videos?id=a&key={CLE_FACTICE}",
    f"rtmp://live.example/app/{CLE_FACTICE}",
    "key=rtmp%3A%2F%2Flive.example%2Fapp%2Fautre",
    "stream_key = live_123456789_" + "a" * 30,              # clé Twitch
    "cle = AIza" + "B" * 35,
])
def test_un_secret_recopie_dans_du_texte_est_refuse(texte):
    assert analyser("journal.txt", texte)


@pytest.mark.parametrize("texte", [
    "id = 8d4f6c1a-3b2e-4c5d-9e8f-0a1b2c3d4e5f",      # UUID
    "rule = TriggerRule(id=uuid.uuid4().hex)",
    "couleur = '#A855F7'  # accent",
    "https://store.steampowered.com/api/storesearch/?term=portal",
])
def test_le_code_ordinaire_passe(texte):
    assert analyser("module.py", texte) == []


def test_une_cle_vide_ne_prend_pas_la_ligne_suivante_pour_valeur():
    # Faux positif trouvé par l'audit de l'historique (env.example de bcefa5c).
    assert analyser("env.example", "OBS_WS_PASSWORD=\nOBS_SCAN_INTERVAL_SECONDS=2.0\n") == []
    assert analyser("env.example", "OBS_WS_PASSWORD=motdepasse_reel_123\n")


def test_le_message_ne_repete_jamais_la_valeur():
    # Il s'affiche dans un terminal et dans un rapport de CI.
    trouvailles = analyser("journal.txt", f"url?key={CLE_FACTICE}")

    assert trouvailles
    assert all(CLE_FACTICE not in trouvaille for trouvaille in trouvailles)


def test_la_cle_de_stream_fuitee_est_reconnue_a_son_condensat():
    # Sa valeur n'est écrite nulle part : seul son SHA-256 l'est.
    assert any("833d1df" in motif
               for motif in check_secrets.CONDENSATS_INTERDITS.values())
