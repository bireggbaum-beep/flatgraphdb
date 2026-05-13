"""
Shared fixtures. Each test gets a fresh DB rooted in a pytest tmp_path so
nothing persists across tests and tests can run in parallel.
"""
import sys
from pathlib import Path

import pytest

# Make flatgraph importable without installing the package
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flatgraph import FlatGraphDB, MaintenanceEngine  # noqa: E402


@pytest.fixture
def db(tmp_path):
    """Fresh FlatGraphDB instance in an isolated tmp dir."""
    return FlatGraphDB(str(tmp_path))


@pytest.fixture
def db_locked(tmp_path):
    """Fresh FlatGraphDB with file_lock=True (multi-process safety on)."""
    return FlatGraphDB(str(tmp_path), file_lock=True)


@pytest.fixture
def db_factory(tmp_path):
    """
    Returns a factory that opens a new FlatGraphDB handle on the same root.
    Simulates multiple processes / app instances of the same user.

        A = db_factory()
        B = db_factory()  # second handle on the same dir
    """
    def _make(**kwargs):
        kwargs.setdefault("file_lock", True)
        return FlatGraphDB(str(tmp_path), **kwargs)
    return _make


@pytest.fixture
def gc_factory(tmp_path):
    """Returns a factory for MaintenanceEngine on the same root as `db`."""
    def _make():
        return MaintenanceEngine(str(tmp_path))
    return _make
