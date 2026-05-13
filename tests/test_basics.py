"""
Golden-path coverage for the core API. These tests don't probe edge cases —
they make sure that the documented happy path keeps working after refactors.
"""
import pytest


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def test_create_and_get_node(db):
    ref = db.create_node("book", "B1", {"title": "X"})
    assert ref == "book/B1"
    assert db.get_node("book/B1") == {"title": "X"}


def test_get_node_returns_deep_copy_by_default(db):
    db.create_node("book", "B1", {"tags": ["a"]})
    node = db.get_node("book/B1")
    node["tags"].append("b")
    # Mutation must not have leaked into the cache.
    assert db.get_node("book/B1") == {"tags": ["a"]}


def test_get_node_readonly_returns_live_reference(db):
    db.create_node("book", "B1", {"tags": ["a"]})
    node = db.get_node("book/B1", readonly=True)
    assert node is db._cache["nodes"]["book"]["B1"]


def test_get_node_invalid_ref(db):
    assert db.get_node("not_a_ref") is None
    assert db.get_node("book/missing") is None


def test_update_node_merges_fields(db):
    db.create_node("book", "B1", {"title": "X", "year": 2000})
    db.update_node("book", "B1", {"year": 2024})
    assert db.get_node("book/B1") == {"title": "X", "year": 2024}


def test_update_node_returns_false_for_missing(db):
    assert db.update_node("book", "missing", {"x": 1}) is False


def test_list_nodes_excludes_soft_deleted_by_default(db):
    db.create_node("book", "B1", {})
    db.create_node("book", "B2", {})
    db.soft_delete("book", "B2")
    assert set(db.list_nodes("book")) == {"B1"}
    assert set(db.list_nodes("book", include_deleted=True)) == {"B1", "B2"}


def test_list_collections_filters_internal(db):
    from flatgraph import FlatGraphDB
    db_audit = FlatGraphDB(str(db.root), audit=True)
    db_audit.create_node("book", "B1", {})
    # _audit_log must not appear in list_collections
    assert "_audit_log" not in db_audit.list_collections()
    assert "book" in db_audit.list_collections()


def test_next_id_with_prefix_and_padding(db):
    assert db.next_id("book", prefix="B-", padding=4) == "B-0001"
    db.create_node("book", "B-0001", {})
    db.create_node("book", "B-0007", {})
    assert db.next_id("book", prefix="B-", padding=4) == "B-0008"


# ---------------------------------------------------------------------------
# find_nodes
# ---------------------------------------------------------------------------

def test_find_nodes_substring_case_insensitive(db):
    db.create_node("book", "B1", {"title": "The Trial"})
    db.create_node("book", "B2", {"title": "Other"})
    hits = db.find_nodes("book", {"title": "trial"})
    assert set(hits) == {"B1"}


def test_find_nodes_wildcard(db):
    db.create_node("book", "B1", {"title": "The Trial"})
    db.create_node("book", "B2", {"title": "The Castle"})
    db.create_node("book", "B3", {"title": "Other"})
    hits = db.find_nodes("book", {"title": "the*"})
    assert set(hits) == {"B1", "B2"}


def test_find_nodes_predicate(db):
    db.create_node("book", "B1", {"year": 1925})
    db.create_node("book", "B2", {"year": 1999})
    hits = db.find_nodes("book", {"year": lambda y: y and y > 1950})
    assert set(hits) == {"B2"}


def test_find_nodes_multiple_fields_are_anded(db):
    db.create_node("book", "B1", {"title": "Foo", "status": "published"})
    db.create_node("book", "B2", {"title": "Foo", "status": "draft"})
    hits = db.find_nodes("book", {"title": "foo", "status": "published"})
    assert set(hits) == {"B1"}


def test_find_nodes_empty_match_returns_all(db):
    db.create_node("book", "B1", {})
    db.create_node("book", "B2", {})
    assert set(db.find_nodes("book", {})) == {"B1", "B2"}


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------

def test_create_and_get_edge(db):
    db.create_node("book", "B1", {})
    db.create_node("author", "A1", {})
    eid = db.create_edge("book/B1", "author/A1", "written_by")
    edge = db.get_edge(eid)
    assert edge["source"] == "book/B1"
    assert edge["target"] == "author/A1"
    assert edge["type"] == "written_by"


