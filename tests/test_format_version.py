"""
Store format version: stamped into datenbank/_meta.json on open and verified
there, so a store written by a newer build is rejected rather than misread.
"""
import json
import os

import pytest

from flatgraph import (
    FlatGraphDB,
    FlatGraphError,
    UnsupportedFormatError,
    _FORMAT_VERSION,
)


def _meta_path(root):
    return os.path.join(root, "datenbank", "_meta.json")


def test_fresh_store_is_stamped_with_current_version(tmp_path):
    FlatGraphDB(str(tmp_path))
    meta = json.load(open(_meta_path(tmp_path)))
    assert meta["format_version"] == _FORMAT_VERSION


def test_unversioned_store_is_upgraded_in_place(tmp_path):
    """A _meta.json from before versioning gets stamped, revisions preserved."""
    FlatGraphDB(str(tmp_path)).create_node("book", "B1", {})
    # Simulate a pre-versioning meta file: revisions present, no format_version.
    meta = json.load(open(_meta_path(tmp_path)))
    revisions = meta["revisions"]
    del meta["format_version"]
    json.dump(meta, open(_meta_path(tmp_path), "w"))

    db = FlatGraphDB(str(tmp_path))
    stamped = json.load(open(_meta_path(tmp_path)))
    assert stamped["format_version"] == _FORMAT_VERSION
    assert stamped["revisions"] == revisions
    assert db.get_node("book/B1") == {}


def test_store_from_a_newer_build_is_rejected(tmp_path):
    FlatGraphDB(str(tmp_path))
    meta = json.load(open(_meta_path(tmp_path)))
    meta["format_version"] = _FORMAT_VERSION + 1
    json.dump(meta, open(_meta_path(tmp_path), "w"))

    with pytest.raises(UnsupportedFormatError) as excinfo:
        FlatGraphDB(str(tmp_path))
    assert isinstance(excinfo.value, FlatGraphError)
    assert isinstance(excinfo.value, RuntimeError)


def test_reopening_a_current_store_keeps_the_version(tmp_path):
    FlatGraphDB(str(tmp_path)).create_node("book", "B1", {})
    FlatGraphDB(str(tmp_path)).create_node("book", "B2", {})
    meta = json.load(open(_meta_path(tmp_path)))
    assert meta["format_version"] == _FORMAT_VERSION
    assert set(meta["revisions"]) == {"book"}
