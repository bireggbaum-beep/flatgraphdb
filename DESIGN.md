# FlatGraphDB — Design & Invariants

This document states the **hard guarantees** of FlatGraphDB: what is durable,
what is atomic, what is visible when, and what happens on a crash. It is the
contract you can build on. The README is the tutorial; this is the spec.

If a behaviour is not stated here, do not assume it.

---

## 1. Storage layout

```
root/
  .flatgraph.lock                     lock file (used only when file_lock=True)
  datenbank/
    _meta.json                        per-collection revision counters
    _tx_<txid>.json                   transient transaction commit marker
    nodes/
      <collection>.json               AUTHORITATIVE — compacted base state
      <collection>_temp.json          AUTHORITATIVE — overlay of recent writes
      <file>.<txid>                   transient transaction staging file
      <file>.<uuid>.tmp               transient atomic-write scratch
    edges/
      <rel_type>.json                 AUTHORITATIVE — one file per edge type
    index/
      <collection>.json               DERIVED — field indexes, rebuildable
  vault/                              binary assets (referenced by convention)
  vault_text/<sha256>.txt             AUTHORITATIVE — immutable longtext blobs
  vault_archive/                      quarantine for purged assets
```

**File classes:**

- **Authoritative** — the source of truth. Lost data here is lost data.
- **Derived** — reconstructable from authoritative files. Safe to delete; will
  be rebuilt on demand. Currently: the field indexes.
- **Transient** — exists only during a write or between a crash and the next
  recovery. Never read as data. Recovery removes or applies it.

---

## 2. The core invariant

A node collection's on-disk state is exactly:

```
base file  +  temp file        (temp entries win over base entries)
```

`<collection>.json` holds the compacted base; `<collection>_temp.json` holds
writes made since the last compaction. A reader merges them with the temp file
taking precedence. Compaction (garbage collection) folds temp into base and
removes the temp file.

Everything else — staging files, commit markers, `.tmp` scratch, indexes — is
either transient scaffolding or derived. Outside of an in-progress commit or an
in-progress recovery, **only base + temp exist**, and the invariant above is
the whole truth.

---

## 3. Write path & durability

Every authoritative file is written through `_atomic_replace`:

1. write to a uniquely-named `.<uuid>.tmp` file
2. `fsync` the temp file
3. `os.replace()` it into place (atomic rename)
4. `fsync` the parent directory

**Guarantee:** after a crash — process kill *or* power loss — a reader sees
either the complete previous file or the complete new file. Never a truncated,
empty, or partially-written file.

Exceptions, by deliberate design:

- **Index files** are written atomically but **not** `fsync`'d (`durable=False`).
  They are derived data; a crash-lost or stale index is detected on read and
  rebuilt. Paying for `fsync` here would buy nothing.
- **`vault_text` blobs** are written durably, but with a unique temp name so the
  write needs no lock (see §10).

---

## 4. Visibility — when is data on disk

| Operation                         | On disk when…                                            |
| --------------------------------- | -------------------------------------------------------- |
| `create_node` / `update_node` / `soft_delete` / `restore_node` outside a transaction | the call returns (written to the temp file, durably)     |
| the same calls **inside** `transaction()` | the `with` block exits — buffered in RAM until then      |
| `create_edge` / `delete_edge` outside a transaction | the call returns                                         |
| anything inside `transaction()`   | the commit marker is durably written (the linearization point) — all of it, atomically |
| `flush()`                         | the call returns                                         |

The **RAM cache is authoritative for the live process** between its own writes.
A second process sees another process's writes only as described in §9.

`vault_text` blobs are an exception to the transaction rule: they are written
**eagerly and durably before the node persist**, even inside a transaction. This
is safe because the blob store is immutable (§8).

---

## 5. Transactions & atomicity

`transaction()` buffers all writes in RAM and commits them as one batch on exit.
On any exception inside the block, the RAM cache is restored to a pre-block
snapshot and nothing is written to disk.

**Single-collection commit** is atomic via the temp-file `os.replace()`.

