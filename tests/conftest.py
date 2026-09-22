"""Fixtures partagées.

obs_dynamics.py construit des widgets CustomTkinter au niveau module, mais
seulement dans `if __name__ == "__main__"` — l'import est donc sûr sans
serveur graphique. On l'importe une fois ici pour éviter le coût répété.
"""
from __future__ import annotations

import gc
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _ramasser_sur_le_thread_principal():
    """Ramasse les déchets de chaque test ICI, sur le thread principal.

    Les tests d'interface laissent derrière eux des variables Tk prises dans
    des cycles. Le ramasse-miettes se déclenche dans le thread qui alloue au
    mauvais moment — souvent un thread du serveur HTTP d'un test suivant.
    `Variable.__del__` y attend alors une boucle Tk qui ne tourne pas :
    environ 1,1 s par variable (mesuré : 10,9 s pour 10). La requête en
    cours restait sans réponse plus de 15 s, d'où les `TimeoutError`
    intermittents de test_triggers.py, en local comme en CI.

    Rien de tel dans l'application : sa boucle Tk tourne en permanence.
    """
    yield
    gc.collect()


@pytest.fixture(scope="session")
def app_module():
    import obs_dynamics
    return obs_dynamics


@pytest.fixture
def tmp_env(tmp_path: Path) -> Path:
    return tmp_path / ".env"


@pytest.fixture
def tmp_games(tmp_path: Path) -> Path:
    return tmp_path / "games.json"
