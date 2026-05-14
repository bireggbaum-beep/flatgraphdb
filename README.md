# FlatGraphDB

A lightweight, file-based graph database for Python. No external dependencies. Single module.

**Key properties:**
- Nodes stored as JSON, grouped by collection (one file per collection)
- Relationships modelled as typed, directed edges (one file per edge type)
- RAM cache for fast reads; durable atomic writes (temp file → fsync → rename → dir fsync)
- Crash-atomic multi-collection transactions; idempotent crash recovery on open
- Optional schema validation, multi-process locking, audit trail, webhooks

For the hard guarantees — what is durable, what is atomic, what is visible when,
and what happens on a crash — see **[DESIGN.md](DESIGN.md)**.

```
root_dir/
  datenbank/
    nodes/        one JSON file per collection
    edges/        one JSON file per edge type
    index/        lazy field indexes for find_nodes()
    _meta.json    store format version + per-collection revision counters
  vault/          binary assets (PDFs, images, …)
  vault_text/     offloaded longtext fields
  vault_archive/  quarantine for soft-deleted assets
```

---

## Quick start

```python
from flatgraph import FlatGraphDB

db = FlatGraphDB("./mydb")

db.create_node("author",  "A1", {"name": "Kafka",  "country": "CZ"})
db.create_node("book",    "B1", {"title": "The Trial", "year": 1925})
db.create_node("book",    "B2", {"title": "The Castle", "year": 1926})

db.create_edge("book/B1", "author/A1", "written_by")
db.create_edge("book/B2", "author/A1", "written_by")

node   = db.get_node("book/B1")
# → {"title": "The Trial", "year": 1925}

authors = db.get_connected("book/B1", direction="out", rel_type="written_by")
# → ["author/A1"]
```

---

## Constructor

```python
FlatGraphDB(
    root_dir,
    schemas            = None,   # field validation per collection (see Schema section)
    edge_constraints   = None,   # allowed source→target pairs per edge type
    file_lock          = False,  # fcntl/msvcrt exclusive lock for multi-process safety
    audit              = False,  # auto-write every write to _audit_log collection
    webhooks           = None,   # HTTP POST hooks on node events
    longtext_threshold = None,   # auto-offload strings > N chars to vault_text/
)
```

---

## Core concepts

**Node reference** — every node is identified globally as `"collection/node_id"`, e.g. `"author/A1"`. This string is used wherever a node needs to be addressed: as edge source/target, as a link field value, as a return value from `create_node`.

**Collection** — a named group of nodes sharing a common type. Stored as a single JSON file. No fixed schema required (schema is opt-in per collection).

**Edge** — a directed, typed connection between two node refs. Edge types are arbitrary strings. Edges can carry arbitrary metadata. One JSON file per edge type.

---

## Schema validation

All schema definitions are opt-in. Collections without a schema accept any data.

```python
db = FlatGraphDB("./mydb", schemas={
    "book": {
        # plain Python type — field is required, value must match isinstance()
        "title": str,
        "year":  int,

        # enum (str) — field required; value must be one of the listed options
        "status": {"type": str, "options": ["draft", "published", "archived"]},

        # enum (list) — field required; every element must be in options
        "genres": {"type": list, "options": ["fiction", "non-fiction", "sci-fi", "biography"]},

        # link — field optional; when present the value must be a valid,
        #         non-deleted node ref in the declared target collection
        "publisher": {"type": "link", "target": "publisher"},
    }
})
```

**Validation** runs on `create_node()` and `update_node()`. Raises `SchemaValidationError` for missing/invalid values and `SchemaTypeError` for wrong types. Both inherit from `ValueError` / `TypeError` respectively, so existing `except` clauses keep working.

---

## Edge constraints

Declare which collection pairs are valid endpoints for a given edge type:

```python
db = FlatGraphDB("./mydb", edge_constraints={
    "written_by":  [("book",    "author")],
    "published_by":[("book",    "publisher")],
    "follows":     [("author",  "author")],
})
```

