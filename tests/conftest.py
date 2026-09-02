"""Fixtures partagées.

obs_dynamics.py construit des widgets CustomTkinter au niveau module, mais
seulement dans `if __name__ == "__main__"` — l'import est donc sûr sans
serveur graphique. On l'importe une fois ici pour éviter le coût répété.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
