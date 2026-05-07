# FlatGraphDB

A lightweight, file-based graph database for Python. No external dependencies. Single module.

```
root_dir/
  datenbank/
    nodes/        one JSON file per collection
    edges/        one JSON file per edge type
    index/        lazy field indexes
  vault/          binary assets (PDFs, images, …)
  vault_text/     offloaded longtext fields
  vault_archive/  quarantine for deleted assets
```

---

## Quick start

```python
from flatgraph import FlatGraphDB, MaintenanceEngine

db = FlatGraphDB("./mydb")

db.create_node("plant",     "P1",  {"name": "Werk Nord"})
db.create_node("equipment", "EQ1", {"name": "Reaktor A", "sap-id": 4711})

db.create_edge("equipment/EQ1", "plant/P1", "gehört-zu-plant")

node = db.get_node("equipment/EQ1")   # {"name": "Reaktor A", "sap-id": 4711}
```

---

## Constructor

```python
FlatGraphDB(
    root_dir,
    schemas          = None,   # field validation per collection
    edge_constraints = None,   # allowed source→target pairs per edge type
    file_lock        = False,  # fcntl/msvcrt file lock for multi-process safety
    audit            = False,  # auto-write audit entries to _audit_log
    webhooks         = None,   # HTTP POST hooks on node events
    longtext_threshold = None, # auto-offload strings longer than N chars to vault_text/
)
```

---

## Schema validation

All schema parameters are opt-in. Collections without a schema accept any data.

```python
db = FlatGraphDB("./mydb", schemas={
    "equipment": {
        # plain type — field required, isinstance check
        "name":    str,
        "sap-id":  int,

        # enum — field required, value must be one of the options
        "gxp-klasse": {"type": str,  "options": ["G", "N"]},
        "iso-tags":   {"type": list, "options": ["ISO5", "ISO7", "ISO8", "ISO9"]},

        # link — field optional; when set, ref must exist in target collection
        "gehört-zu-plant": {"type": "link", "target": "plant"},
    }
})
```

---

## Edge constraints

```python
db = FlatGraphDB("./mydb", edge_constraints={
    "gehört-zu-plant": [
        ("equipment", "plant"),
        ("software",  "plant"),
    ],
    "verarbeitet-product": [("process", "product")],
})
```

`create_edge()` raises `ValueError` when the source/target collection pair is not in the allowed list. Edge types without a constraint pass freely.

---

## Nodes

```python
# Create
ref = db.create_node("equipment", "EQ1", {"name": "Reaktor", "sap-id": 4711})
# → "equipment/EQ1"

# Read
node = db.get_node("equipment/EQ1")           # deep copy, None if deleted/missing
node = db.get_node("equipment/EQ1", readonly=True)  # direct cache ref, do not mutate
node = db.get_node_full("equipment/EQ1")      # resolves @vault_text/ references
node = db.get_node_raw("equipment/EQ1")       # includes soft-deleted nodes

# Update
db.update_node("equipment", "EQ1", {"status": "INAKTIV"})

# List / Search
all_nodes  = db.list_nodes("equipment")
all_nodes  = db.list_nodes("equipment", readonly=True)

results    = db.find_nodes("equipment", {"name": "reaktor"})        # substring
results    = db.find_nodes("equipment", {"name": "Reak*"})          # wildcard
results    = db.find_nodes("equipment", {"sap-id": lambda v: v > 1000})  # predicate
results    = db.find_nodes("equipment", {"name": "reaktor"}, readonly=True)

collections = db.list_collections()

# Auto-increment ID
next_id = db.next_id("equipment", prefix="EQ-", padding=4)  # "EQ-0001"

# Soft-delete (marks for GC)
db.soft_delete("equipment", "EQ1")
db.soft_delete("equipment", "EQ1", keep_asset=False)  # also archive vault file
db.restore_node("equipment", "EQ1")                   # undo before GC runs
```

---

## Edges

