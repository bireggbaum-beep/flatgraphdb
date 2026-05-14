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


def test_non_durable_write_is_still_atomic_and_correct(db):
    """durable=False skips fsync but must still produce a complete, valid file."""
    import glob
    import json
    path = os.path.join(db.root, "scratch.json")
    db._save_json_atomic(path, {"a": 1, "b": [2, 3]}, durable=False)
    assert json.load(open(path)) == {"a": 1, "b": [2, 3]}
    # No .tmp scratch left behind (temp names are unique: <path>.<uuid>.tmp).
    assert glob.glob(path + ".*.tmp") == []


def test_index_writes_are_not_fsync_durable(db, monkeypatch):
    """
    Index persistence is derived data — it must go through the non-durable
    path, so a find_nodes() call triggers no fsync of the index file.
    """
    db.create_node("book", "B1", {"title": "Trial"})
    calls = {"fsync": 0}
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: calls.__setitem__("fsync", calls["fsync"] + 1) or real_fsync(fd))
    db.find_nodes("book", {"title": "trial"})  # builds + persists the index
    assert calls["fsync"] == 0
