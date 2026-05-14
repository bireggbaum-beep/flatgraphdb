"""
Cross-collection transaction atomicity: the staging-file + commit-marker
protocol and its crash-recovery.

"Crash" is simulated two ways: by hand-crafting the on-disk state recovery
must repair, and by monkeypatching the write path to fail at the exact
window that matters (just before vs. just after the commit marker).
"""
import json
import os
import re

import pytest

from flatgraph import FlatGraphDB

_STAGING_RE = re.compile(r".+\.json\.[0-9a-f]{32}$")
_MARKER_RE = re.compile(r"_tx_[0-9a-f]{32}\.json$")


def _no_tx_artifacts(root):
    """True if no staging files and no commit markers are left anywhere."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if _STAGING_RE.match(name) or _MARKER_RE.match(name):
                return False
    return True


def _datenbank(root):
    return os.path.join(root, "datenbank")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_normal_transaction_leaves_no_tx_artifacts(db):
    with db.transaction():
        db.create_node("a", "A1", {"v": 1})
        db.create_node("b", "B1", {"v": 2})
        db.create_edge("a/A1", "b/B1", "rel")
    assert _no_tx_artifacts(db.root)
    assert db.get_node("a/A1") == {"v": 1}
    assert db.get_node("b/B1") == {"v": 2}


def test_transaction_spanning_collections_is_visible_after_reopen(tmp_path):
    db = FlatGraphDB(str(tmp_path))
    with db.transaction():
        db.create_node("a", "A1", {"v": 1})
        db.create_node("b", "B1", {"v": 2})
    del db
    db2 = FlatGraphDB(str(tmp_path))
    assert db2.get_node("a/A1") == {"v": 1}
    assert db2.get_node("b/B1") == {"v": 2}


# ---------------------------------------------------------------------------
# Recovery primitives — hand-crafted on-disk state
# ---------------------------------------------------------------------------

def test_recovery_rolls_forward_a_committed_marker(tmp_path):
    FlatGraphDB(str(tmp_path))  # bootstrap the directory layout
    txid = "a" * 32
    nodes_dir = os.path.join(_datenbank(str(tmp_path)), "nodes")
    staging = os.path.join(nodes_dir, f"book_temp.json.{txid}")
    with open(staging, "w") as f:
        json.dump({"B1": {"title": "recovered"}}, f)
    marker = os.path.join(_datenbank(str(tmp_path)), f"_tx_{txid}.json")
    with open(marker, "w") as f:
        json.dump({
            "apply": [[f"datenbank/nodes/book_temp.json.{txid}",
                       "datenbank/nodes/book_temp.json"]],
            "revisions": {"book": 1},
        }, f)

    db = FlatGraphDB(str(tmp_path))
    assert db.get_node("book/B1") == {"title": "recovered"}
    assert db._collection_revision("book") == 1
    assert not os.path.exists(marker)
    assert not os.path.exists(staging)


def test_recovery_rolls_back_orphan_staging_files(tmp_path):
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"title": "original"})
    del db
    txid = "b" * 32
    nodes_dir = os.path.join(_datenbank(str(tmp_path)), "nodes")
    staging = os.path.join(nodes_dir, f"book_temp.json.{txid}")
    with open(staging, "w") as f:
        json.dump({"B1": {"title": "uncommitted"}, "B2": {"title": "uncommitted"}}, f)

    db2 = FlatGraphDB(str(tmp_path))
    assert db2.get_node("book/B1") == {"title": "original"}  # untouched
    assert db2.get_node("book/B2") is None
    assert not os.path.exists(staging)


def test_corrupt_marker_is_treated_as_rollback(tmp_path):
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"v": "original"})
    del db
    txid = "d" * 32
    nodes_dir = os.path.join(_datenbank(str(tmp_path)), "nodes")
    staging = os.path.join(nodes_dir, f"book_temp.json.{txid}")
    with open(staging, "w") as f:
        json.dump({"B1": {"v": "from-corrupt-tx"}}, f)
    marker = os.path.join(_datenbank(str(tmp_path)), f"_tx_{txid}.json")
    with open(marker, "w") as f:
        f.write("{ this never finished fsync")  # unparseable

    db2 = FlatGraphDB(str(tmp_path))
    assert db2.get_node("book/B1") == {"v": "original"}
    assert not os.path.exists(marker)
    assert not os.path.exists(staging)


def test_recovery_sweeps_tmp_scratch_files(tmp_path):
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"v": 1})
    del db
    scratch = os.path.join(_datenbank(str(tmp_path)), "nodes", "book.json.tmp")
    with open(scratch, "w") as f:
        f.write("partial garbage from an interrupted write")

    db2 = FlatGraphDB(str(tmp_path))
    assert not os.path.exists(scratch)
    assert db2.get_node("book/B1") == {"v": 1}


def test_recovery_idempotent_on_committed_marker(tmp_path):
    FlatGraphDB(str(tmp_path))
    txid = "c" * 32
    nodes_dir = os.path.join(_datenbank(str(tmp_path)), "nodes")
    staging = os.path.join(nodes_dir, f"book_temp.json.{txid}")
    with open(staging, "w") as f:
        json.dump({"B1": {"v": 1}}, f)
    marker = os.path.join(_datenbank(str(tmp_path)), f"_tx_{txid}.json")
    with open(marker, "w") as f:
        json.dump({
            "apply": [[f"datenbank/nodes/book_temp.json.{txid}",
                       "datenbank/nodes/book_temp.json"]],
            "revisions": {"book": 1},
        }, f)

    # First open applies it; every subsequent open must be a clean no-op.
    db1 = FlatGraphDB(str(tmp_path))
    db2 = FlatGraphDB(str(tmp_path))
    assert db1.get_node("book/B1") == {"v": 1}
    assert db2.get_node("book/B1") == {"v": 1}


# ---------------------------------------------------------------------------
# Crash windows — monkeypatched write path
# ---------------------------------------------------------------------------

def test_crash_before_marker_rolls_back(db, monkeypatch):
    """Process dies after staging files exist but before the commit marker."""
    orig = db._save_json_atomic

    def fail_on_marker(path, data):
        if os.path.basename(path).startswith("_tx_"):
            raise RuntimeError("simulated crash before commit marker")
        return orig(path, data)

    monkeypatch.setattr(db, "_save_json_atomic", fail_on_marker)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.create_node("a", "A1", {"v": 1})
            db.create_node("b", "B1", {"v": 2})

    db2 = FlatGraphDB(db.root)
    assert db2.get_node("a/A1") is None
    assert db2.get_node("b/B1") is None
    assert _no_tx_artifacts(db.root)


def test_crash_after_marker_rolls_forward(db, monkeypatch):
    """Process dies after the durable commit marker but before the apply step."""
    def fail_apply(marker_path, marker):
        raise RuntimeError("simulated crash after commit point")

    monkeypatch.setattr(db, "_apply_tx_marker", fail_apply)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.create_node("a", "A1", {"v": 1})
            db.create_node("b", "B1", {"v": 2})

    db2 = FlatGraphDB(db.root)
    assert db2.get_node("a/A1") == {"v": 1}
    assert db2.get_node("b/B1") == {"v": 2}
    assert _no_tx_artifacts(db.root)


def test_crash_before_marker_does_not_touch_existing_data(db, monkeypatch):
    """A rolled-back transaction must not partially mutate pre-existing data."""
    db.create_node("a", "A1", {"v": "before"})
    orig = db._save_json_atomic

    def fail_on_marker(path, data):
        if os.path.basename(path).startswith("_tx_"):
            raise RuntimeError("simulated crash before commit marker")
        return orig(path, data)

    monkeypatch.setattr(db, "_save_json_atomic", fail_on_marker)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.update_node("a", "A1", {"v": "during"})
            db.create_node("b", "B1", {"v": 1})

    db2 = FlatGraphDB(db.root)
    assert db2.get_node("a/A1") == {"v": "before"}
    assert db2.get_node("b/B1") is None
    assert _no_tx_artifacts(db.root)