**Multi-collection commit** is atomic via a staging-file + commit-marker
protocol, run entirely under one lock:

1. Compute every collection's and edge type's next file content. CAS conflicts
   (§10) surface here, before anything touches disk.
2. Write each next-content to a transaction-scoped staging file `<file>.<txid>`,
   durably.
3. Write one commit marker `datenbank/_tx_<txid>.json`. **Its durable creation
   is the commit point** — the single atomic act that flips the whole
   transaction from "not committed" to "committed".
4. Rename the staging files into place; apply the revision bumps to `_meta.json`.
5. Remove the marker.

The marker is transient scaffolding, not a permanent storage tier. Between
commits the store is back to plain base + temp (§2).

**Guarantee:** a crash at any point during a multi-collection commit resolves,
on the next open, to **either the whole transaction or none of it**.

---

## 6. Crash recovery

Recovery runs under the lock, in `_initialize_cache`, **before the cache is
loaded** — so every handle opens a consistent state.

| On-disk state found                    | Recovery action                          |
| --------------------------------------- | ---------------------------------------- |
| commit marker `_tx_<txid>.json`         | roll **forward**: re-apply its staging files and revision bumps (idempotent), then delete the marker |
| staging files `<file>.<txid>`, no marker | roll **back**: delete them                |
| a marker that fails to parse            | it never finished its `fsync`, so it was never a commit point → delete it; its staging files roll back |
| `.tmp` scratch from an interrupted write | delete it                                 |

What a process abort can leave, and how it resolves:

- **Mid single write** — the temp file is atomically the old or new content.
  The revision counter may be one ahead of the data (§8) — harmless.
- **Mid multi-collection commit, before the marker** — orphan staging files →
  rolled back.
- **Mid multi-collection commit, after the marker** — marker present → rolled
  forward.
- **Mid garbage collection** — GC is idempotent; just re-run it.
- **Mid `vault_text` blob write** — orphan `.tmp` scratch → swept. The
  content-addressed blob itself is atomically present or absent.
- **In-flight RAM state** (`_node_updates`, `_pending_creations`, dirty sets)
  is lost — it only ever mattered to the dead process's session.

---

## 7. Revision counters & index validity

Each collection has a monotonically increasing `revision` in `datenbank/_meta.json`,
incremented on every write to that collection. A persisted field index records
the revision it was built at; on load, a revision mismatch (or a parse failure)
**discards the index and rebuilds it** from the nodes.

This catches the case a checksum-over-ids cannot: a field-*value* change that
leaves the id set unchanged.

**Ordering rule for single writes:** the revision is bumped *before* the data
file is written. A crash in between therefore leaves `revision ≥ data`, which
only ever **over**-invalidates an index (a harmless rebuild) — never
**under**-invalidates it (which would serve stale results). Multi-collection
commits carry their revision bumps inside the marker, so they apply atomically
with the data.

Indexes are derived: deleting `datenbank/index/` at any time is safe.

---

## 8. `vault_text` longtext blobs

String fields longer than `longtext_threshold` are offloaded to
`vault_text/<sha256>.txt` and the node keeps an `@vault_text/...` reference.

Blobs are **content-addressed and therefore immutable**:

- An update writes a **new** blob; it never overwrites an existing one.
- Identical content is written once (deduplicated); re-saving an unchanged
  field writes nothing.
- A rolled-back or crashed write can only ever leave an **unreferenced orphan** —
  it can never corrupt the value an existing reference points at.

Because the store is immutable, blob writes need no part in the transaction
commit protocol. The cost is that superseded blobs accumulate as orphans until
the next `garbage_collection()` — the same orphan-until-GC model as `vault/`
binary assets.

---

## 9. Concurrency — what `file_lock=True` guarantees

`file_lock=True` is for **multiple OS processes / app instances of one user**
sharing a database directory. It is **not** a multi-user permission layer.

With `file_lock=True`, under an exclusive lock held for the whole write:

- **`create_node` is compare-and-swap.** If a peer process already created the
  same id, the local node is rolled back and `ConflictError` is raised. No
  silent overwrite.
