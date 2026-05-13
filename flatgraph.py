"""
=============================================================================
FlatGraphDB — Lightweight, file-based Graph Database for Python
=============================================================================
A single-module embedded graph database. No external dependencies.

Architecture:
  root_dir/
    datenbank/
      nodes/      -> one JSON file per collection
      edges/      -> one JSON file per edge type
    vault/        -> binary assets (PDFs, images, etc.)
    vault_archive/-> quarantine for deleted assets

Features:
  - Graph model: nodes reference each other only via edges (no direct FK)
  - Soft-delete with a two-phase, idempotent Garbage Collector
  - Cascade-delete on edge: target node dies with source node
  - Atomic writes via temp+rename
  - Optional schema validation per collection
  - RAM cache for fast reads; lazy field index for fast find_nodes()
  - Breadth-first multi-hop traversal
  - Auto-increment ID helper
  - Optional transaction context manager (batch writes + rollback)
  - Optional audit trail (audit=True)
  - Optional webhook hooks (webhooks=[...])
=============================================================================
"""

import contextlib
import copy
import json
import os
import re
import shutil
import sys
import threading
import urllib.request
import uuid
from datetime import datetime, timezone

_RESERVED_EDGE_FIELDS  = {"source", "target", "type", "created_at", "_cascade_delete"}
_INTERNAL_COLLECTIONS  = {"_audit_log"}


# =============================================================================
# EXCEPTIONS
# =============================================================================
# All FlatGraphDB errors inherit from FlatGraphError, so callers can catch the
# whole family with one except clause. Each concrete error also inherits from a
# matching stdlib type (KeyError / ValueError / TypeError / RuntimeError) so
# existing code that catches those continues to work.

class FlatGraphError(Exception):
    """Base class for every FlatGraphDB error."""


class NodeExistsError(FlatGraphError, KeyError):
    """A node with the given id already exists in the target collection."""


class NodeNotFoundError(FlatGraphError, KeyError):
    """A referenced node does not exist (or is soft-deleted)."""


class SchemaValidationError(FlatGraphError, ValueError):
    """Data does not satisfy the declared collection schema."""


class SchemaTypeError(FlatGraphError, TypeError):
    """A schema field is present but has the wrong Python type."""


class EdgeConstraintError(FlatGraphError, ValueError):
    """An edge violates the declared edge_constraints for its rel_type."""


class TransactionError(FlatGraphError):
    """A transaction could not be completed."""


class ConflictError(FlatGraphError):
    """A multi-process write conflict was detected."""


class CorruptStoreError(FlatGraphError, RuntimeError):
    """An on-disk file is unreadable or not valid JSON."""


# =============================================================================
# MAIN ENGINE
# =============================================================================