```python
edge_id = db.create_edge(
    "equipment/EQ1", "plant/P1",
    rel_type="gehört-zu-plant",
    meta={"since": "2026-01-01"},
    cascade_delete=False,   # True → target deleted when source is deleted
)

db.get_edge(edge_id)
db.delete_edge(edge_id)
db.list_edges()                        # all edges
db.list_edges(rel_type="gehört-zu-plant")

# Neighbours
targets = db.get_connected("equipment/EQ1", direction="out", rel_type="gehört-zu-plant")
sources = db.get_connected("plant/P1",       direction="in")

edges   = db.get_connected_edges("equipment/EQ1", direction="out")
# → [(edge_id, edge_data), …]

# Traversal (breadth-first)
reachable = db.traverse("process/MAIN", rel_type="hat_subprozess", max_depth=3)

# Chained traversal
equipments = db.collect_related("process/MAIN", ["hat_subprozess", "benötigt_equipment"])
```

---

## Import / Export

```python
# JSON
db.import_json("equipment", "sap_export.json",
    id_field="EQUNR",
    field_map={"EQUNR": "sap-id", "EQKTX": "name"},
    on_conflict="overwrite",   # error | skip | overwrite
)
db.export_json("equipment", "backup.json")

# CSV
db.import_csv("equipment", "sap_export.csv",
    id_field="EQUNR",
    field_map={"EQUNR": "sap-id", "EQKTX": "name"},
    on_conflict="skip",
)
db.export_csv("equipment", "report.csv", fields=["name", "status", "sap-id"])
```

Imports run inside a transaction — fully atomic.

---

## Transactions

```python
with db.transaction():
    ref = db.create_node("document", "DOC-001", {"title": "Prüfanweisung"})
    db.update_node("document", "DOC-001", {"filepath": vault_path})
    db.create_edge(ref, "equipment/EQ1", "gehört-zu-equipment")
# all writes flushed to disk here; rolled back on exception
```

Transactions can be nested — inner transactions join the outer one.

---

## vault_text — longtext offloading

```python
db = FlatGraphDB("./mydb", longtext_threshold=500)

db.create_node("document", "DOC1", {
    "title": "Prüfanweisung",
    "body":  "..."  * 300,   # > 500 chars → offloaded automatically
})

node = db.get_node("document/DOC1")
# node["body"] == "@vault_text/datenbank/..."  ← reference, fast

node_full = db.get_node_full("document/DOC1")
# node_full["body"] == full text content  ← resolved from disk
```

Orphaned `vault_text/` files (no live node reference) are removed automatically by the garbage collector.

---

## Multi-process safety

```python
db = FlatGraphDB("./mydb", file_lock=True)
```

With `file_lock=True`, every write uses an exclusive `fcntl`/`msvcrt` file lock and a Read-Modify-Write pattern — concurrent processes merge their changes instead of overwriting each other.

---

## Audit trail

```python
db = FlatGraphDB("./mydb", audit=True)
# create_node / update_node / soft_delete / restore_node
# → automatic entries in _audit_log collection
audit = db.list_nodes("_audit_log")
```

---

## Webhooks

```python
db = FlatGraphDB("./mydb", webhooks=[
    {
        "url":         "https://hooks.example.com/flatgraph",
        "events":      ["create_node", "update_node"],
        "collections": ["equipment", "document"],
    }
])
```

HTTP POST, non-blocking (daemon thread). `events` / `collections`: `"*"` = all.

---

## Garbage collector

```python
from flatgraph import MaintenanceEngine

gc = MaintenanceEngine("./mydb")
stats = gc.run_garbage_collection(verbose=True)
# {scanned, edges_removed, nodes_purged, assets_archived, assets_kept, vault_text_orphans}
```

**Phases:**
1. **Scanner** — finds nodes with `_deletion_flag`, expands cascade-delete targets
2. **Cascader** — removes all edges from/to deleted nodes
3. **Purger** — archives vault assets, permanently deletes nodes
4. **Compaction** — merges temp-files into base JSON files
5. **vault_text cleanup** — removes orphaned text files

Idempotent — safe to re-run after a crash.

---

## readonly views

`get_node`, `list_nodes`, and `find_nodes` accept `readonly=True`. This skips the deep copy and returns a direct reference to the internal cache. Significantly faster for large collections used in display-only contexts (list views, reports, exports).

**Contract:** never mutate the returned dict when using `readonly=True`.
