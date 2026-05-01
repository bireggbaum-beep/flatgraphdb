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
import hashlib
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
# MAIN ENGINE
# =============================================================================

class FlatGraphDB:
    def __init__(self, root_dir, schemas=None, edge_constraints=None,
                 file_lock=False, audit=False, webhooks=None):
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
        """
        self.root = root_dir
        self.schemas = schemas or {}
        self.edge_constraints = edge_constraints or {}
        self.file_lock = file_lock
        self.audit = audit
        self.webhooks = webhooks or []
        self._lock_path = os.path.join(root_dir, ".flatgraph.lock")
        self._audit_writing = False  # prevents recursive audit entries

        self.dirs = {
            "nodes": os.path.join(root_dir, "datenbank", "nodes"),
            "edges": os.path.join(root_dir, "datenbank", "edges"),
            "index": os.path.join(root_dir, "datenbank", "index"),
            "vault": os.path.join(root_dir, "vault"),
            "vault_archive": os.path.join(root_dir, "vault_archive"),
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
        self._purged_nodes   = {}    # {collection: set(node_ids)} — permanently deleted this session
        self._edge_additions = {}    # {rel_type: {edge_id: edge_data}} — edges created this session
        self._edge_deletions = {}    # {rel_type: set(edge_ids)} — edges deleted this session
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
            raise RuntimeError(f"File '{filepath}' is not valid JSON: {e}") from e
        except IOError as e:
            raise RuntimeError(f"Could not read file '{filepath}': {e}") from e

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

    def _persist_collection(self, collection_name):
        """Write only changed nodes to the temp-file — or buffer when inside a transaction."""
        dirty_ids = self._dirty_nodes.get(collection_name, set())
        if not dirty_ids:
            return
        if self._transaction_depth > 0:
            return  # buffered until commit
        self._dirty_nodes.pop(collection_name, None)
        temp_path = self._temp_file(collection_name)
        col_data = self._cache["nodes"].get(collection_name, {})
        with self._acquire_lock():
            # Read inside the lock to avoid TOCTOU races with other processes
            temp_data = self._load_json_from_disk(temp_path)
            for nid in dirty_ids:
                if nid in col_data:
                    temp_data[nid] = col_data[nid]
                else:
                    temp_data.pop(nid, None)
            self._save_json_atomic(temp_path, temp_data)

    def _flush_pending_writes(self):
        """Flush all buffered node and edge writes to disk (transaction commit)."""
        for collection_name in list(self._dirty_nodes.keys()):
            dirty_ids = self._dirty_nodes.pop(collection_name, set())
            if not dirty_ids:
                continue
            temp_path = self._temp_file(collection_name)
            col_data = self._cache["nodes"].get(collection_name, {})
            with self._acquire_lock():
                temp_data = self._load_json_from_disk(temp_path)
                for nid in dirty_ids:
                    if nid in col_data:
                        temp_data[nid] = col_data[nid]
                    else:
                        temp_data.pop(nid, None)
                self._save_json_atomic(temp_path, temp_data)
        for rel_type in list(self._dirty_edges):
            self._flush_edge_type(rel_type)
        self._dirty_edges.clear()

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

        self._transaction_depth = 1
        try:
            yield
            self._transaction_depth = 0
            self._flush_pending_writes()
        except Exception:
            self._transaction_depth = 0
            self._cache["nodes"]   = snap_nodes
            self._cache["edges"]   = snap_edges
            self._edge_type_index  = snap_edge_idx
            self._purged_nodes     = snap_purged
            self._edge_additions   = snap_edge_add
            self._edge_deletions   = snap_edge_del
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
                    raise ValueError(
                        f"Required field '{field}' missing in collection '{collection_name}'."
                    )
                if not isinstance(data[field], spec):
                    raise TypeError(
                        f"Field '{field}' must be of type {spec.__name__}."
                    )
                continue

            if not isinstance(spec, dict):
                raise ValueError(f"Invalid schema spec for field '{field}': {spec!r}")

            field_type = spec.get("type")

            # --- enum: {"type": str|list, "options": [...]} ---
            if field_type in (str, list):
                if field not in data:
                    raise ValueError(
                        f"Required field '{field}' missing in collection '{collection_name}'."
                    )
                value = data[field]
                if not isinstance(value, field_type):
                    name = field_type.__name__
                    raise TypeError(f"Field '{field}' must be of type {name}.")
                options = spec.get("options")
                if options is not None:
                    items = value if isinstance(value, list) else [value]
                    for item in items:
                        if item not in options:
                            raise ValueError(
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
                    raise ValueError(
                        f"Field '{field}': referenced node '{value}' does not exist{hint}."
                    )
                continue

            raise ValueError(f"Unknown schema type '{field_type}' for field '{field}'.")

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
            raise ValueError(
                f"Edge type '{rel_type}' does not allow "
                f"'{src_col}' → '{tgt_col}'. "
                f"Allowed pairs: {[list(p) for p in allowed]}"
            )

    def _is_deleted(self, node_data):
        return node_data is not None and "_deletion_flag" in node_data

    # ------------------------------------------------------------------
    # Internal helpers — field index
    # ------------------------------------------------------------------

    def _index_file(self, collection):
        return os.path.join(self.dirs["index"], f"{collection}.json")

    def _collection_checksum(self, collection):
        keys = sorted(self._cache["nodes"].get(collection, {}).keys())
        return hashlib.md5("|".join(keys).encode()).hexdigest()

    def _mark_index_dirty(self, collection):
        self._dirty_index.add(collection)
        self._index_cache.pop(collection, None)

    def _load_index_from_disk(self, collection):
        """Load field index from disk. Returns None if missing or stale (checksum mismatch)."""
        path = self._index_file(collection)
        if not os.path.exists(path):
            return None
        try:
            data = self._load_json_from_disk(path)
        except RuntimeError:
            return None
        if data.get("_meta", {}).get("checksum") != self._collection_checksum(collection):
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
        data["_meta"] = {"checksum": self._collection_checksum(collection)}
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
            raise KeyError(
                f"Node '{node_id}' already exists in collection '{collection_name}'. "
                f"Use update_node() to modify existing nodes."
            )

        self._cache["nodes"][collection_name][node_id] = copy.deepcopy(data)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._mark_index_dirty(collection_name)
        self._log_audit("create", collection_name, node_id)
        self._fire_hooks("create_node", collection_name, node_id)
        return f"{collection_name}/{node_id}"

    def get_node(self, node_ref):
        """
        Read a node from the RAM cache.
        Returns None if the node does not exist or is soft-deleted.
        """
        try:
            col, n_id = node_ref.split("/", 1)
        except ValueError:
            return None

        node_data = self._cache["nodes"].get(col, {}).get(n_id)
        if node_data and not self._is_deleted(node_data):
            return copy.deepcopy(node_data)
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
        raw = self._cache["nodes"].get(col, {}).get(n_id)
        return copy.deepcopy(raw) if raw is not None else None

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

        col_cache[node_id].update(update_data)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._mark_index_dirty(collection_name)
        public_fields = {k: v for k, v in update_data.items() if not k.startswith("_")}
        if public_fields:
            self._log_audit("update", collection_name, node_id, str(list(public_fields.keys())))
            self._fire_hooks("update_node", collection_name, node_id)
        return True

    def list_nodes(self, collection_name, include_deleted=False):
        """
        Return all nodes of a collection as dict {node_id: data}.
        Soft-deleted entries are excluded by default.
        """
        col = self._cache["nodes"].get(collection_name, {})
        if include_deleted:
            return {nid: copy.deepcopy(data) for nid, data in col.items()}
        return {nid: copy.deepcopy(data) for nid, data in col.items() if not self._is_deleted(data)}

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

    def find_nodes(self, collection_name, match):
        """
        Search nodes by field values. Supports three matching modes per field:

          Substring   {"title": "pump"}           -> case-insensitive substring
          Wildcard    {"title": "LH*Pump*"}        -> * as wildcard
          Predicate   {"title": lambda v: ...}    -> arbitrary logic, linear scan

        Multiple fields are AND-combined.
        :return: {node_id: node_data}
        """
        if not match:
            return self.list_nodes(collection_name)

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
        return {
            nid: copy.deepcopy(nodes[nid])
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
        """
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            return False
        col_cache[node_id].pop("_deletion_flag", None)
        col_cache[node_id].pop("_keep_asset", None)
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
            raise ValueError(f"Source node '{source_ref}' does not exist or is soft-deleted.")
        if self.get_node(target_ref) is None:
            raise ValueError(f"Target node '{target_ref}' does not exist or is soft-deleted.")
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

    def collect_related(self, start_ref, rel_type_path, direction="out"):
        """
        Collect nodes along a chain of different relationship types.
        Useful for mixed traversals like:
        MainProcess --has_subprocess--> SubProcesses --needs_equipment--> Equipments

        :param rel_type_path: list of rel_types to follow per level;
                              the last level provides the results.
        :return: Liste von Node-Refs am Ende der Kette (ohne Duplikate)

        Beispiel:
            # All equipments of all sub-processes (and the main process itself):
            db.collect_related('processes/PROC-MAIN',
                               ['has_subprocess', 'needs_equipment'])
        """
        if not rel_type_path:
            return []

        # Collect all intermediate nodes (first to second-to-last level)
        current = {start_ref}
        for rel_type in rel_type_path[:-1]:
            next_set = set()
            for node in current:
                next_set.update(self.get_connected(node, direction, rel_type))
            # Keep start level too, in case there are direct connections at that level
            current = current | next_set

        # Final level: collect target nodes
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
        stats = {"scanned": 0, "edges_removed": 0, "nodes_purged": 0, "assets_archived": 0, "assets_kept": 0}

        # --- PHASE A: Scanner ---
        to_delete = self._scan_for_deletions()
        stats["scanned"] = len(to_delete)

        if not to_delete:
            if verbose:
                print("[GC] No objects marked for deletion.")
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

        self._write_maintenance_log(stats)
        return stats

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
        # Only mark in RAM during expansion; no persist inside the loop.
        # All affected collections are flushed after the expansion completes
        # so the on-disk state stays consistent.
        to_delete_refs = {f"{col}/{nid}" for col, nid, _ in direct}
        dirty_collections = set()

        changed = True
        while changed:
            changed = False
            edges_snapshot = [e for b in self._cache["edges"].values() for e in b.values()]
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
