# FlatGraphDB

A lightweight, file-based graph database for Python. No external dependencies. Single module.

**Key properties:**
- Nodes stored as JSON, grouped by collection (one file per collection)
- Relationships modelled as typed, directed edges (one file per edge type)
- RAM cache for fast reads; atomic writes via temp+rename
- Optional schema validation, multi-process locking, audit trail, webhooks

```
root_dir/
  datenbank/
    nodes/        one JSON file per collection
    edges/        one JSON file per edge type
    index/        lazy field indexes for find_nodes()
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

**Validation** runs on `create_node()` and `update_node()`. Raises `ValueError` for missing/invalid values, `TypeError` for wrong types.

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

`create_edge()` raises `ValueError` when the pair is not in the allowed list. Edge types not listed pass freely.

---

## Nodes — API reference

### create_node

```python
ref = db.create_node(collection_name, node_id, data)
# → "collection/node_id"
# raises KeyError if node_id already exists in the collection
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
# undoes soft_delete as long as GC has not yet run
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

Follows a chain of different edge types level by level:

```python
# books written by authors that a given author follows
books = db.collect_related("author/A1", ["follows", "written_by"], direction="out")
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
# node["content"] == "@vault_text/vault_text/..."  ← reference string, fast

node_full = db.get_node_full("article/AR1")
# node_full["content"] == full text  ← one extra disk read per offloaded field
```

Offloading is transparent: `update_node` with a long value offloads automatically. The GC removes `vault_text/` files that are no longer referenced by any live node.

---

## Multi-process safety

```python
db = FlatGraphDB("./mydb", file_lock=True)
```

With `file_lock=True`, every write acquires an exclusive file lock (`fcntl` on Unix, `msvcrt` on Windows) and uses a Read-Modify-Write pattern. Concurrent processes merge their changes at the collection level rather than blindly overwriting each other's writes.

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
