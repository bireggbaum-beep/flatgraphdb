# flatgraph

A graph database in a single Python file. Nodes and typed edges are stored as
plain JSON on disk, held in memory for reads, and written crash-safely. No
dependencies outside the standard library, Python 3.9+.

```python
from flatgraph import FlatGraphDB

with FlatGraphDB("./mydb") as db:
    db.create_node("step", "S1", {"name": "Cut"})
    db.create_node("step", "S2", {"name": "Weld"})
    db.create_node("step", "S3", {"name": "Paint"})
    db.create_edge("step/S1", "step/S2", "before")
    db.create_edge("step/S2", "step/S3", "before")

    db.get_node("step/S2")                    # {'name': 'Weld'}
    db.get_connected("step/S1")               # ['step/S2']
    db.traverse("step/S1", rel_type="before") # ['step/S2', 'step/S3']
    db.topological_sort("before")             # [['step/S1'], ['step/S2'], ['step/S3']]
```

**What you get**

- Nodes in collections (`"collection/id"`), directed or undirected typed
  edges with metadata, multi-hop traversal, shortest path, topological sort
  and cycle detection.
- Transactions that are all-or-nothing **on disk**, not only in memory, also
  across a crash.
- Every write goes to a work file, `fsync`, `os.replace`; no file is ever
  changed in place. Memory changes only after the disk has taken the change.
- Thread-safe; one open instance per database (a second one gets
  `BestandBelegt`).
- Two-step deletion: `soft_delete` to a trash, `restore_node`, then `purge`
  or the garbage collector. Cascades along edges marked `cascade_delete`.
- Optional schemas, edge constraints, audit log, change callback, offloading
  of long texts.
- Human-readable files: you can `grep` your data without the library.

**What you do not get**

- Not a server, no query language, no network access.
- Everything is held in memory. Measured up to 100 000 nodes and 200 000
  edges (open: 5.6 s, 588 MB).
- No checksums: a flipped bit that still yields valid JSON goes unnoticed.
- Crash safety is tested by killing processes at every file operation, not by
  cutting power. Whether `fsync` really reaches the platter is not tested.

## Install

Copy `flatgraph.py` into your project, or:

```bash
pip install git+<repository URL>
```

## The contract

What flatgraph promises, what it explicitly does not, what things cost and
how every claim was tested is in **[`VERTRAG.md`](VERTRAG.md)** (German, the
binding reference). Changes per version: [`CHANGELOG.txt`](CHANGELOG.txt).
Code comments and tests are in German too; the public API keeps English names
where it always had them.

## API at a glance

| Area | Methods |
|---|---|
| Open / close | `FlatGraphDB(root_dir, schemas=None, edge_constraints=None, audit=False, bei_aenderung=None, longtext_threshold=None)`, `close()`, `with … as db` |
| Nodes | `create_node`, `get_node`, `get_node_full`, `get_node_raw`, `update_node`, `list_nodes`, `list_collections`, `find_nodes`, `next_id` |
| Deleting | `soft_delete`, `restore_node`, `purge`, `run_garbage_collection`, `verwendungen` (who points here), `loeschfolgen` (what a delete takes along) |
| Edges | `create_edge(source, target, rel_type, meta=None, cascade_delete=False, gerichtet=True)`, `get_edge`, `delete_edge`, `reverse_edge`, `list_edges` |
| Graph | `get_connected`, `get_connected_edges`, `traverse`, `collect_related`, `find_path`, `topological_sort`, `detect_cycles` |
| Transactions | `with db.transaction(): …` (nested blocks join the outer one) |
| Checks | `schema_verstoesse` (report schema violations without raising) |
| Import / export | `import_json`, `import_csv`, `export_json`, `export_csv` |

`readonly=True` on reads skips the copy and hands out the cache itself: faster,
but never modify what you get.

**Errors** all inherit from `FlatGraphFehler` and additionally from the
built-in type callers used to catch, e.g. `KnotenFehlt` (node missing) is also
a `KeyError`, `NichtSpeicherbar` (value has no faithful JSON form) also a
`TypeError`, `DateiKaputt` (damaged file) also a `RuntimeError`,
`ZyklusGefunden` (cycle) also a `ValueError`. The full table is in
`VERTRAG.md` §4.

## On disk

```
root_dir/
  datenbank/
    _meta.json                          storage format number
    nodes/<collection>/fach_000001.json up to 25 nodes per file
    edges/<edge type>/fach_000001.json  up to 25 edges per file
  vault/  vault_archive/  vault_text/   attachments, archived ones, long texts
```

The storage format has a number (`SPEICHERFORM`, currently 3). Older
databases are migrated on open, with no way back; a library older than the
database refuses to open it (`SpeicherformZuNeu`). Keep a copy before
upgrading.

## Tests

No test framework, just the standard library. Each suite ends with
`n/n Checks bestanden` and exits 1 on a failure:

```bash
for t in tests/test_flatgraph_*.py; do python "$t" | tail -1; done
FLATGRAPH_DATEI=/path/to/other/flatgraph.py python tests/test_flatgraph_graph.py
```

Besides behaviour, the suites kill processes and inject I/O errors at every
file operation of every multi-file operation (`test_flatgraph_absturz.py`),
flip bits and damage files (`test_flatgraph_robustheit.py`), and run mixed
readers and writers, extreme values and a long run against a model
(`test_flatgraph_belastung.py`). `bench/lauf.py` measures performance.

## License

MIT, see [`LICENSE`](LICENSE).
