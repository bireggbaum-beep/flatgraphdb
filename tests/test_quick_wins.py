"""
Quick wins from the first hardening round:
  - collect_related strict (default) vs include_intermediate
  - restore_node honest return values
"""


# ---------------------------------------------------------------------------
# collect_related
# ---------------------------------------------------------------------------

def test_collect_related_empty_path_returns_empty(db):
    db.create_node("a", "A1", {})
    assert db.collect_related("a/A1", []) == []


def test_collect_related_strict_follows_chain_level_by_level(db):
    # A1 --follows--> A2 --written_by--> B2
    # A1 --written_by--> B1   (direct shortcut from start node)
    for col, nid in [("author", "A1"), ("author", "A2"),
                     ("book", "B1"), ("book", "B2")]:
        db.create_node(col, nid, {})
    db.create_edge("author/A1", "author/A2", "follows")
    db.create_edge("author/A2", "book/B2",    "written_by")
    db.create_edge("author/A1", "book/B1",    "written_by")  # shortcut

    strict = db.collect_related("author/A1", ["follows", "written_by"])
    # Only B2 — the shortcut B1 must NOT leak in strict mode.
    assert strict == ["book/B2"]


def test_collect_related_include_intermediate_unions_each_level(db):
    for col, nid in [("author", "A1"), ("author", "A2"),
                     ("book", "B1"), ("book", "B2")]:
        db.create_node(col, nid, {})
    db.create_edge("author/A1", "author/A2", "follows")
    db.create_edge("author/A2", "book/B2",    "written_by")
    db.create_edge("author/A1", "book/B1",    "written_by")  # shortcut

    loose = db.collect_related(
        "author/A1", ["follows", "written_by"], include_intermediate=True,
    )
    assert loose == ["book/B1", "book/B2"]


def test_collect_related_returns_sorted_no_duplicates(db):
    db.create_node("a", "A1", {})
    db.create_node("b", "B1", {})
    db.create_node("b", "B2", {})
    db.create_edge("a/A1", "b/B2", "rel")
    db.create_edge("a/A1", "b/B1", "rel")
    db.create_edge("a/A1", "b/B2", "rel")  # duplicate edge to same target

    out = db.collect_related("a/A1", ["rel"])
    assert out == ["b/B1", "b/B2"]


# ---------------------------------------------------------------------------
# restore_node
# ---------------------------------------------------------------------------

def test_restore_node_missing_returns_false(db):
    assert db.restore_node("book", "nope") is False


def test_restore_node_not_deleted_returns_false(db):
    db.create_node("book", "B1", {})
    assert db.restore_node("book", "B1") is False


def test_restore_node_actually_deleted_returns_true(db):
    db.create_node("book", "B1", {})
    db.soft_delete("book", "B1")
    assert db.restore_node("book", "B1") is True
    assert db.get_node("book/B1") == {}


def test_restore_node_idempotent_after_first_restore(db):
    db.create_node("book", "B1", {})
    db.soft_delete("book", "B1")
    db.restore_node("book", "B1")
    # second call: nothing to restore anymore
    assert db.restore_node("book", "B1") is False


def test_restore_node_no_audit_or_webhook_on_noop(tmp_path):
    """When restore_node is a no-op, no audit entry should be written."""
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path), audit=True)
    db.create_node("book", "B1", {})
    audit_before = dict(db._cache["nodes"].get("_audit_log", {}))
    # Not soft-deleted → restore is a no-op → no audit entry
    db.restore_node("book", "B1")
    audit_after = dict(db._cache["nodes"].get("_audit_log", {}))
    # The "restore" action should not have produced a new entry
    restore_entries = [e for e in audit_after.values() if e.get("action") == "restore"]
    assert restore_entries == []
    # Sanity: missing-node case also produces no entry
    db.restore_node("book", "MISSING")
    audit_after2 = dict(db._cache["nodes"].get("_audit_log", {}))
    assert audit_after2 == audit_after