`create_edge()` raises `EdgeConstraintError` (a `ValueError` subclass) when the pair is not in the allowed list. Edge types not listed pass freely.

---

## Nodes — API reference

### create_node

```python
ref = db.create_node(collection_name, node_id, data)
# → "collection/node_id"
# raises NodeExistsError (KeyError subclass) if node_id already exists in the collection
```

### get_node / get_node_full / get_node_raw

```python
node = db.get_node("book/B1")
# → deep copy of node data, or None if not found / soft-deleted

node = db.get_node("book/B1", readonly=True)
# → direct cache reference (no copy) — do not mutate; see readonly section

node = db.get_node_full("book/B1")
# → like get_node, but @vault_text/ references are resolved to full text

node = db.get_node_raw("book/B1")
# → like get_node, but also returns soft-deleted nodes (includes _deletion_flag)
```

### update_node

```python
db.update_node("book", "B1", {"status": "published"})
# → True on success, False if node_id not found
# merges update_data into the existing node (patch semantics, not replace)
```

### list_nodes

```python
all_books = db.list_nodes("book")
# → {node_id: data, …} — excludes soft-deleted

all_books = db.list_nodes("book", include_deleted=True)
all_books = db.list_nodes("book", readonly=True)  # no deep copy

db.list_collections()
# → ["author", "book", "publisher", …]  (internal collections excluded)
```

### find_nodes

```python
# substring match (case-insensitive)
results = db.find_nodes("book", {"title": "trial"})

# wildcard (* as glob)
results = db.find_nodes("book", {"title": "The*"})

# predicate (arbitrary logic, linear scan — no index)
results = db.find_nodes("book", {"year": lambda y: y and y > 1920})

# multiple fields are AND-combined
results = db.find_nodes("book", {"status": "published", "title": "the*"})

# readonly variant
results = db.find_nodes("book", {"status": "published"}, readonly=True)
```

Returns `{node_id: data}`. Soft-deleted nodes are always excluded.

**Index validity.** Each collection has an on-disk revision counter (`datenbank/_meta.json`) that is incremented on every write. Persisted indexes in `datenbank/index/` store the revision they were built from and are discarded on load when the revision has moved on — so a field-value change without an id change correctly invalidates a stale index. A `find_nodes()` call after that simply rebuilds the index on the next access.

### next_id

```python
db.next_id("book")                          # "1", "2", …
db.next_id("book", prefix="B-")             # "B-1", "B-2", …
db.next_id("book", prefix="B-", padding=4)  # "B-0001", "B-0042", …
```

Scans existing IDs with the given prefix and returns `max + 1`. Safe to call in a transaction.

### Soft-delete and restore

```python
db.soft_delete("book", "B1")
# marks node with _deletion_flag; invisible to get_node/list_nodes/find_nodes
# the garbage collector permanently removes it on the next GC run

db.soft_delete("book", "B1", keep_asset=False)
# same, but instructs GC to move the vault file to vault_archive/ instead of keeping it

db.restore_node("book", "B1")
# undoes soft_delete as long as GC has not yet run.
# Returns True only if a soft-deleted node was actually un-flagged;
# returns False if the node does not exist or was not soft-deleted (no-op,
# no audit entry, no webhook).
```

---

## Edges — API reference

### create_edge

```python
edge_id = db.create_edge(
    source_ref    = "book/B1",
    target_ref    = "author/A1",
    rel_type      = "written_by",
    meta          = {"role": "primary"},  # optional metadata dict
    cascade_delete= False,
)
```

`cascade_delete=True` — when the source node is soft-deleted, the GC automatically soft-deletes the target node as well. Use for owned sub-objects (e.g. a log entry that only exists in the context of its parent).

### get_edge / delete_edge / list_edges

```python
db.get_edge(edge_id)          # → edge data dict, or None
db.delete_edge(edge_id)       # → True / False; edges have no soft-delete

db.list_edges()                          # → {edge_id: data, …} — all types
db.list_edges(rel_type="written_by")     # → filtered by type
```

### get_connected