class FlatGraphDB:
    def __init__(self, root_dir, schemas=None, edge_constraints=None,
                 file_lock=False, audit=False, webhooks=None,
                 longtext_threshold=None):
        """
        Initialize the graph engine.

        :param root_dir:   Root directory of the database (created if it does not exist).
        :param schemas:    Optional dict {collection_name: {field: spec}} for type-checked
                           collections. Three spec forms are supported:
                             str / int / float / bool / list
                               Plain Python type — field is required, value must be that type.
                             {"type": str|list, "options": [...]}
                               Enum — field is required; for str the value must be one of the
                               options; for list every element must be in options.
                             {"type": "link", "target": "collection_name"}
                               Node reference — field is optional; when present the value must
                               be a valid, non-deleted node ref in the target collection.
        :param file_lock:  Enable file-level locking for multi-process safety (fcntl/msvcrt).
        :param audit:      Automatically write audit entries to _audit_log on every write.
        :param webhooks:   List of hook configs fired on node events (non-blocking HTTP POST):
                           [{"url": "https://...", "events": "*", "collections": "*"}]
                           events / collections: "*" = all, or a list e.g. ["create_node"]
        :param edge_constraints: Optional dict {rel_type: [(source_collection, target_collection), ...]}
                                 restricting which collection pairs are valid for each edge type.
                                 Example: {"gehört-zu-plant": [("equipment", "plant"),
                                                               ("software",   "plant")]}
        :param longtext_threshold: If set (int), string fields longer than this many characters
                                   are automatically offloaded to vault_text/ as plain-text files.
                                   The node field stores an "@vault_text/..." reference instead.
                                   Use get_node_full() to resolve references back to full text.
        """
        self.root = root_dir
        self.schemas = schemas or {}
        self.edge_constraints = edge_constraints or {}
        self.file_lock = file_lock
        self.audit = audit
        self.webhooks = webhooks or []
        self.longtext_threshold = longtext_threshold
        self._lock_path = os.path.join(root_dir, ".flatgraph.lock")
        self._audit_writing = False  # prevents recursive audit entries

        self.dirs = {
            "nodes":        os.path.join(root_dir, "datenbank", "nodes"),
            "edges":        os.path.join(root_dir, "datenbank", "edges"),
            "index":        os.path.join(root_dir, "datenbank", "index"),
            "vault":        os.path.join(root_dir, "vault"),
            "vault_archive":os.path.join(root_dir, "vault_archive"),
            "vault_text":   os.path.join(root_dir, "vault_text"),
        }
        for path in self.dirs.values():
            os.makedirs(path, exist_ok=True)

        # RAM cache: edges = {rel_type: {edge_id: edge_data}}
        self._cache = {"nodes": {}, "edges": {}}
        self._edge_type_index = {}   # {edge_id: rel_type} — reverse index, RAM only
        self._index_cache = {}       # {collection: {field: {value_lower: [node_ids]}}}
        self._dirty_index  = set()   # collections that need re-indexing after a write
        self._dirty_nodes  = {}      # {collection: set(node_ids)} — pending temp-file writes
        self._dirty_edges  = set()   # rel_types with pending edge writes
        self._transaction_depth = 0  # >0 = active transaction, writes are buffered
        # RMW delta tracking — required for correct multi-process merges
        self._purged_nodes      = {}  # {collection: set(node_ids)} — permanently deleted this session
        self._edge_additions    = {}  # {rel_type: {edge_id: edge_data}} — edges created this session
        self._edge_deletions    = {}  # {rel_type: set(edge_ids)} — edges deleted this session
        self._pending_creations = {}  # {collection: set(node_ids)} — created in RAM, not yet on disk → CAS at persist
        self._node_updates      = {}  # {collection: {node_id: {field: value}}} — per-node update delta for per-field merge
        self._collection_revisions = {}  # {collection: int} — on-disk revision counter; populated by _initialize_cache
        self._initialize_cache()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _edges_file(self, rel_type):
        safe = rel_type.replace("/", "_").replace("\\", "_")
        return os.path.join(self.dirs["edges"], f"{safe}.json")

    def _persist_edge_type(self, rel_type):
        """Write an edge type to disk — or buffer it if inside a transaction."""
        if self._transaction_depth > 0:
            self._dirty_edges.add(rel_type)
            return
        self._dirty_edges.discard(rel_type)
        self._flush_edge_type(rel_type)

    def _flush_edge_type(self, rel_type):
        """RMW for a single edge file: read current disk state, apply our delta, write back."""
        new     = self._edge_additions.get(rel_type, {})
        deleted = self._edge_deletions.get(rel_type, set())
        if not new and not deleted:
            return
        with self._acquire_lock():
            disk_edges = self._load_json_from_disk(self._edges_file(rel_type))
            for eid in deleted:
                disk_edges.pop(eid, None)
            disk_edges.update(new)
            self._save_json_atomic(self._edges_file(rel_type), disk_edges)
        # Clear deltas only after a successful write
        self._edge_additions.pop(rel_type, None)
        self._edge_deletions.pop(rel_type, None)
        self._cache["edges"][rel_type] = disk_edges

    @staticmethod
    def _translate_legacy_edge(edge):
        """Translate v0.9 German field names to English in-place. Returns the edge."""
        if "quelle" in edge:
            edge["source"]     = edge.pop("quelle")
            edge["target"]     = edge.pop("ziel",  edge.get("target", ""))
            edge["type"]       = edge.pop("typ",   edge.get("type",   "_unknown"))
            edge["created_at"] = edge.pop("erstellt_am", edge.get("created_at", ""))
        return edge

    def _migrate_legacy_edges(self):
        """Migrate old single-file objektlinks.json to per-type files."""
        legacy = os.path.join(self.dirs["edges"], "objektlinks.json")
        if not os.path.exists(legacy):
            return
        all_edges = self._load_json_from_disk(legacy)
        by_type = {}
        for edge_id, edge_data in all_edges.items():
            self._translate_legacy_edge(edge_data)
            t = edge_data.get("type", "_unknown")
            by_type.setdefault(t, {})[edge_id] = edge_data
        for t, edges in by_type.items():
            self._save_json_atomic(self._edges_file(t), edges)
        os.remove(legacy)

    def _initialize_cache(self):
        """Load all JSON files into RAM once. Temp-files are merged on top of the base state."""
        self._collection_revisions = self._load_revisions_from_disk()

        if os.path.exists(self.dirs["nodes"]):
            for filename in os.listdir(self.dirs["nodes"]):
                if filename.endswith("_temp.json") or not filename.endswith(".json"):
                    continue
                collection_name = filename[:-5]
                path = os.path.join(self.dirs["nodes"], filename)
                self._cache["nodes"][collection_name] = self._load_json_from_disk(path)

            # Merge temp-files on top (pending writes from a previous session)
            for filename in os.listdir(self.dirs["nodes"]):
                if not filename.endswith("_temp.json"):
                    continue
                collection_name = filename[: -len("_temp.json")]
                temp_data = self._load_json_from_disk(os.path.join(self.dirs["nodes"], filename))
                self._cache["nodes"].setdefault(collection_name, {}).update(temp_data)

        self._migrate_legacy_edges()

        if os.path.exists(self.dirs["edges"]):
            for filename in os.listdir(self.dirs["edges"]):
                if filename.endswith(".json"):
                    rel_type = filename[:-5]
                    path = os.path.join(self.dirs["edges"], filename)
                    edges = self._load_json_from_disk(path)
                    # Translate any leftover v0.9 German field names
                    for edge in edges.values():
                        self._translate_legacy_edge(edge)
                    self._cache["edges"][rel_type] = edges
                    for edge_id in edges:
                        self._edge_type_index[edge_id] = rel_type

    def _load_json_from_disk(self, filepath):
        """Load a JSON file from disk. Missing file → empty dict. Corrupted file → RuntimeError."""
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise CorruptStoreError(f"File '{filepath}' is not valid JSON: {e}") from e
        except IOError as e:
            raise CorruptStoreError(f"Could not read file '{filepath}': {e}") from e

    def _save_json_atomic(self, filepath, data):
        """Atomic write: write to .tmp first, then os.replace()."""
        temp_file = filepath + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, filepath)

    def _temp_file(self, collection_name):
        return os.path.join(self.dirs["nodes"], f"{collection_name}_temp.json")

    @contextlib.contextmanager
    def _acquire_lock(self):
        """File lock for multi-process safety (only active when file_lock=True)."""
        if not self.file_lock:
            yield
            return
        lock_fh = open(self._lock_path, "a", encoding="utf-8")
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_fh, fcntl.LOCK_UN)
            lock_fh.close()

    def _mark_node_dirty(self, collection_name, node_id):
        self._dirty_nodes.setdefault(collection_name, set()).add(node_id)

    def _persist_collection_unlocked(self, collection_name, dirty_ids):
        """
        Write a batch of dirty node ids to the collection's temp file under the
        already-acquired self._acquire_lock(). Performs:

          - CAS for pending creations: if the id already exists on disk, the node
            is rolled back from RAM and ConflictError is raised. Other dirty ids
            in this batch are still applied (we raise after the save).
          - Per-field merge for updates whose delta is tracked in _node_updates:
            the on-disk version is read inside the lock and our delta layered on
            top, so disjoint-field updates from peer processes survive.
          - Whole-node write for dirty ids without a tracked delta (restore_node,
            GC cascade flag, internal _-only updates).
          - Disk delete for dirty ids that are no longer in the RAM cache.

        Returns the list of (collection, node_id) tuples that conflicted; caller
        decides whether to raise.
        """
        temp_path = self._temp_file(collection_name)
        base_path = os.path.join(self.dirs["nodes"], f"{collection_name}.json")
        col_data  = self._cache["nodes"].get(collection_name, {})

        temp_data = self._load_json_from_disk(temp_path)
        disk_base = self._load_json_from_disk(base_path)
        pending_new  = self._pending_creations.get(collection_name, set())
        node_updates = self._node_updates.get(collection_name, {})

        conflicts = []
        applied = False

        for nid in dirty_ids:
            if nid in pending_new:
                # CAS: must not exist on disk yet.
                if nid in temp_data or nid in disk_base:
                    col_data.pop(nid, None)
                    pending_new.discard(nid)
                    node_updates.pop(nid, None)
                    conflicts.append(nid)
                    continue
                if nid in col_data:
                    temp_data[nid] = col_data[nid]
                    applied = True
            elif nid in col_data:
                delta = node_updates.get(nid)
                if delta is not None:
                    if nid not in temp_data and nid not in disk_base:
                        # Node we were updating got purged by another process.
                        col_data.pop(nid, None)
                        node_updates.pop(nid, None)
                        conflicts.append(nid)
                        continue
                    merged = dict(temp_data.get(nid) or disk_base.get(nid, {}))
                    merged.update(delta)
                    temp_data[nid] = merged
                    col_data[nid] = merged  # sync RAM so subsequent reads see merged state
                    applied = True
                else:
                    temp_data[nid] = col_data[nid]
                    applied = True
            elif nid in temp_data:
                # Node was removed from RAM (e.g. GC purge); reflect on disk.
                temp_data.pop(nid, None)
                applied = True

        if applied:
            self._save_json_atomic(temp_path, temp_data)
            self._bump_revision_unlocked(collection_name)

        # Successfully persisted dirty ids drop out of the pending sets.
        non_conflicted = set(dirty_ids) - set(conflicts)
        pending_new.difference_update(non_conflicted)
        for nid in non_conflicted:
            node_updates.pop(nid, None)
        if not pending_new:
            self._pending_creations.pop(collection_name, None)
        if not node_updates:
            self._node_updates.pop(collection_name, None)

        return conflicts

    def _persist_collection(self, collection_name):
        """Write only changed nodes to the temp-file — or buffer when inside a transaction."""
        dirty_ids = self._dirty_nodes.get(collection_name, set())
        if not dirty_ids:
            return
        if self._transaction_depth > 0:
            return  # buffered until commit
        self._dirty_nodes.pop(collection_name, None)
        with self._acquire_lock():
            conflicts = self._persist_collection_unlocked(collection_name, dirty_ids)
        if conflicts:
            raise ConflictError(
                f"Node id collision in '{collection_name}' — already created by another process: "
                f"{sorted(conflicts)}"
            )

    def _flush_pending_writes(self):
        """Flush all buffered node and edge writes to disk (transaction commit)."""
        all_conflicts = {}
        for collection_name in list(self._dirty_nodes.keys()):
            dirty_ids = self._dirty_nodes.pop(collection_name, set())
            if not dirty_ids:
                continue
            with self._acquire_lock():
                conflicts = self._persist_collection_unlocked(collection_name, dirty_ids)
            if conflicts:
                all_conflicts[collection_name] = sorted(conflicts)
        for rel_type in list(self._dirty_edges):
            self._flush_edge_type(rel_type)
        self._dirty_edges.clear()
        if all_conflicts:
            raise ConflictError(
                f"Transaction commit hit id collisions from peer processes: {all_conflicts}"
            )

    def flush(self):
        """Write all buffered writes to disk immediately. Useful outside transaction()."""
        self._flush_pending_writes()

    @contextlib.contextmanager
    def transaction(self):
        """
        Atomic transaction: all writes are buffered in RAM and flushed to disk as a
        batch on exit. On exception: full rollback. Nested transactions join the outer one.
        """
        if self._transaction_depth > 0:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        # Snapshot for rollback
        snap_nodes      = copy.deepcopy(self._cache["nodes"])
        snap_edges      = copy.deepcopy(self._cache["edges"])
        snap_edge_idx   = copy.deepcopy(self._edge_type_index)
        snap_purged     = copy.deepcopy(self._purged_nodes)
        snap_edge_add   = copy.deepcopy(self._edge_additions)
        snap_edge_del   = copy.deepcopy(self._edge_deletions)
        snap_pending    = copy.deepcopy(self._pending_creations)
        snap_updates    = copy.deepcopy(self._node_updates)

        self._transaction_depth = 1
        try:
            yield
            self._transaction_depth = 0
            self._flush_pending_writes()
        except Exception:
            self._transaction_depth = 0
            self._cache["nodes"]    = snap_nodes
            self._cache["edges"]    = snap_edges
            self._edge_type_index   = snap_edge_idx
            self._purged_nodes      = snap_purged
            self._edge_additions    = snap_edge_add
            self._edge_deletions    = snap_edge_del
            self._pending_creations = snap_pending
            self._node_updates      = snap_updates
            self._dirty_nodes.clear()
            self._dirty_edges.clear()
            self._dirty_index.update(snap_nodes.keys())
            raise

    def _persist_collection_full(self, collection_name):
        """Compact a collection: RMW merge under lock, write base file, remove temp file."""
        path      = os.path.join(self.dirs["nodes"], f"{collection_name}.json")
        temp_path = self._temp_file(collection_name)
        our_ram   = self._cache["nodes"].get(collection_name, {})
        purged    = self._purged_nodes.pop(collection_name, set())
        with self._acquire_lock():
            # Read fresh disk state: base file + any pending temp entries from other processes
            disk_base = self._load_json_from_disk(path)
            disk_temp = self._load_json_from_disk(temp_path)
            merged = {**disk_base, **disk_temp}
            # Our RAM wins for nodes we know about
            merged.update(our_ram)
            # Remove nodes the GC permanently deleted this session
            for nid in purged:
                merged.pop(nid, None)
            self._save_json_atomic(path, merged)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            self._bump_revision_unlocked(collection_name)
        # Keep local RAM in sync with the authoritative merged state
        self._cache["nodes"][collection_name] = merged
        self._dirty_nodes.pop(collection_name, None)

    def _validate_node(self, collection_name, data):
        """Validate data against the schema for this collection (if one is defined)."""
        if collection_name not in self.schemas:
            return
        schema = self.schemas[collection_name]
        for field, spec in schema.items():
            # --- plain Python type (existing behaviour, fully backward-compatible) ---
            if isinstance(spec, type):
                if field not in data:
                    raise SchemaValidationError(
                        f"Required field '{field}' missing in collection '{collection_name}'."
                    )
                if not isinstance(data[field], spec):
                    raise SchemaTypeError(
                        f"Field '{field}' must be of type {spec.__name__}."
                    )
                continue

            if not isinstance(spec, dict):
                raise SchemaValidationError(f"Invalid schema spec for field '{field}': {spec!r}")

            field_type = spec.get("type")

            # --- enum: {"type": str|list, "options": [...]} ---
            if field_type in (str, list):
                if field not in data:
                    raise SchemaValidationError(
                        f"Required field '{field}' missing in collection '{collection_name}'."
                    )
                value = data[field]
                if not isinstance(value, field_type):
                    name = field_type.__name__
                    raise SchemaTypeError(f"Field '{field}' must be of type {name}.")
                options = spec.get("options")
                if options is not None:
                    items = value if isinstance(value, list) else [value]
                    for item in items:
                        if item not in options:
                            raise SchemaValidationError(
                                f"Field '{field}': '{item}' is not an allowed value. "
                                f"Allowed: {options}"
                            )
                continue

            # --- link: {"type": "link", "target": "collection_name"} ---
            if field_type == "link":
                value = data.get(field)
                if value is None:
                    continue  # link fields are optional
                target_col = spec.get("target")
                if not self._node_ref_exists(value, expected_collection=target_col):
                    hint = f" in collection '{target_col}'" if target_col else ""
                    raise SchemaValidationError(
                        f"Field '{field}': referenced node '{value}' does not exist{hint}."
                    )
                continue

            raise SchemaValidationError(f"Unknown schema type '{field_type}' for field '{field}'.")

    def _node_ref_exists(self, ref, expected_collection=None):
        """Return True if ref points to an existing, non-deleted node."""
        if not isinstance(ref, str) or "/" not in ref:
            return False
        col, nid = ref.split("/", 1)
        if expected_collection and col != expected_collection:
            return False
        node = self._cache["nodes"].get(col, {}).get(nid)
        return node is not None and not self._is_deleted(node)

    def _validate_edge(self, source_ref, target_ref, rel_type):
        """Check source/target collections against declared edge_constraints (if any)."""
        allowed = self.edge_constraints.get(rel_type)
        if allowed is None:
            return  # no constraint declared for this rel_type
        src_col = source_ref.split("/", 1)[0] if "/" in source_ref else source_ref
        tgt_col = target_ref.split("/", 1)[0] if "/" in target_ref else target_ref
        if (src_col, tgt_col) not in [tuple(p) for p in allowed]:
            raise EdgeConstraintError(
                f"Edge type '{rel_type}' does not allow "
                f"'{src_col}' → '{tgt_col}'. "
                f"Allowed pairs: {[list(p) for p in allowed]}"
            )

    def _is_deleted(self, node_data):
        return node_data is not None and "_deletion_flag" in node_data

    # ------------------------------------------------------------------
    # Internal helpers — multi-process cache freshness
    # ------------------------------------------------------------------

    def _reload_collection_unlocked(self, collection_name):
        """Re-read base + temp for one collection. Caller must hold _acquire_lock()."""
        base_path = os.path.join(self.dirs["nodes"], f"{collection_name}.json")
        temp_path = self._temp_file(collection_name)
        disk_base = self._load_json_from_disk(base_path)
        disk_temp = self._load_json_from_disk(temp_path)
        merged = {**disk_base, **disk_temp}
        # Preserve our own pending creations (not yet on disk).
        for nid in self._pending_creations.get(collection_name, set()):
            ram_node = self._cache["nodes"].get(collection_name, {}).get(nid)
            if ram_node is not None:
                merged[nid] = ram_node
        # Layer our pending field updates over the freshly-read disk state so
        # un-flushed local changes survive a refresh.
        for nid, delta in self._node_updates.get(collection_name, {}).items():
            base = dict(merged.get(nid, {}))
            base.update(delta)
            merged[nid] = base
        self._cache["nodes"][collection_name] = merged
        revisions = self._load_revisions_from_disk()
        self._collection_revisions[collection_name] = revisions.get(collection_name, 0)
        self._index_cache.pop(collection_name, None)

    def _refresh_if_stale(self, collection_name):
        """
        If a peer process bumped the on-disk revision for this collection past
        what we last saw, reload it. Only active when file_lock=True (the opt-in
        multi-process mode) and outside of transactions (whose snapshot must not
        shift mid-block).
        """
        if not self.file_lock or self._transaction_depth > 0:
            return
        on_disk = self._load_revisions_from_disk().get(collection_name, 0)
        if on_disk <= self._collection_revisions.get(collection_name, 0):
            return
        with self._acquire_lock():
            # Re-check inside the lock — another process may have bumped further;
            # we always end up with the latest state.
            self._reload_collection_unlocked(collection_name)

    # ------------------------------------------------------------------
    # Internal helpers — vault_text
    # ------------------------------------------------------------------

    _VAULT_TEXT_PREFIX = "@vault_text/"

    def _vt_path(self, collection_name, node_id, field):
        """Return the vault_text filepath for a given node field."""
        safe = re.sub(r"[^\w\-]", "_", f"{collection_name}__{node_id}__{field}")
        return os.path.join(self.dirs["vault_text"], f"{safe}.txt")

    def _offload_longtexts(self, collection_name, node_id, data):
        """
        If longtext_threshold is set, replace any string value that exceeds
        the threshold with an @vault_text/ reference. Modifies data in-place.
        """
        if not self.longtext_threshold:
            return
        for field, value in list(data.items()):
            if field.startswith("_"):
                continue
            if isinstance(value, str) and len(value) > self.longtext_threshold:
                path = self._vt_path(collection_name, node_id, field)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(value)
                data[field] = f"{self._VAULT_TEXT_PREFIX}{os.path.relpath(path, self.root)}"

    def _resolve_longtexts(self, data):
        """Return a copy of data with all @vault_text/ references resolved to their text."""
        resolved = {}
        for field, value in data.items():
            if isinstance(value, str) and value.startswith(self._VAULT_TEXT_PREFIX):
                rel = value[len(self._VAULT_TEXT_PREFIX):]
                abs_path = os.path.join(self.root, rel)
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        resolved[field] = f.read()
                except OSError:
                    resolved[field] = value  # keep reference if file missing
            else:
                resolved[field] = value
        return resolved

    # ------------------------------------------------------------------
    # Internal helpers — field index
    # ------------------------------------------------------------------

    def _index_file(self, collection):
        return os.path.join(self.dirs["index"], f"{collection}.json")

    def _meta_file(self):
        return os.path.join(self.root, "datenbank", "_meta.json")

    def _load_revisions_from_disk(self):
        """Load the {collection: revision} counter dict from datenbank/_meta.json."""
        return self._load_json_from_disk(self._meta_file()).get("revisions", {})

    def _bump_revision_unlocked(self, collection):
        """
        Increment the on-disk revision counter for one collection.

        Must be called inside an already-acquired self._acquire_lock() block —
        does RMW on the meta file without re-acquiring the lock so we share
        the same critical section as the actual data write.
        """
        path = self._meta_file()
        meta = self._load_json_from_disk(path)
        revisions = meta.setdefault("revisions", {})
        disk_rev = revisions.get(collection, 0)
        ram_rev  = self._collection_revisions.get(collection, 0)
        new_rev  = max(disk_rev, ram_rev) + 1
        revisions[collection] = new_rev
        self._save_json_atomic(path, meta)
        self._collection_revisions[collection] = new_rev

    def _collection_revision(self, collection):
        """Cached on-disk revision counter for one collection (0 if never written)."""
        return self._collection_revisions.get(collection, 0)

    def _mark_index_dirty(self, collection):
        self._dirty_index.add(collection)
        self._index_cache.pop(collection, None)

    def _load_index_from_disk(self, collection):
        """Load field index from disk. Returns None if missing or stale (revision mismatch)."""
        path = self._index_file(collection)
        if not os.path.exists(path):
            return None
        try:
            data = self._load_json_from_disk(path)
        except CorruptStoreError:
            return None
        if data.get("_meta", {}).get("revision") != self._collection_revision(collection):
            return None
        return {k: v for k, v in data.items() if k != "_meta"}

    def _build_field_index(self, collection, field):
        idx = {}
        for node_id, data in self._cache["nodes"].get(collection, {}).items():
            if self._is_deleted(data):
                continue
            val = data.get(field)
            if val is not None:
                idx.setdefault(str(val).lower(), []).append(node_id)
        return idx

    def _persist_index(self, collection):
        data = dict(self._index_cache.get(collection, {}))
        data["_meta"] = {"revision": self._collection_revision(collection)}
        self._save_json_atomic(self._index_file(collection), data)

    def _get_field_index(self, collection, field):
        """Return the index for a field, building and persisting it lazily if needed."""
        self._dirty_index.discard(collection)

        if collection in self._index_cache and field in self._index_cache[collection]:
            return self._index_cache[collection][field]

        if collection not in self._index_cache:
            loaded = self._load_index_from_disk(collection)
            if loaded is not None:
                self._index_cache[collection] = loaded
                if field in loaded:
                    return loaded[field]

        field_idx = self._build_field_index(collection, field)
        self._index_cache.setdefault(collection, {})[field] = field_idx
        self._persist_index(collection)
        return field_idx

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - NODES
    # ------------------------------------------------------------------

    def create_node(self, collection_name, node_id, data):
        """
        Create a node.
        :return: node reference as string "collection/id"
        :raises KeyError: if node_id already exists in this collection
        """
        self._validate_node(collection_name, data)

        if collection_name not in self._cache["nodes"]:
            self._cache["nodes"][collection_name] = {}

        if node_id in self._cache["nodes"][collection_name]:
            raise NodeExistsError(
                f"Node '{node_id}' already exists in collection '{collection_name}'. "
                f"Use update_node() to modify existing nodes."
            )

        stored = copy.deepcopy(data)
        self._offload_longtexts(collection_name, node_id, stored)
        self._cache["nodes"][collection_name][node_id] = stored
        self._pending_creations.setdefault(collection_name, set()).add(node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._mark_index_dirty(collection_name)
        self._log_audit("create", collection_name, node_id)
        self._fire_hooks("create_node", collection_name, node_id)
        return f"{collection_name}/{node_id}"

    def get_node(self, node_ref, readonly=False):
        """
        Read a node from the RAM cache.
        Returns None if the node does not exist or is soft-deleted.

        :param readonly: If True, return a direct reference to the cached dict instead of a
                         deep copy. Faster for display-only use — caller must never mutate
                         the returned dict.
        """
        try:
            col, n_id = node_ref.split("/", 1)
        except ValueError:
            return None

        self._refresh_if_stale(col)
        node_data = self._cache["nodes"].get(col, {}).get(n_id)
        if node_data and not self._is_deleted(node_data):
            return node_data if readonly else copy.deepcopy(node_data)
        return None

    def get_node_raw(self, node_ref):
        """
        Like get_node, but also returns soft-deleted nodes.
        Useful for maintenance / admin views.
        """
        try:
            col, n_id = node_ref.split("/", 1)
        except ValueError:
            return None
        self._refresh_if_stale(col)
        raw = self._cache["nodes"].get(col, {}).get(n_id)
        return copy.deepcopy(raw) if raw is not None else None

    def get_node_full(self, node_ref):
        """
        Like get_node, but resolves all @vault_text/ references in the returned dict,
        replacing them with the full text content from disk.
        Returns None if the node does not exist or is soft-deleted.
        """
        data = self.get_node(node_ref)
        if data is None:
            return None
        return self._resolve_longtexts(data)

    def update_node(self, collection_name, node_id, update_data):
        """
        Update fields of an existing node.
        Validates the merged state against the schema (if one is defined).
        """
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            return False

        # Simulate merge to check schema before applying
        merged = {**col_cache[node_id], **update_data}
        # Internal fields like _deletion_flag must not break schema validation
        schema_check_data = {k: v for k, v in merged.items() if not k.startswith("_")}
        try:
            self._validate_node(collection_name, schema_check_data)
        except (ValueError, TypeError):
            # Pure internal-flag updates (soft_delete) skip schema check
            if not all(k.startswith("_") for k in update_data):
                raise

        self._offload_longtexts(collection_name, node_id, update_data)
        col_cache[node_id].update(update_data)
        # Record the per-field delta so _persist_collection can apply only these
        # fields on top of the current disk state (per-field LWW merge under lock).
        # Skip delta tracking for nodes still pending creation: their first persist
        # writes the full RAM version anyway, and tracking would force a merge with
        # an empty disk node on retry after a CAS conflict.
        if node_id not in self._pending_creations.get(collection_name, set()):
            self._node_updates.setdefault(collection_name, {}).setdefault(node_id, {}).update(update_data)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._mark_index_dirty(collection_name)
        public_fields = {k: v for k, v in update_data.items() if not k.startswith("_")}
        if public_fields:
            self._log_audit("update", collection_name, node_id, str(list(public_fields.keys())))
            self._fire_hooks("update_node", collection_name, node_id)
        return True

    def list_nodes(self, collection_name, include_deleted=False, readonly=False):
        """
        Return all nodes of a collection as dict {node_id: data}.
        Soft-deleted entries are excluded by default.

        :param readonly: If True, values are direct cache references (no deep copy).
                         Faster for display/reporting — caller must never mutate the dicts.
        """
        self._refresh_if_stale(collection_name)
        col = self._cache["nodes"].get(collection_name, {})
        _copy = (lambda d: d) if readonly else copy.deepcopy
        if include_deleted:
            return {nid: _copy(data) for nid, data in col.items()}
        return {nid: _copy(data) for nid, data in col.items() if not self._is_deleted(data)}

    def list_collections(self):
        """Return names of all known node collections (internal collections excluded)."""
        return [c for c in self._cache["nodes"] if c not in _INTERNAL_COLLECTIONS]

    def _log_audit(self, action, collection, node_id, details=None):
        """Write an audit entry to _audit_log (only when audit=True)."""
        if not self.audit or self._audit_writing:
            return
        if collection in _INTERNAL_COLLECTIONS:
            return
        self._audit_writing = True
        try:
            entry_id = uuid.uuid4().hex
            entry = {
                "ref":        f"{collection}/{node_id}",
                "action":     action,
                "changed_at": datetime.now(timezone.utc).isoformat(),
            }
            if details:
                entry["details"] = details
            if "_audit_log" not in self._cache["nodes"]:
                self._cache["nodes"]["_audit_log"] = {}
            self._cache["nodes"]["_audit_log"][entry_id] = entry
            self._mark_node_dirty("_audit_log", entry_id)
            self._persist_collection("_audit_log")
        finally:
            self._audit_writing = False

    def _fire_hooks(self, event, collection, node_id):
        """Fire non-blocking HTTP POST to all matching webhook URLs. Errors are silently ignored."""
        if not self.webhooks or collection in _INTERNAL_COLLECTIONS:
            return
        payload = json.dumps({
            "event":      event,
            "collection": collection,
            "node_id":    node_id,
            "ref":        f"{collection}/{node_id}",
            "timestamp":  datetime.now(timezone.utc).isoformat(),
        }).encode()
        for hook in self.webhooks:
            events      = hook.get("events", "*")
            collections = hook.get("collections", "*")
            if events != "*" and event not in events:
                continue
            if collections != "*" and collection not in collections:
                continue
            url = hook.get("url", "")
            if not url:
                continue
            def _send(u=url, p=payload):
                try:
                    req = urllib.request.Request(
                        u, data=p, headers={"Content-Type": "application/json"}, method="POST"
                    )
                    urllib.request.urlopen(req, timeout=3)
                except Exception:
                    pass
            threading.Thread(target=_send, daemon=True).start()

    def find_nodes(self, collection_name, match, readonly=False):
        """
        Search nodes by field values. Supports three matching modes per field:

          Substring   {"title": "pump"}           -> case-insensitive substring
          Wildcard    {"title": "LH*Pump*"}        -> * as wildcard
          Predicate   {"title": lambda v: ...}    -> arbitrary logic, linear scan

        Multiple fields are AND-combined.

        :param readonly: If True, values are direct cache references (no deep copy).
                         Faster for display/reporting — caller must never mutate the dicts.
        :return: {node_id: node_data}
        """
        if not match:
            return self.list_nodes(collection_name, readonly=readonly)

        self._refresh_if_stale(collection_name)
        result_ids = None

        for field, criterion in match.items():
            if callable(criterion):
                nodes = self._cache["nodes"].get(collection_name, {})
                matching = {
                    nid for nid, data in nodes.items()
                    if not self._is_deleted(data) and criterion(data.get(field))
                }
            elif isinstance(criterion, str) and "*" in criterion:
                pattern = re.compile(
                    "^" + re.escape(criterion.lower()).replace(r"\*", ".*") + "$"
                )
                field_idx = self._get_field_index(collection_name, field)
                matching = {nid for val, ids in field_idx.items()
                            if pattern.match(val) for nid in ids}
            else:
                needle = str(criterion).lower()
                field_idx = self._get_field_index(collection_name, field)
                matching = {nid for val, ids in field_idx.items()
                            if needle in val for nid in ids}

            result_ids = matching if result_ids is None else result_ids & matching

        if result_ids is None:
            return {}

        nodes = self._cache["nodes"].get(collection_name, {})
        _copy = (lambda d: d) if readonly else copy.deepcopy
        return {
            nid: _copy(nodes[nid])
            for nid in result_ids
            if nid in nodes and not self._is_deleted(nodes[nid])
        }

    def next_id(self, collection_name, prefix="", padding=0):
        """
        Return the next available ID in a collection.
        Scans existing IDs with the given prefix and returns max+1.

        :param collection_name: collection to scan
        :param prefix: ID prefix, e.g. 'DOC-' or 'EQ-'
        :param padding: zero-pad the number, e.g. 4 → 'DOC-0001'
        :return: Neue ID als String

        Beispiel:
            db.next_id('documents', prefix='DOC-', padding=4)
            -> 'DOC-0001' wenn leer, 'DOC-0042' wenn DOC-0041 existiert
        """
        existing = self._cache["nodes"].get(collection_name, {})
        max_num = 0
        for nid in existing:
            if prefix and not nid.startswith(prefix):
                continue
            num_part = nid[len(prefix):]
            try:
                num = int(num_part)
                if num > max_num:
                    max_num = num
            except ValueError:
                continue
        next_num = max_num + 1
        if padding > 0:
            return f"{prefix}{str(next_num).zfill(padding)}"
        return f"{prefix}{next_num}"

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - IMPORT / EXPORT
    # ------------------------------------------------------------------

    def _import_records(self, collection_name, records, id_field, field_map, on_conflict):
        """Shared core for import_json / import_csv."""
        if on_conflict not in ("error", "skip", "overwrite"):
            raise ValueError(f"on_conflict must be 'error', 'skip', or 'overwrite'; got '{on_conflict}'")
        imported = skipped = 0
        with self.transaction():
            for raw in records:
                # apply field_map (rename keys)
                if field_map:
                    row = {field_map.get(k, k): v for k, v in raw.items()}
                    src_id_key = field_map.get(id_field, id_field)
                else:
                    row = dict(raw)
                    src_id_key = id_field
                node_id = str(row.get(src_id_key) or raw.get(id_field, ""))
                if not node_id:
                    raise ValueError(f"id_field '{id_field}' missing or empty in record: {raw}")
                exists = node_id in self._cache["nodes"].get(collection_name, {})
                if exists:
                    if on_conflict == "error":
                        raise NodeExistsError(f"Node '{node_id}' already exists in '{collection_name}'.")
                    if on_conflict == "skip":
                        skipped += 1
                        continue
                    self.update_node(collection_name, node_id, row)
                else:
                    self.create_node(collection_name, node_id, row)
                imported += 1
        return {"imported": imported, "skipped": skipped}

    def import_json(self, collection_name, filepath, id_field,
                    field_map=None, on_conflict="error"):
        """
        Import nodes from a JSON file into a collection.

        The file may contain either a list of dicts or a dict of dicts
        (values are treated as records).

        :param id_field:    Field name in the source data whose value becomes the node ID.
        :param field_map:   Optional {source_field: target_field} rename map.
        :param on_conflict: 'error' (default) | 'skip' | 'overwrite'
        :return: {"imported": n, "skipped": n}
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data if isinstance(data, list) else list(data.values())
        return self._import_records(collection_name, records, id_field, field_map, on_conflict)

    def import_csv(self, collection_name, filepath, id_field,
                   field_map=None, on_conflict="error"):
        """
        Import nodes from a CSV file into a collection.

        :param id_field:    Column name whose value becomes the node ID.
        :param field_map:   Optional {source_column: target_field} rename map.
        :param on_conflict: 'error' (default) | 'skip' | 'overwrite'
        :return: {"imported": n, "skipped": n}
        """
        import csv
        with open(filepath, "r", encoding="utf-8", newline="") as f:
            records = list(csv.DictReader(f))
        return self._import_records(collection_name, records, id_field, field_map, on_conflict)

    def export_json(self, collection_name, filepath):
        """
        Export all nodes of a collection to a JSON file.
        Format: {"node_id": {fields...}, ...}
        """
        nodes = self.list_nodes(collection_name)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(nodes, f, indent=2, ensure_ascii=False)

    def export_csv(self, collection_name, filepath, fields=None):
        """
        Export all nodes of a collection to a CSV file.

        :param fields: Optional list of field names to include (all fields if None).
                       The node ID is always written as the first column '_id'.
        """
        import csv
        nodes = self.list_nodes(collection_name)
        if not nodes:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                pass
            return
        if fields is None:
            seen = {}
            for data in nodes.values():
                for k in data:
                    seen.setdefault(k, None)
            fields = [k for k in seen if not k.startswith("_")]
        with open(filepath, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["_id"] + list(fields),
                                    extrasaction="ignore")
            writer.writeheader()
            for node_id, data in nodes.items():
                writer.writerow({"_id": node_id, **{k: data.get(k, "") for k in fields}})

    def soft_delete(self, collection_name, node_id, keep_asset=True):
        """Mark a node for deletion by the garbage collector."""
        result = self.update_node(collection_name, node_id, {
            "_deletion_flag": datetime.now(timezone.utc).isoformat(),
            "_keep_asset": keep_asset,
        })
        if result:
            self._log_audit("soft_delete", collection_name, node_id)
            self._fire_hooks("soft_delete", collection_name, node_id)
        return result

    def restore_node(self, collection_name, node_id):
        """
        Undo a soft-delete (as long as the GC has not yet run).

        :return: True only if a soft-deleted node was actually restored.
                 False if the node does not exist or was not soft-deleted —
                 in both no-op cases neither audit log nor webhooks fire.
        """
        col_cache = self._cache["nodes"].get(collection_name, {})
        node = col_cache.get(node_id)
        if node is None or "_deletion_flag" not in node:
            return False
        node.pop("_deletion_flag", None)
        node.pop("_keep_asset", None)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._mark_index_dirty(collection_name)
        self._log_audit("restore", collection_name, node_id)
        self._fire_hooks("restore_node", collection_name, node_id)
        return True

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - EDGES
    # ------------------------------------------------------------------

    def create_edge(self, source_ref, target_ref, rel_type, meta=None, cascade_delete=False):
        """
        Create a directed edge.
        :param cascade_delete: if True the target node is deleted together with the source.
                               Use for: logs belonging to an object, sub-processes of a
                               main process, attached files, etc.
        :return: edge_id
        """
        if self.get_node(source_ref) is None:
            raise NodeNotFoundError(f"Source node '{source_ref}' does not exist or is soft-deleted.")
        if self.get_node(target_ref) is None:
            raise NodeNotFoundError(f"Target node '{target_ref}' does not exist or is soft-deleted.")
        self._validate_edge(source_ref, target_ref, rel_type)

        edge_id = f"link_{uuid.uuid4().hex[:12]}"
        edge_data = {
            "source":     source_ref,
            "target":     target_ref,
            "type":       rel_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if cascade_delete:
            edge_data["_cascade_delete"] = True
        if meta:
            safe_meta = {k: v for k, v in meta.items() if k not in _RESERVED_EDGE_FIELDS}
            edge_data.update(safe_meta)

        self._cache["edges"].setdefault(rel_type, {})[edge_id] = edge_data
        self._edge_type_index[edge_id] = rel_type
        self._edge_additions.setdefault(rel_type, {})[edge_id] = edge_data
        self._persist_edge_type(rel_type)
        return edge_id

    def get_edge(self, edge_id):
        """Return a copy of the edge with the given ID, or None."""
        rel_type = self._edge_type_index.get(edge_id)
        if rel_type is None:
            return None
        edge = self._cache["edges"].get(rel_type, {}).get(edge_id)
        return copy.deepcopy(edge) if edge is not None else None

    def delete_edge(self, edge_id):
        """Delete an edge permanently (edges have no soft-delete)."""
        rel_type = self._edge_type_index.get(edge_id)
        if rel_type and edge_id in self._cache["edges"].get(rel_type, {}):
            del self._cache["edges"][rel_type][edge_id]
            del self._edge_type_index[edge_id]
            # If the edge was added this session it never reached disk — just cancel the addition.
            # Otherwise record it as a deletion so _flush_edge_type removes it from disk.
            if edge_id in self._edge_additions.get(rel_type, {}):
                del self._edge_additions[rel_type][edge_id]
            else:
                self._edge_deletions.setdefault(rel_type, set()).add(edge_id)
            self._persist_edge_type(rel_type)
            return True
        return False

    def list_edges(self, rel_type=None):
        """
        Return all edges, optionally filtered by type.
        Format: {edge_id: edge_data}
        """
        if rel_type is not None:
            return {
                eid: copy.deepcopy(e)
                for eid, e in self._cache["edges"].get(rel_type, {}).items()
            }
        return {
            eid: copy.deepcopy(e)
            for bucket in self._cache["edges"].values()
            for eid, e in bucket.items()
        }

    def get_connected(self, node_ref, direction="out", rel_type=None,
                      target_collection=None, include_deleted=False):
        """
        Find connected node references.
        :param direction: 'out' (outgoing) or 'in' (incoming)
        :param rel_type:  optional filter by relationship type
        :param target_collection: optional filter by target collection
                                  (e.g. 'equipments' returns only refs starting with 'equipments/')
        :param include_deleted: include soft-deleted targets
        :return: Liste von Node-Refs
        """
        if rel_type:
            buckets = [self._cache["edges"].get(rel_type, {}).values()]
        else:
            buckets = [b.values() for b in self._cache["edges"].values()]

        results = []
        for edge in (e for b in buckets for e in b):
            if direction == "out" and edge["source"] == node_ref:
                target = edge["target"]
            elif direction == "in" and edge["target"] == node_ref:
                target = edge["source"]
            else:
                continue

            # collection filter
            if target_collection and not target.startswith(f"{target_collection}/"):
                continue

            # filter out non-existent and soft-deleted targets
            if not include_deleted:
                raw = self._cache["nodes"].get(
                    target.split("/", 1)[0] if "/" in target else "", {}
                ).get(target.split("/", 1)[1] if "/" in target else "")
                if raw is None or self._is_deleted(raw):
                    continue

            results.append(target)
        return results

    def get_connected_edges(self, node_ref, direction="out", rel_type=None):
        """
        Like get_connected, but returns the full edge objects (including metadata).
        Format: [(edge_id, edge_data), ...]
        """
        if rel_type:
            buckets = [(rel_type, self._cache["edges"].get(rel_type, {}))]
        else:
            buckets = list(self._cache["edges"].items())

        results = []
        for _, bucket in buckets:
            for edge_id, edge in bucket.items():
                if direction == "out" and edge["source"] == node_ref:
                    results.append((edge_id, copy.deepcopy(edge)))
                elif direction == "in" and edge["target"] == node_ref:
                    results.append((edge_id, copy.deepcopy(edge)))
        return results

    def traverse(self, start_ref, rel_type=None, direction="out",
                 max_depth=None, target_collection=None, include_deleted=False,
                 include_start=False):
        """
        Multi-hop traversal (breadth-first).
        Find all nodes reachable from start_ref via rel_type.

        :param start_ref: starting node
        :param rel_type: filter by relationship type (None = follow all types)
        :param direction: 'out' follows edges forward, 'in' follows backward
        :param max_depth: maximum depth (None = unlimited)
        :param target_collection: optional filter by target collection
        :param include_start: include start node in result (default False)
        :return: list of node refs in BFS order (no duplicates)

        Beispiel:
            # All sub-processes and sub-sub-processes of a main process:
            db.traverse('processes/PROC-MAIN', rel_type='has_subprocess')

            # All equipments needed by any sub-process:
            subs = db.traverse('processes/PROC-MAIN', rel_type='has_subprocess')
            all_procs = [start] + subs
            equipments = set()
            for p in all_procs:
                equipments.update(db.get_connected(p, 'out', 'needs_equipment'))
        """
        visited = {start_ref}
        current_level = [start_ref]
        results = [start_ref] if include_start else []
        depth = 0

        while current_level:
            if max_depth is not None and depth >= max_depth:
                break
            next_level = []
            for node in current_level:
                connected = self.get_connected(
                    node, direction=direction, rel_type=rel_type,
                    target_collection=target_collection, include_deleted=include_deleted,
                )
                for c in connected:
                    if c not in visited:
                        visited.add(c)
                        next_level.append(c)
                        results.append(c)
            current_level = next_level
            depth += 1

        return results

    def collect_related(self, start_ref, rel_type_path, direction="out",
                        include_intermediate=False):
        """
        Collect nodes along a chain of different relationship types.
        Each rel_type is applied strictly level by level: only nodes reached at
        level N are used as starting points for level N+1.

        Useful for mixed traversals like:
        MainProcess --has_subprocess--> SubProcesses --needs_equipment--> Equipments

        :param rel_type_path: list of rel_types to follow per level;
                              the last level provides the results.
        :param include_intermediate: If True, every intermediate level is unioned with
                                     the carried-over set, so direct connections at
                                     earlier levels can still reach the final rel_type.
                                     Default False = strict level-by-level (was the
                                     documented semantics; the loose mode is opt-in).
        :return: sorted list of node refs at the end of the chain (no duplicates)

        Beispiel:
            # Equipments needed by sub-processes of a main process (strict):
            db.collect_related('processes/PROC-MAIN',
                               ['has_subprocess', 'needs_equipment'])

            # ...plus equipments needed directly by the main process itself:
            db.collect_related('processes/PROC-MAIN',
                               ['has_subprocess', 'needs_equipment'],
                               include_intermediate=True)
        """
        if not rel_type_path:
            return []

        current = {start_ref}
        for rel_type in rel_type_path[:-1]:
            next_set = set()
            for node in current:
                next_set.update(self.get_connected(node, direction, rel_type))
            current = (current | next_set) if include_intermediate else next_set

        results = set()
        final_rel = rel_type_path[-1]
        for node in current:
            results.update(self.get_connected(node, direction, final_rel))
        return sorted(results)


# =============================================================================
# MAINTENANCE MODULE: GARBAGE COLLECTOR
# =============================================================================

class MaintenanceEngine(FlatGraphDB):
    """
    Isolated maintenance module. Runs manually, on a schedule, or at app startup.
    Idempotent: safe to re-run after failures.

    Phases:
      A - Scanner:  finds nodes with _deletion_flag
      B - Cascader: removes associated edges
      C - Purger:   archives assets and permanently deletes nodes
    """

    def run_garbage_collection(self, verbose=False):
        """
        Run the full garbage collection cycle.
        :return: statistics dict
        """
        stats = {"scanned": 0, "edges_removed": 0, "nodes_purged": 0,
                 "assets_archived": 0, "assets_kept": 0, "vault_text_orphans": 0}

        # --- PHASE A: Scanner ---
        to_delete = self._scan_for_deletions()
        stats["scanned"] = len(to_delete)

        if not to_delete:
            if verbose:
                print("[GC] No objects marked for deletion.")
            stats["vault_text_orphans"] = self._collect_vault_text_orphans(verbose)
            self._write_maintenance_log(stats)
            return stats

        if verbose:
            print(f"[GC] {len(to_delete)} object(s) marked for deletion.")

        # --- PHASE B: Cascader ---
        refs_to_delete = {f"{col}/{nid}" for col, nid, _ in to_delete}
        stats["edges_removed"] = self._cascade_delete_edges(refs_to_delete)

        if verbose:
            print(f"[GC] {stats['edges_removed']} dangling edge(s) removed.")

        # --- PHASE C: Purger ---
        for col, nid, node_data in to_delete:
            archived = self._purge_node(col, nid, node_data)
            stats["nodes_purged"] += 1
            if archived is True:
                stats["assets_archived"] += 1
            elif archived is False:
                stats["assets_kept"] += 1

        if verbose:
            print(f"[GC] {stats['nodes_purged']} node(s) permanently deleted.")
            print(f"[GC] {stats['assets_archived']} asset(s) archived, {stats['assets_kept']} kept.")

        # Compaction: write all collections that had deletions fully to disk
        purged_collections = {col for col, _, _ in to_delete}
        for col in purged_collections:
            self._persist_collection_full(col)

        # Also compact any other pending temp-files
        for col in list(self._cache["nodes"].keys()):
            if os.path.exists(self._temp_file(col)):
                self._persist_collection_full(col)

        # vault_text orphan cleanup: remove .txt files with no live node reference
        stats["vault_text_orphans"] = self._collect_vault_text_orphans(verbose)

        self._write_maintenance_log(stats)
        return stats

    def _collect_vault_text_orphans(self, verbose=False):
        """Remove vault_text/ files that are no longer referenced by any live node."""
        vt_dir = self.dirs["vault_text"]
        if not os.path.isdir(vt_dir):
            return 0
        # Build set of all @vault_text/ references currently in the cache
        live_refs = set()
        prefix = FlatGraphDB._VAULT_TEXT_PREFIX
        for col_data in self._cache["nodes"].values():
            for node_data in col_data.values():
                if self._is_deleted(node_data):
                    continue
                for value in node_data.values():
                    if isinstance(value, str) and value.startswith(prefix):
                        rel = value[len(prefix):]
                        live_refs.add(os.path.normpath(os.path.join(self.root, rel)))
        removed = 0
        for filename in os.listdir(vt_dir):
            if not filename.endswith(".txt"):
                continue
            filepath = os.path.normpath(os.path.join(vt_dir, filename))
            if filepath not in live_refs:
                os.remove(filepath)
                removed += 1
        if verbose and removed:
            print(f"[GC] {removed} vault_text orphan(s) removed.")
        return removed

    # ------------------------------------------------------------------
    # Interne Phasen
    # ------------------------------------------------------------------

    def _scan_for_deletions(self):
        """
        Phase A: find all nodes with _deletion_flag.
        Expand the set with cascade_delete targets (transitive closure).
        """
        # Step 1: find directly flagged nodes
        direct = []
        for col_name, col_data in self._cache["nodes"].items():
            for node_id, data in col_data.items():
                if self._is_deleted(data):
                    direct.append((col_name, node_id, data))

        if not direct:
            return []

        # Step 2: cascade expansion — follow all cascade_delete edges recursively.
        # Snapshot taken once before the loop: edges never change during expansion
        # (only node _deletion_flag is written), so one pass through the snapshot
        # is sufficient per outer iteration.
        # All affected collections are flushed only after the full expansion so
        # the on-disk state is never partially consistent mid-GC.
        to_delete_refs = {f"{col}/{nid}" for col, nid, _ in direct}
        dirty_collections = set()
        edges_snapshot = [e for b in self._cache["edges"].values() for e in b.values()]

        changed = True
        while changed:
            changed = False
            for edge in edges_snapshot:
                if not edge.get("_cascade_delete"):
                    continue
                if edge["source"] in to_delete_refs and edge["target"] not in to_delete_refs:
                    to_delete_refs.add(edge["target"])
                    try:
                        col, nid = edge["target"].split("/", 1)
                        target_node = self._cache["nodes"].get(col, {}).get(nid)
                        if target_node and not self._is_deleted(target_node):
                            target_node["_deletion_flag"] = datetime.now(timezone.utc).isoformat()
                            target_node.setdefault("_keep_asset", True)
                            self._mark_node_dirty(col, nid)
                            dirty_collections.add(col)
                    except ValueError:
                        pass
                    changed = True

        # Expansion done — flush all marked collections to disk
        for col in dirty_collections:
            self._persist_collection(col)

        # Step 3: build final list from the current cache state
        found = []
        for ref in to_delete_refs:
            try:
                col, nid = ref.split("/", 1)
            except ValueError:
                continue
            data = self._cache["nodes"].get(col, {}).get(nid)
            if data and self._is_deleted(data):
                found.append((col, nid, data))
        return found

    def _cascade_delete_edges(self, refs_to_delete):
        """Phase B: remove all edges pointing to or from nodes being deleted."""
        dirty_types = set()
        count = 0
        for rel_type, bucket in self._cache["edges"].items():
            to_remove = [
                eid for eid, e in bucket.items()
                if e["source"] in refs_to_delete or e["target"] in refs_to_delete
            ]
            for eid in to_remove:
                del bucket[eid]
                self._edge_type_index.pop(eid, None)
                if eid in self._edge_additions.get(rel_type, {}):
                    del self._edge_additions[rel_type][eid]
                else:
                    self._edge_deletions.setdefault(rel_type, set()).add(eid)
                dirty_types.add(rel_type)
                count += 1
        for rel_type in dirty_types:
            self._persist_edge_type(rel_type)
        return count

    def _purge_node(self, collection_name, node_id, node_data):
        """
        Phase C: handle asset archiving and permanently delete the node.
        :return: True if asset archived, False if kept, None if no asset
        """
        keep_asset = node_data.get("_keep_asset", True)
        asset_path = node_data.get("datei")  # convention: 'datei' field points to a vault-relative path
        asset_status = None

        if asset_path:
            vault_real = os.path.realpath(self.dirs["vault"])
            candidate = os.path.realpath(os.path.join(self.root, asset_path))
            # path outside vault is ignored (prevents path traversal)
            if candidate.startswith(vault_real + os.sep) and os.path.exists(candidate):
                if keep_asset:
                    # file stays in vault (becomes an orphan)
                    asset_status = False
                else:
                    # move to vault_archive (safety net)
                    target_path = os.path.join(
                        self.dirs["vault_archive"],
                        os.path.basename(candidate)
                    )
                    # avoid name collision in archive
                    if os.path.exists(target_path):
                        base, ext = os.path.splitext(target_path)
                        target_path = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
                    shutil.move(candidate, target_path)
                    asset_status = True

        # remove node permanently (LAST step → idempotency)
        del self._cache["nodes"][collection_name][node_id]
        self._purged_nodes.setdefault(collection_name, set()).add(node_id)
        self._dirty_nodes.pop(collection_name, None)  # no temp-write for deleted nodes
        self._mark_index_dirty(collection_name)
        return asset_status

    def _write_maintenance_log(self, stats):
        """Append an entry to the maintenance log."""
        log_path = os.path.join(self.root, "datenbank", "maintenance.log")
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] {json.dumps(stats, ensure_ascii=False)}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
