"""
Collection revision counters + index invalidation.

Regression coverage for the bug that the previous id-checksum did not catch:
a field-value change with an unchanged id set must invalidate a persisted
field index across a fresh process open.
"""
import json
import os


def _meta_path(root):
    return os.path.join(root, "datenbank", "_meta.json")


def _idx_path(root, collection):
    return os.path.join(root, "datenbank", "index", f"{collection}.json")


def test_create_bumps_revision(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {})
    meta = json.load(open(_meta_path(tmp_path)))
    assert meta["revisions"]["book"] == 1


def test_update_bumps_revision_even_without_id_change(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"title": "Foo"})
    rev1 = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    db.update_node("book", "B1", {"title": "Bar"})
    rev2 = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    assert rev2 > rev1


def test_field_index_stores_revision_marker(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"title": "Foo"})
    db.find_nodes("book", {"title": "foo"})  # forces index build + persist
    idx = json.load(open(_idx_path(tmp_path, "book")))
    expected = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    assert idx["_meta"]["revision"] == expected


def test_stale_disk_index_is_discarded_on_fresh_open(tmp_path):
    """
    Regression: the previous checksum-over-ids could not detect a field-value
    change. A fresh process must rebuild the index, not serve stale matches.
    """
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"title": "Foo"})
    db.find_nodes("book", {"title": "foo"})  # persist index@rev=1
    db.update_node("book", "B1", {"title": "Bar"})  # rev moves to 2
    del db

    # Fresh handle = simulated new process / cold cache
    db2 = FlatGraphDB(str(tmp_path))
    assert db2.find_nodes("book", {"title": "foo"}) == {}
    assert "B1" in db2.find_nodes("book", {"title": "bar"})


def test_revision_survives_process_restart(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {})
    db.update_node("book", "B1", {"x": 1})
    rev_before = db._collection_revision("book")
    del db
    db2 = FlatGraphDB(str(tmp_path))
    assert db2._collection_revision("book") == rev_before


def test_transaction_commit_bumps_once_per_collection(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B0", {})
    prev = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    with db.transaction():
        db.create_node("book", "B1", {})
        db.update_node("book", "B0", {"x": 1})
        db.create_node("book", "B2", {})
    after = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    assert after == prev + 1


def test_gc_compaction_bumps_revision(tmp_path):
    from flatgraph import FlatGraphDB, MaintenanceEngine
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {})
    db.soft_delete("book", "B1")
    rev_before = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    rev_after = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    assert rev_after > rev_before


def test_soft_delete_and_restore_each_bump(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {})
    r0 = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    db.soft_delete("book", "B1")
    r1 = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    db.restore_node("book", "B1")
    r2 = json.load(open(_meta_path(tmp_path)))["revisions"]["book"]
    assert r1 == r0 + 1
    assert r2 == r1 + 1