```python
# outgoing edges from a node
targets = db.get_connected("book/B1", direction="out", rel_type="written_by")
# → ["author/A1"]

# incoming edges to a node
sources = db.get_connected("author/A1", direction="in", rel_type="written_by")
# → ["book/B1", "book/B2"]

# filter by target collection
targets = db.get_connected("author/A1", direction="in", target_collection="book")

# include soft-deleted neighbours
targets = db.get_connected("book/B1", direction="out", include_deleted=True)
```

### get_connected_edges

Like `get_connected` but returns full edge objects:

```python
edges = db.get_connected_edges("book/B1", direction="out", rel_type="written_by")
# → [(edge_id, {"source": "book/B1", "target": "author/A1", "type": "written_by", …}), …]
```

### traverse

Breadth-first multi-hop traversal:

```python
# all nodes reachable from a starting point along one edge type
all_followers = db.traverse("author/A1", rel_type="follows")

# limit depth
direct_and_indirect = db.traverse("author/A1", rel_type="follows", max_depth=2)

# follow backwards (incoming edges)
db.traverse("author/A1", rel_type="follows", direction="in")

# include the start node in the result
db.traverse("author/A1", rel_type="follows", include_start=True)
```

Returns a list of node refs in BFS order, no duplicates.

### collect_related

Follows a chain of different edge types strictly level by level. Only nodes
reached at level N are used as starting points for level N+1.

```python
# books written by authors that a given author follows
books = db.collect_related("author/A1", ["follows", "written_by"], direction="out")

# also include books written directly by A1 (loose mode, opt-in)
books = db.collect_related("author/A1", ["follows", "written_by"],
                           direction="out", include_intermediate=True)
```

---

## Transactions

All writes inside a `with db.transaction()` block are buffered in RAM and flushed to disk atomically on exit. On exception the cache is rolled back to the state before the block — nothing is written to disk.

```python
with db.transaction():
    ref = db.create_node("book", db.next_id("book", "B-", 4), {"title": "New Book"})
    db.create_edge(ref, "author/A1", "written_by")
    db.update_node("author", "A1", {"book_count": 3})
# → all three writes hit disk here as one batch

# nested transactions join the outermost one
with db.transaction():
    with db.transaction():   # inner — no separate commit
        db.create_node(...)
# → flushed when the outer block exits
```

`db.flush()` — force-write all pending writes outside a transaction context.

### Durability and crash recovery

Every store file (node collections, edge types, field indexes, the revision counter) is written through a durable atomic path: data is written to a `.tmp` file, `fsync`'d, `os.replace()`'d into place, and the parent directory is `fsync`'d so the rename itself survives power loss. After a crash a reader sees either the previous file or the fully-written new one — never a truncated or zero-length file.

A `transaction()` that spans **multiple collections** is made atomic with a staging-file + commit-marker protocol, all under one lock:

1. Every collection's and edge type's next file content is computed (CAS conflicts surface here, before anything touches disk).
2. Each is written to a transaction-scoped staging file (`<file>.<txid>`), durably `fsync`'d.
3. One commit marker `datenbank/_tx_<txid>.json` is written durably — **its creation is the commit point**.
4. The staging files are renamed into place and the revision bumps applied.
5. The marker is removed.

On open, recovery runs first: a present marker is rolled **forward** (idempotent re-apply), staging files with no marker are rolled **back** (deleted), and `.tmp` scratch from an interrupted write is swept. A crash at any point therefore resolves to either the whole transaction or none of it. A marker that fails to parse never finished its `fsync`, so it was never a commit point and rolls back.

Single (non-transaction) writes bump the revision counter *before* the data file, so a crash in between leaves the revision ahead of the data — which only over-invalidates a field index (a harmless rebuild) rather than under-invalidating it.

---

## Import / Export

