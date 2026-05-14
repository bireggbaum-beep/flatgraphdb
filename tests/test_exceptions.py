"""
Exception hierarchy: every concrete error inherits from FlatGraphError AND a
matching stdlib base, so existing except clauses keep working.
"""
import pytest

from flatgraph import (
    FlatGraphError,
    NodeExistsError,
    NodeNotFoundError,
    SchemaValidationError,
    SchemaTypeError,
    EdgeConstraintError,
    CorruptStoreError,
    UnsupportedFormatError,
    TransactionError,
    ConflictError,
)


@pytest.mark.parametrize("exc,stdlib", [
    (NodeExistsError,        KeyError),
    (NodeNotFoundError,      KeyError),
    (SchemaValidationError,  ValueError),
    (SchemaTypeError,        TypeError),
    (EdgeConstraintError,    ValueError),
    (CorruptStoreError,      RuntimeError),
    (UnsupportedFormatError, RuntimeError),
])
def test_concrete_exceptions_inherit_from_flatgraph_and_stdlib(exc, stdlib):
    assert issubclass(exc, FlatGraphError)
    assert issubclass(exc, stdlib)


@pytest.mark.parametrize("exc", [TransactionError, ConflictError])
def test_transaction_and_conflict_inherit_only_from_flatgraph(exc):
    assert issubclass(exc, FlatGraphError)


def test_create_node_collision_raises_node_exists_and_is_keyerror(db):
    db.create_node("book", "B1", {})
    with pytest.raises(NodeExistsError) as excinfo:
        db.create_node("book", "B1", {})
    assert isinstance(excinfo.value, KeyError)


def test_missing_required_field_raises_schema_validation_and_is_valueerror(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path), schemas={"book": {"title": str}})
    with pytest.raises(SchemaValidationError) as excinfo:
        db.create_node("book", "B1", {})
    assert isinstance(excinfo.value, ValueError)


def test_wrong_type_raises_schema_type_and_is_typeerror(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path), schemas={"book": {"title": str}})
    with pytest.raises(SchemaTypeError) as excinfo:
        db.create_node("book", "B1", {"title": 42})
    assert isinstance(excinfo.value, TypeError)


def test_edge_constraint_violation_raises_edge_constraint_and_is_valueerror(tmp_path):
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path), edge_constraints={"written_by": [("book", "author")]})
    db.create_node("book", "B1", {})
    db.create_node("book", "B2", {})  # wrong target collection for the constraint
    with pytest.raises(EdgeConstraintError) as excinfo:
        db.create_edge("book/B1", "book/B2", "written_by")
    assert isinstance(excinfo.value, ValueError)


def test_missing_node_for_edge_raises_node_not_found_and_is_keyerror(db):
    with pytest.raises(NodeNotFoundError) as excinfo:
        db.create_edge("book/missing", "book/also-missing", "rel")
    assert isinstance(excinfo.value, KeyError)


def test_corrupt_json_file_raises_corrupt_store(tmp_path):
    """A malformed JSON file under nodes/ must raise CorruptStoreError on read."""
    from flatgraph import FlatGraphDB
    db = FlatGraphDB(str(tmp_path))
    db.create_node("book", "B1", {"title": "x"})
    # Trash the temp file with invalid JSON
    temp_path = db._temp_file("book")
    with open(temp_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    with pytest.raises(CorruptStoreError) as excinfo:
        db._load_json_from_disk(temp_path)
    assert isinstance(excinfo.value, RuntimeError)