- **`update_node` is a per-field merge.** The on-disk node is re-read inside the
  lock and the local field delta layered on top — two processes editing
  *disjoint* fields of the same node both survive.
- **Reads refresh stale caches.** `get_node`, `get_node_raw`, `get_node_full`,
  `list_nodes`, `find_nodes` check the collection's revision on entry and reload
  that one collection if a peer moved it ahead.

What `file_lock=True` does **not** give you:

- **No edge refresh.** `get_connected` and `traverse` read the local edge cache;
  a peer's edge writes are not picked up until the handle is reopened.
- **No real-time coherence.** Cache freshness is refresh-on-read. A handle that
  only writes, or only traverses edges, will not see peer updates.
- **Edges have no CAS.** Edge ids are random UUIDs; collisions are treated as
  effectively impossible. Concurrent add/delete deltas are merged by RMW.
- **Whole-node writers are last-writer-wins.** `restore_node` and the GC cascade
  flag write the whole node, not a field delta — concurrent with an
  `update_node` on the same node, the later flush wins wholesale.

With `file_lock=False` (the default): per-file writes are still atomic and
durable, but concurrent peers can lose updates and stale caches are never
refreshed. Use it only for single-process access.

---

## 10. Conflict policy

| Write                         | Policy on a concurrent peer write                      |
| ----------------------------- | ------------------------------------------------------ |
| `create_node` (same id)       | reject-on-conflict — `ConflictError`, local rollback   |
| `update_node` (disjoint fields) | merge — both survive                                  |
| `update_node` (same field)    | last-writer-wins (the later flush wins)                |
| `restore_node`, GC cascade flag | last-writer-wins, whole-node                          |
| edges                         | RMW merge of add/delete deltas; no CAS                 |

---

## 11. Idempotent operations

These are safe to call again after a failure or crash at any point:

- `run_garbage_collection()` — explicitly designed for it; re-run after any
  partial failure.
- transaction crash recovery (`_recover_transactions`, run on every open).
- commit-marker application (`_apply_tx_marker`).
- `restore_node()` — a no-op when the node is not soft-deleted (returns `False`).
- opening the database — recovery runs first, every time.

---

## 12. Soft-delete & garbage-collection lifecycle

```
soft_delete(node)        sets _deletion_flag — node now invisible to
                         get_node / list_nodes / find_nodes, still on disk
                         and still visible to get_node_raw
restore_node(node)       clears the flag — only valid before GC runs
run_garbage_collection() PHASE A  scan for _deletion_flag'd nodes
                         PHASE A  cascade-expand via cascade_delete edges
                         PHASE B  remove edges touching doomed nodes
                         PHASE C  archive/keep assets, purge nodes
                                  compact affected collections (temp → base)
                                  sweep unreferenced vault_text blobs
```

The GC is a separate `MaintenanceEngine` handle. It is idempotent and may run
on a schedule, at startup, or manually. A node is only *permanently* gone after
a GC run; until then a soft-delete is reversible.

---

## 13. Known limitations & explicit non-guarantees

- **No multi-user layer.** No identities, permissions, or per-user views.
- **No edge-cache refresh** across processes (§9).
- **Post-commit-point in-process failure is a false negative.** If applying a
  transaction's staging files raises *after* the commit marker is durable, the
  transaction *is* committed (recovery rolls it forward on the next open), but
  the calling process sees an exception as though it failed. Narrow, and the
  realistic failure here is a process kill, which has no exception to mis-report.
- **`vault_text` orphans accumulate** between GC runs under update-heavy
  longtext workloads (§8).
- **The GC's `vault_text` orphan sweep reads the GC process's cache.** A blob
  referenced only by a node a concurrent peer just created — and which the GC
  process has not loaded — could in principle be swept. Open a fresh GC handle
  *after* peer writes have committed.
- **Power-loss durability is not unit-tested.** It is reasoned about and the
  observable contract (atomic writes, no scratch leaks, recovery) is tested;
  true power-cut testing needs a VM-level harness.