```python
# --- JSON ---
db.import_json(
    "book", "export.json",
    id_field   = "isbn",                          # source field that becomes node_id
    field_map  = {"isbn": "isbn", "t": "title"},  # optional rename map
    on_conflict= "overwrite",                     # "error" | "skip" | "overwrite"
)
db.export_json("book", "backup.json")
# output: {"node_id": {fields}, …}

# --- CSV ---
db.import_csv(
    "book", "data.csv",
    id_field   = "isbn",
    field_map  = {"t": "title"},
    on_conflict= "skip",
)
db.export_csv("book", "report.csv", fields=["title", "year", "status"])
# output: _id column + declared fields; fields=None exports all non-internal fields
```

Imports run inside a transaction — atomic, rolled back on any error.

---

## vault_text — longtext offloading

Large text fields can be offloaded from the JSON node to plain files in `vault_text/`. This keeps the RAM cache small while still allowing full-text access on demand.

```python
db = FlatGraphDB("./mydb", longtext_threshold=500)

db.create_node("article", "AR1", {
    "title":   "Short title",        # 11 chars — stays inline
    "content": "..." * 300,          # > 500 chars → written to vault_text/*.txt
})

node = db.get_node("article/AR1")
# node["content"] == "@vault_text/vault_text/<sha256>.txt"  ← reference, fast

node_full = db.get_node_full("article/AR1")
# node_full["content"] == full text  ← one extra disk read per offloaded field
```

Offloading is transparent: `update_node` with a long value offloads automatically.

**Content-addressed and immutable.** A blob is named by the sha256 of its content. The store is therefore append-only: an update writes a *new* blob and never overwrites an old one, and identical content across nodes is written once (deduplicated). Each blob is written through the durable atomic path before the referencing node is persisted.

This immutability is what makes offloading transaction-safe. A rolled-back or crashed write can only ever leave an *unreferenced orphan* — it can never corrupt the value an existing reference points at. The GC removes `vault_text/` files no longer referenced by any live node, so frequent updates of large fields accumulate orphans until the next `garbage_collection()` run — the same orphan-until-GC model the `vault/` binary assets use.

---

## Multi-process safety

```python
db = FlatGraphDB("./mydb", file_lock=True)
```

`file_lock=True` is the opt-in mode for multiple OS processes (or app instances) sharing the same database directory — for example the same user driving two devices, or a web app running with multiple workers. It is not a multi-user permission layer.

Every write acquires an exclusive file lock (`fcntl` on Unix, `msvcrt` on Windows) and runs the entire read-modify-write inside that lock. Within that critical section three guarantees are added:

- **CAS for `create_node()`** — at the moment of the disk write the on-disk state is re-read; if a peer process already created the same id, the RAM-cached node is rolled back and `ConflictError` is raised. No silent overwrite.
- **Per-field merge for `update_node()`** — the on-disk version of the node is read inside the lock and your update is layered on top. Two processes editing **disjoint fields** of the same node both survive. Two processes writing the **same field** resolve as last-writer-wins.
- **Stale-cache refresh on reads** — `get_node()`, `get_node_raw()`, `get_node_full()`, `list_nodes()` and `find_nodes()` check the collection's revision counter on entry. If a peer process bumped it past the local view, that single collection is re-read from disk before the call returns.

```python
try:
    db.create_node("book", "B1", {...})
except ConflictError:
    # peer process already created this id — refresh and decide
    existing = db.get_node("book/B1")
```

