"""
Longtext offloading: string fields over longtext_threshold are written to
content-addressed blobs under vault_text/ and the node keeps a reference.

Content-addressing (blob named by sha256 of its content) makes the vault_text
store immutable, which is what makes offloading transaction-safe: an update
writes a new blob and never overwrites an old one, so a rolled-back write can
only ever leave an orphan for the GC — never a corrupted value.
"""
import hashlib
import os

import pytest

from flatgraph import FlatGraphDB, MaintenanceEngine


@pytest.fixture
def ldb(tmp_path):
    """A db with longtext offloading on at a low threshold."""
    return FlatGraphDB(str(tmp_path), longtext_threshold=10)


def _blobs(db):
    return sorted(os.listdir(db.dirs["vault_text"]))


# ---------------------------------------------------------------------------
# Offload / resolve basics
# ---------------------------------------------------------------------------

def test_short_fields_stay_inline_long_fields_offload(ldb):
    ldb.create_node("doc", "D1", {"short": "hi", "long": "x" * 50})
    raw = ldb.get_node("doc/D1")
    assert raw["short"] == "hi"
    assert raw["long"].startswith("@vault_text/")
    # get_node_full resolves the reference back to the text
    assert ldb.get_node_full("doc/D1") == {"short": "hi", "long": "x" * 50}


def test_offloading_off_by_default(tmp_path):
    db = FlatGraphDB(str(tmp_path))  # no longtext_threshold
    db.create_node("doc", "D1", {"long": "x" * 10_000})
    assert db.get_node("doc/D1")["long"] == "x" * 10_000
    assert not os.path.isdir(db.dirs["vault_text"]) or _blobs(db) == []


def test_blob_is_named_by_content_hash(ldb):
    content = "the quick brown fox " * 5
    ldb.create_node("doc", "D1", {"body": content})
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest() + ".txt"
    assert _blobs(ldb) == [expected]


def test_blob_survives_reopen(tmp_path):
    db = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    db.create_node("doc", "D1", {"body": "y" * 100})
    del db
    db2 = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    assert db2.get_node_full("doc/D1") == {"body": "y" * 100}


# ---------------------------------------------------------------------------
# Content-addressing: dedup + immutability
# ---------------------------------------------------------------------------

def test_identical_content_is_deduplicated(ldb):
    shared = "shared long content here" * 3
    ldb.create_node("doc", "D1", {"body": shared})
    ldb.create_node("doc", "D2", {"body": shared})
    assert len(_blobs(ldb)) == 1
    assert ldb.get_node_full("doc/D1") == ldb.get_node_full("doc/D2")


def test_update_writes_new_blob_and_leaves_old_one_untouched(ldb):
    ldb.create_node("doc", "D1", {"body": "first long version aaaa"})
    first_blobs = _blobs(ldb)
    assert len(first_blobs) == 1

    ldb.update_node("doc", "D1", {"body": "second long version bbbb"})
    # The old blob is immutable — still on disk — and a new one was written.
    assert len(_blobs(ldb)) == 2
    assert set(first_blobs).issubset(set(_blobs(ldb)))
    assert ldb.get_node_full("doc/D1") == {"body": "second long version bbbb"}


def test_resaving_unchanged_longtext_writes_no_new_blob(ldb):
    ldb.create_node("doc", "D1", {"body": "unchanged long body text"})
    before = _blobs(ldb)
    ldb.update_node("doc", "D1", {"body": "unchanged long body text"})
    assert _blobs(ldb) == before  # same content hash → skip-if-exists


# ---------------------------------------------------------------------------
# Transaction safety — the reason for content-addressing
# ---------------------------------------------------------------------------

def test_rolled_back_update_leaves_original_longtext_intact(ldb):
    """
    Regression: with the old mutable-path scheme an update overwrote the blob
    in place, so a transaction rollback could not undo it and silently served
    the new content. Content-addressing makes this impossible.
    """
    ldb.create_node("doc", "D1", {"body": "ORIGINAL long content here"})
    original = ldb.get_node_full("doc/D1")

    with pytest.raises(RuntimeError):
        with ldb.transaction():
            ldb.update_node("doc", "D1", {"body": "REPLACEMENT long content"})
            raise RuntimeError("boom")

    # The node reverts to the original reference, and the original blob was
    # never touched — so the original content is still served.
    assert ldb.get_node_full("doc/D1") == original


def test_rolled_back_create_leaves_orphan_blob_for_gc(tmp_path):
    db = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.create_node("doc", "D1", {"body": "z" * 100})
            raise RuntimeError("boom")

    # The node rolled back, but the blob was written eagerly → orphan on disk.
    assert db.get_node("doc/D1") is None
    assert len(_blobs(db)) == 1

    # The GC's existing orphan sweep collects it.
    MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    assert _blobs(db) == []


# ---------------------------------------------------------------------------
# GC orphan collection
# ---------------------------------------------------------------------------

def test_gc_collects_superseded_blob_keeps_referenced_one(tmp_path):
    db = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    db.create_node("doc", "D1", {"body": "first long version aaaa"})
    db.update_node("doc", "D1", {"body": "second long version bbbb"})
    assert len(_blobs(db)) == 2  # old (orphan) + new (referenced)

    MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    assert len(_blobs(db)) == 1

    # The live node still resolves after the sweep.
    db2 = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    assert db2.get_node_full("doc/D1") == {"body": "second long version bbbb"}


def test_gc_keeps_blob_shared_by_multiple_nodes(tmp_path):
    db = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    shared = "shared long content here" * 3
    db.create_node("doc", "D1", {"body": shared})
    db.create_node("doc", "D2", {"body": shared})
    db.soft_delete("doc", "D1")  # D2 still references the shared blob

    MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    assert len(_blobs(db)) == 1
    db2 = FlatGraphDB(str(tmp_path), longtext_threshold=10)
    assert db2.get_node_full("doc/D2") == {"body": shared}


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------

def test_offload_leaves_no_tmp_scratch(ldb):
    ldb.create_node("doc", "D1", {"body": "a" * 100})
    ldb.update_node("doc", "D1", {"body": "b" * 100})
    leftovers = [f for f in os.listdir(ldb.dirs["vault_text"]) if f.endswith(".tmp")]
    assert leftovers == []
