"""
Durability of the atomic write path.

True power-loss durability cannot be unit-tested without a VM / power-cut
harness — these tests cover the observable contract instead: writes land
correctly, no .tmp scratch files leak into the store, and the directory
fsync helper is safe to call.
"""
import os


def _all_files(root):
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            out.append(os.path.join(dirpath, name))
    return out


def test_writes_leave_no_tmp_scratch_files(db):
    db.create_node("book", "B1", {"title": "X"})
    db.create_node("author", "A1", {"name": "Y"})
    db.create_edge("book/B1", "author/A1", "written_by")
    db.update_node("book", "B1", {"title": "Z"})
    db.find_nodes("book", {"title": "z"})  # builds + persists an index
    leftovers = [f for f in _all_files(db.root) if f.endswith(".tmp")]
    assert leftovers == []


def test_transaction_commit_leaves_no_tmp_scratch_files(db):
    with db.transaction():
        for i in range(5):
            db.create_node("book", f"B{i}", {"n": i})
    leftovers = [f for f in _all_files(db.root) if f.endswith(".tmp")]
    assert leftovers == []


def test_gc_leaves_no_tmp_scratch_files(tmp_path):
    from flatgraph import FlatGraphDB, MaintenanceEngine
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {})
    db.soft_delete("book", "B1")
    MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    leftovers = [f for f in _all_files(str(tmp_path)) if f.endswith(".tmp")]
    assert leftovers == []


def test_data_survives_reopen(tmp_path):
    """The durable write path must still produce a file a fresh handle can read."""
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"title": "persisted"})
    del db
    db2 = FlatGraphDB(str(tmp_path))
    assert db2.get_node("book/B1") == {"title": "persisted"}


def test_fsync_dir_is_safe_on_normal_directory(db):
    # Should not raise on a real directory...
    db._fsync_dir(db.dirs["nodes"])
    # ...and should silently no-op on a path that cannot be opened.
    db._fsync_dir(os.path.join(db.root, "does", "not", "exist"))