def test_delete_edge(db):
    db.create_node("a", "A1", {})
    db.create_node("b", "B1", {})
    eid = db.create_edge("a/A1", "b/B1", "rel")
    assert db.delete_edge(eid) is True
    assert db.get_edge(eid) is None
    assert db.delete_edge(eid) is False  # idempotent on missing


def test_get_connected_out_and_in(db):
    db.create_node("a", "A1", {})
    db.create_node("b", "B1", {})
    db.create_edge("a/A1", "b/B1", "rel")
    assert db.get_connected("a/A1", direction="out") == ["b/B1"]
    assert db.get_connected("b/B1", direction="in") == ["a/A1"]


def test_get_connected_filters_soft_deleted_targets(db):
    db.create_node("a", "A1", {})
    db.create_node("b", "B1", {})
    db.create_edge("a/A1", "b/B1", "rel")
    db.soft_delete("b", "B1")
    assert db.get_connected("a/A1", "out") == []
    assert db.get_connected("a/A1", "out", include_deleted=True) == ["b/B1"]


def test_traverse_multi_hop_bfs(db):
    # A1 -> A2 -> A3
    for nid in ["A1", "A2", "A3"]:
        db.create_node("n", nid, {})
    db.create_edge("n/A1", "n/A2", "rel")
    db.create_edge("n/A2", "n/A3", "rel")
    out = db.traverse("n/A1", rel_type="rel")
    assert out == ["n/A2", "n/A3"]


def test_traverse_respects_max_depth(db):
    for nid in ["A1", "A2", "A3", "A4"]:
        db.create_node("n", nid, {})
    db.create_edge("n/A1", "n/A2", "rel")
    db.create_edge("n/A2", "n/A3", "rel")
    db.create_edge("n/A3", "n/A4", "rel")
    assert db.traverse("n/A1", "rel", max_depth=2) == ["n/A2", "n/A3"]


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def test_transaction_commits_all_or_nothing(db):
    with db.transaction():
        db.create_node("book", "B1", {})
        db.create_node("book", "B2", {})
    assert set(db.list_nodes("book")) == {"B1", "B2"}


def test_transaction_rollback_on_exception(db):
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.create_node("book", "B1", {})
            raise RuntimeError("boom")
    assert db.list_nodes("book") == {}


def test_nested_transactions_join_outer(db):
    with db.transaction():
        db.create_node("book", "B1", {})
        with db.transaction():
            db.create_node("book", "B2", {})
        # No flush yet — still inside outer
        assert db._transaction_depth == 1
    assert set(db.list_nodes("book")) == {"B1", "B2"}


# ---------------------------------------------------------------------------
# Soft-delete + GC
# ---------------------------------------------------------------------------

def test_soft_delete_hides_node_but_keeps_data(db):
    db.create_node("book", "B1", {"title": "X"})
    db.soft_delete("book", "B1")
    assert db.get_node("book/B1") is None
    raw = db.get_node_raw("book/B1")
    assert raw["title"] == "X"
    assert "_deletion_flag" in raw


def test_gc_removes_soft_deleted_nodes_and_their_edges(tmp_path):
    from flatgraph import FlatGraphDB, MaintenanceEngine
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {})
    db.create_node("author", "A1", {})
    db.create_edge("book/B1", "author/A1", "written_by")
    db.soft_delete("book", "B1")

    stats = MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    assert stats["nodes_purged"] == 1
    assert stats["edges_removed"] == 1

    # Re-open: node and edge are truly gone
    db2 = FlatGraphDB(str(tmp_path))
    assert db2.get_node("book/B1") is None
    assert db2.get_node_raw("book/B1") is None
    assert db2.list_edges() == {}


def test_gc_cascade_delete(tmp_path):
    """Edges with cascade_delete=True drag their target down."""
    from flatgraph import FlatGraphDB, MaintenanceEngine
    db = FlatGraphDB(str(tmp_path))
    db.create_node("process", "P1", {})
    db.create_node("log", "L1", {})
    db.create_edge("process/P1", "log/L1", "has_log", cascade_delete=True)
    db.soft_delete("process", "P1")

    MaintenanceEngine(str(tmp_path)).run_garbage_collection()
    db2 = FlatGraphDB(str(tmp_path))
    assert db2.get_node_raw("process/P1") is None
    assert db2.get_node_raw("log/L1") is None
