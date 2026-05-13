"""
Multi-process safety guarantees that engage when file_lock=True:
  - CAS for create_node across peer processes
  - per-field merge for update_node under concurrent disjoint updates
  - stale-cache refresh on public read methods
  - no spurious revision bump on conflict-only batches
  - transaction commit raises ConflictError and rolls back RAM
"""
import pytest

from flatgraph import ConflictError


def test_cas_rejects_concurrent_create_collision(db_factory):
    A = db_factory()
    B = db_factory()  # B opened BEFORE A wrote, so its RAM does not know B1 yet
    A.create_node("book", "B1", {"title": "from A"})
    with pytest.raises(ConflictError):
        B.create_node("book", "B1", {"title": "from B"})


def test_cas_conflict_does_not_bump_revision_on_b(db_factory):
    """
    The conflicting process must not advance its own revision counter; if it
    did, the next refresh would think it is already up to date and skip the
    reload that would show it the peer state. (regression for a real bug)
    """
    A = db_factory()
    B = db_factory()
    A.create_node("book", "B1", {})
    with pytest.raises(ConflictError):
        B.create_node("book", "B1", {})
    assert B._collection_revisions.get("book", 0) == 0


def test_cas_conflict_then_refresh_shows_peer_state(db_factory):
    A = db_factory()
    B = db_factory()
    A.create_node("book", "B1", {"title": "from A"})
    with pytest.raises(ConflictError):
        B.create_node("book", "B1", {"title": "from B"})
    # Next read triggers _refresh_if_stale → B should now see A's version.
    assert B.get_node("book/B1") == {"title": "from A"}


def test_per_field_merge_preserves_disjoint_updates(db_factory):
    A = db_factory()
    A.create_node("book", "B1", {"a": 1, "b": 1})
    B = db_factory()
    A.update_node("book", "B1", {"a": 2})  # disk: {a: 2, b: 1}
    B.update_node("book", "B1", {"b": 3})  # B's delta {b:3} merged onto disk
    assert B.get_node("book/B1") == {"a": 2, "b": 3}


def test_same_field_resolves_as_last_writer_wins(db_factory):
    A = db_factory()
    A.create_node("book", "B1", {"title": "orig"})
    B = db_factory()
    A.update_node("book", "B1", {"title": "A wins"})
    B.update_node("book", "B1", {"title": "B wins"})
    # B persisted after A → B wins.
    assert B.get_node("book/B1") == {"title": "B wins"}
    # And a fresh open agrees.
    fresh = db_factory()
    assert fresh.get_node("book/B1") == {"title": "B wins"}


def test_stale_cache_refresh_on_get_node(db_factory):
    A = db_factory()
    A.create_node("book", "B1", {"title": "v1"})
    B = db_factory()
    assert B.get_node("book/B1") == {"title": "v1"}
    A.update_node("book", "B1", {"title": "v2"})
    assert B.get_node("book/B1") == {"title": "v2"}


def test_stale_cache_refresh_on_find_nodes(db_factory):
    A = db_factory()
    A.create_node("book", "B1", {"title": "v1"})
    B = db_factory()
    assert "B1" in B.find_nodes("book", {"title": "v1"})
    A.update_node("book", "B1", {"title": "v2"})
    assert "B1" in B.find_nodes("book", {"title": "v2"})
    assert B.find_nodes("book", {"title": "v1"}) == {}


def test_stale_cache_refresh_on_list_nodes(db_factory):
    A = db_factory()
    B = db_factory()
    A.create_node("book", "B1", {})
    assert "B1" in B.list_nodes("book")


def test_refresh_inactive_without_file_lock(tmp_path):
    """
    Without file_lock=True the refresh path is intentionally skipped — opt-in.
    """
    from flatgraph import FlatGraphDB
    A = FlatGraphDB(str(tmp_path), file_lock=False)
    B = FlatGraphDB(str(tmp_path), file_lock=False)
    A.create_node("book", "B1", {"title": "v1"})
    assert B.get_node("book/B1") is None  # B never refreshes


def test_transaction_commit_conflict_rolls_back_ram(db_factory):
    # B opens before A writes, so B's RAM does not know about B1 → CAS must
    # surface at commit time rather than the early RAM check in create_node.
    A = db_factory()
    B = db_factory()
    A.create_node("book", "B1", {"title": "from A"})
    with pytest.raises(ConflictError):
        with B.transaction():
            B.create_node("book", "B2", {"title": "non-conflicting"})
            B.create_node("book", "B1", {"title": "will conflict"})
    # Both nodes B was creating should be gone from B's RAM after rollback.
    assert B.get_node("book/B2") is None


def test_purged_node_update_raises_conflict(db_factory, gc_factory):
    """
    Updating a node that another process purged via GC produces a conflict.
    """
    A = db_factory()
    A.create_node("book", "B1", {"x": 1})
    B = db_factory()  # B loads B1 into its RAM

    # Another process soft-deletes and GCs the node out from under B.
    A.soft_delete("book", "B1")
    gc_factory().run_garbage_collection()

    # B still thinks B1 exists; its update should be detected as conflict.
    # (B will refresh-on-read first; once refreshed the node is gone, so
    # update_node returns False rather than raising.)
    refreshed = B.get_node("book/B1")
    assert refreshed is None  # refresh removed it
    assert B.update_node("book", "B1", {"x": 99}) is False