**What this mode does not promise.** Edges have unique UUIDs and are not subject to CAS. Whole-node operations that bypass the field-delta path (`restore_node`, GC cascade flag) write the full node version and follow whole-node LWW semantics. (Cross-collection transactions *are* crash-atomic — see [Durability and crash recovery](#durability-and-crash-recovery).)

With `file_lock=False` (the default) none of the above is active: writes are still atomic per file via `os.replace()`, but concurrent peer writes can lose updates and stale RAM caches are never refreshed.

---

## Audit trail

```python
db = FlatGraphDB("./mydb", audit=True)

db.create_node("book", "B1", {"title": "The Trial"})
db.update_node("book", "B1", {"status": "published"})

log = db.list_nodes("_audit_log")
# each entry: {"ref": "book/B1", "action": "create"|"update"|"soft_delete"|"restore",
#              "changed_at": "ISO timestamp", "details": "…"}
```

`_audit_log` is excluded from `list_collections()` and from GC.

---

## Webhooks

```python
db = FlatGraphDB("./mydb", webhooks=[
    {
        "url":         "https://example.com/hooks/graph",
        "events":      ["create_node", "update_node", "soft_delete"],
        "collections": "*",    # "*" = all, or a list e.g. ["book", "author"]
    }
])
```

Fires a non-blocking HTTP POST (daemon thread, 3 s timeout) on matching events. Payload:

```json
{
  "event":      "create_node",
  "collection": "book",
  "node_id":    "B1",
  "ref":        "book/B1",
  "timestamp":  "2026-05-07T12:00:00Z"
}
```

Available events: `create_node`, `update_node`, `soft_delete`, `restore_node`.

---

## Garbage collector

```python
from flatgraph import MaintenanceEngine

gc = MaintenanceEngine("./mydb")
stats = gc.run_garbage_collection(verbose=True)
# returns: {scanned, edges_removed, nodes_purged,
#           assets_archived, assets_kept, vault_text_orphans}
```

**Phases:**
1. **Scanner** — finds nodes with `_deletion_flag`; expands cascade-delete targets transitively
2. **Cascader** — removes all edges connected to nodes being deleted
3. **Purger** — moves vault assets to `vault_archive/` or keeps them; permanently deletes nodes
4. **Compaction** — merges pending temp-files into base JSON files
5. **vault_text cleanup** — removes `.txt` files no longer referenced by any live node

Idempotent — safe to re-run after a crash at any phase.

---

## readonly views

`get_node`, `list_nodes`, and `find_nodes` accept `readonly=True`. This skips the deep copy and returns a direct reference to the internal cache dict. Useful for high-throughput display operations (list views, reports, exports) where the returned data is never modified.

```python
# standard — safe to mutate the returned dict
node = db.get_node("book/B1")

# readonly — faster, do NOT mutate
node = db.get_node("book/B1", readonly=True)
rows = db.list_nodes("book", readonly=True)
hits = db.find_nodes("book", {"status": "published"}, readonly=True)
```

**Contract:** never write to a dict returned with `readonly=True`. Doing so corrupts the internal cache.

---

## Exceptions

All FlatGraphDB-specific errors derive from `FlatGraphError`, so a single `except FlatGraphError` catches the whole family. Each concrete class also inherits from a matching stdlib base so existing `except KeyError / ValueError / TypeError / RuntimeError` clauses keep working.

| Exception                | Inherits from         | Raised when                                            |
| ------------------------ | --------------------- | ------------------------------------------------------ |
| `NodeExistsError`        | `KeyError`            | `create_node` / `import_*` and the id already exists   |
| `NodeNotFoundError`      | `KeyError`            | `create_edge` source/target ref does not resolve       |
| `SchemaValidationError`  | `ValueError`          | required field missing, enum/link value invalid        |
| `SchemaTypeError`        | `TypeError`           | schema field present but wrong Python type             |
| `EdgeConstraintError`    | `ValueError`          | edge violates declared `edge_constraints`              |
| `CorruptStoreError`      | `RuntimeError`        | on-disk JSON is unreadable or malformed                |
| `UnsupportedFormatError` | `RuntimeError`        | store written by a newer FlatGraphDB build             |
| `ConflictError`          | `FlatGraphError`      | peer-process CAS collision on `create_node` / commit   |
| `TransactionError`       | `FlatGraphError`      | reserved for future transactional failures             |

---

## Tests

```bash
pip install pytest
pytest
```

The suite lives in `tests/`, uses pytest's `tmp_path` so each test runs against a fresh on-disk database, and covers the exception hierarchy, the documented `collect_related` / `restore_node` contracts, revision-based index invalidation, multi-process CAS / per-field merge / stale-cache refresh, plus the core CRUD / traverse / transaction / GC golden paths. CI runs it on every push via `.github/workflows/tests.yml`.
