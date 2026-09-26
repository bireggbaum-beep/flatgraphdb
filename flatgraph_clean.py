"""
FlatGraphDB — a single-file, embedded graph database for Python. No external
dependencies.

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
  - Optional change callback (bei_aenderung=...)
"""

import contextlib
import copy
import functools
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import types
import urllib.parse
import uuid
import warnings
import weakref
from datetime import datetime, timezone


class FlatGraphFehler(Exception):
    """Base class of all flatgraph errors."""


class KnotenFehlt(FlatGraphFehler, KeyError):
    """The referenced node does not exist (or is soft-deleted).

    Also inherits from KeyError for backward compatibility with
    `except KeyError` call sites.
    """


class KnotenExistiert(FlatGraphFehler, KeyError):
    """A node with this ID already exists."""


class KanteFehlt(FlatGraphFehler, KeyError):
    """The referenced edge does not exist."""


class UngueltigeReferenz(FlatGraphFehler, ValueError):
    """A node reference is not in `collection/id` form."""


class UngueltigerName(FlatGraphFehler, ValueError):
    """A collection name, edge type, or ID that cannot be safely
    mapped to a filename."""


class NichtSpeicherbar(FlatGraphFehler, TypeError):
    """A value that would not come back unchanged from JSON.

    Checked BEFORE the in-memory store is changed, so a rejected write
    never leaves the cache and disk out of sync.

    Also inherits from TypeError, matching what `json` used to raise.
    """


class BestandBelegt(FlatGraphFehler, RuntimeError):
    """This store is already open — by another process or another
    instance in this one."""

    def __init__(self, pfad, im_selben_prozess=False):
        self.pfad, self.im_selben_prozess = pfad, im_selben_prozess
        if im_selben_prozess:
            text = (f"Der Bestand unter '{pfad}' ist in diesem Prozess schon "
                    f"geoeffnet. Eine Instanz je Bestand: die vorhandene "
                    f"weiterbenutzen oder vorher mit close() schliessen.")
        else:
            text = (f"Der Bestand unter '{pfad}' ist von einem anderen Prozess "
                    f"geoeffnet. Ein Bestand gehoert einem Prozess; den anderen "
                    f"beenden oder dort weiterarbeiten.")
        super().__init__(text)


class BestandGeschlossen(FlatGraphFehler, RuntimeError):
    """No more writes are accepted after `close()`."""


class NichtInTransaktion(FlatGraphFehler, RuntimeError):
    """This operation cannot be rolled back and therefore does not
    run inside a transaction."""


class DateiKaputt(FlatGraphFehler, RuntimeError):
    """A file of the store is unreadable or not valid JSON."""


class AbschlussHaengt(FlatGraphFehler, OSError):
    """The transaction is committed, but not all shards are in place yet.

    The intent file was already durably written when renaming the
    shards failed; the next open or write finishes the job.
    """


class SpeicherformZuNeu(FlatGraphFehler):
    """The store was written in a newer storage format than this
    version of flatgraph understands."""

    def __init__(self, gefunden, unterstuetzt, pfad):
        self.gefunden, self.unterstuetzt, self.pfad = gefunden, unterstuetzt, pfad
        super().__init__(
            f"Der Bestand unter '{pfad}' ist in Speicherform {gefunden} "
            f"geschrieben; diese flatgraph-Fassung versteht hoechstens "
            f"{unterstuetzt}. Eine neuere Fassung verwenden — die Daten sind "
            f"in Ordnung, nur zu neu fuer diesen Code."
        )


__version__ = "4.0.0-entwurf"
__grundlage__ = "2.2.0, Uebernahme vom 19.09.2026"

# Storage format version, independent of the library version.
#   1  one collection file per node type plus a delta file, rewritten whole
#      on every write.
#   2  one file per node (nodes/<collection>/<id>.json).
#   3  shards: up to FACH_GROESSE nodes per file (nodes/<collection>/fach_000001.json),
#      edges likewise per type. Filled, not scattered: a new node goes into
#      the last shard until it is full, then the next one starts.
SPEICHERFORM = 3

# Nodes/edges per shard. 25 was the measured sweet spot between file count
# and per-write cost.
FACH_GROESSE = 25

_log = logging.getLogger("flatgraph")

_RESERVED_EDGE_FIELDS  = {"source", "target", "type", "created_at", "_cascade_delete"}
_INTERNAL_COLLECTIONS  = {"_audit_log"}

# Node fields that flatgraph itself writes; only these are exempt from
# schema validation.
# _geloescht_durch: which node's cascade brought this one to the trash, so
# restore_node() recovers exactly that set.
_INTERNE_KNOTENFELDER  = {"_deletion_flag", "_keep_asset", "_geloescht_durch"}

# In the undo log: "did not exist before the transaction". A sentinel
# object because None is a valid value.
_FEHLTE = object()

# Collection and edge-type names become directory/file names, so only
# characters safe on every filesystem are allowed.
_NAMENSREGEL = re.compile(r"[^\W_][\w.-]{0,99}")


# =============================================================================
# FAECHER (Speicherform 3)
# =============================================================================

def _ordnername(name):
    """Collection/edge-type name as a directory name — reversible with unquote."""
    kodiert = name.replace("%", "%25").replace("/", "%2F").replace("\\", "%5C")
    if kodiert.startswith("."):
        kodiert = "%2E" + kodiert[1:]
    return kodiert


class _Ablage:
    """Which ID lives in which shard, for one collection or edge type.

    RAM-only, rebuilt on open. New entries go into the last shard until it
    is full; gaps only appear when the GC purges entries, and it is also
    the GC that closes them again (FlatGraphDB._verdichten).
    """

    def __init__(self, verzeichnis):
        self.verzeichnis = verzeichnis
        self.fach_von = {}      # kennung -> fachnummer
        self.inhalt = {}        # fachnummer -> {kennungen}

    def pfad(self, nr):
        return os.path.join(self.verzeichnis, f"fach_{nr:06d}.json")

    def eintragen(self, kennung, nr):
        self.fach_von[kennung] = nr
        self.inhalt.setdefault(nr, set()).add(kennung)

    def zuordnen(self, kennung):
        nr = self.fach_von.get(kennung)
        if nr is not None:
            return nr
        letztes = max(self.inhalt) if self.inhalt else 0
        if letztes == 0 or len(self.inhalt[letztes]) >= FACH_GROESSE:
            letztes += 1
        self.eintragen(kennung, letztes)
        return letztes

    def entfernen(self, kennung):
        nr = self.fach_von.pop(kennung, None)
        if nr is not None:
            self.inhalt[nr].discard(kennung)
        return nr


# =============================================================================
# PROZESSSPERRE
# =============================================================================
# One open instance per store, across process boundaries and within the
# same process: two instances writing independently would silently diverge.
# `flock` handles cross-process locking; within a process this module keeps
# its own bookkeeping of which stores are open, since a second `flock` on a
# freshly opened fd is not reliably rejected on every platform.
# Released by `close()` or when the instance is collected; the OS releases
# the lock automatically if the process dies.

_SPERREN = {}                      # echter Pfad -> Dateideskriptor
_SPERREN_SCHUTZ = threading.Lock()


def _sperre_nehmen(root_dir):
    schluessel = os.path.realpath(root_dir)
    with _SPERREN_SCHUTZ:
        if schluessel in _SPERREN:
            raise BestandBelegt(root_dir, im_selben_prozess=True)
        fd = os.open(os.path.join(root_dir, ".flatgraph.lock"),
                     os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise BestandBelegt(root_dir) from e
        _SPERREN[schluessel] = fd
        return schluessel


def _sperre_freigeben(schluessel):
    with _SPERREN_SCHUTZ:
        fd = _SPERREN.pop(schluessel, None)
        if fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


# =============================================================================
# MAIN ENGINE
# =============================================================================

class FlatGraphDB:
    def __init__(self, root_dir, schemas=None, edge_constraints=None,
                 file_lock=False, audit=False, webhooks=None, bei_aenderung=None,
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
        :param file_lock:  Accepted but has no effect, kept for old call sites. Every
                           store is process-locked on open (BestandBelegt).
        :param audit:      Automatically write audit entries to _audit_log on every write.
        :param bei_aenderung: Callback invoked with a dict after each change:
                           {"ereignis", "ref", "sammlung"/"kantenart", "zeit"}. Inside a
                           transaction only after a successful commit, never on rollback.
                           An error inside it is logged (logger "flatgraph") and does not
                           undo the change.
        :param webhooks:   Removed in 4.0 — raises TypeError. Use bei_aenderung instead.
        :param edge_constraints: Optional dict {rel_type: [(source_collection, target_collection), ...]}
                                 restricting which collection pairs are valid for each edge type.
                                 Example: {"gehört-zu-plant": [("equipment", "plant"),
                                                               ("software",   "plant")]}
        :param longtext_threshold: If set (int), string fields longer than this many characters
                                   are automatically offloaded to vault_text/ as plain-text files.
                                   The node field stores an "@vault_text/..." reference instead.
                                   Use get_node_full() to resolve references back to full text.
        """
        # Instance lock, before anything else. Re-entrant because public
        # methods call each other and a thread must be able to keep
        # writing inside its own transaction.
        self._sperre = threading.RLock()
        if file_lock:
            warnings.warn(
                "file_lock hat keine Wirkung mehr: jeder Bestand wird beim "
                "Oeffnen fuer den Prozess gesperrt.", DeprecationWarning, stacklevel=2)
        self.root = root_dir
        self.schemas = schemas or {}
        self.edge_constraints = edge_constraints or {}
        self.audit = audit
        if webhooks:
            raise TypeError(
                "webhooks gibt es ab flatgraph 4.0 nicht mehr: flatgraph "
                "verschickt kein HTTP. Stattdessen bei_aenderung=<funktion> "
                "uebergeben und dort selbst versenden.")
        self.bei_aenderung = bei_aenderung
        self._meldungen = []         # in einer Transaktion gepufferte Meldungen
        self.longtext_threshold = longtext_threshold
        self._audit_writing = False  # prevents recursive audit entries
        self._geschlossen = False

        # Lock first — before migrating an older storage format (which
        # writes) and before loading, which would otherwise race another
        # process's changes.
        os.makedirs(root_dir, exist_ok=True)
        schluessel = _sperre_nehmen(root_dir)
        # No reference to self in the finalizer, or it would keep the
        # instance alive and the lock would never be released.
        self._freigeben = weakref.finalize(self, _sperre_freigeben, schluessel)
        try:
            self._oeffnen()
        except BaseException:
            # If opening fails (DateiKaputt, SpeicherformZuNeu) there is no
            # instance to close, so release the lock here.
            self._freigeben()
            raise

    def _oeffnen(self):
        root_dir = self.root

        self.dirs = {
            "nodes":        os.path.join(root_dir, "datenbank", "nodes"),
            "edges":        os.path.join(root_dir, "datenbank", "edges"),
            "vault":        os.path.join(root_dir, "vault"),
            "vault_archive":os.path.join(root_dir, "vault_archive"),
            "vault_text":   os.path.join(root_dir, "vault_text"),
        }
        for path in self.dirs.values():
            os.makedirs(path, exist_ok=True)

        # RAM cache: edges = {rel_type: {edge_id: edge_data}}
        self._cache = {"nodes": {}, "edges": {}}
        self._edge_type_index = {}   # {edge_id: rel_type} — reverse index, RAM only
        # Neighborhood indexes: {node_ref: {edge_type: {edge_id: edge}}} — RAM only,
        # rebuilt from the edges on open.
        self._out_index = {}
        self._in_index = {}
        self._index_cache = {}       # {collection: {field: {wert_klein: {kennungen}}}} — nur RAM
        self._dirty_nodes  = {}      # {collection: set(node_ids)} — pending temp-file writes
        self._dirty_edges  = {}      # {kantenart: {kanten_ids}} — in einer Transaktion gepuffert
        # ID -> shard, per collection/edge type; built on open.
        self._ablagen = {"nodes": {}, "edges": {}}
        self._transaction_depth = 0  # >0 = active transaction, writes are buffered
        # Undo log of the running transaction, else None. Keys:
        #   ("knoten", sammlung, id)  -> node before first change (copy) or _FEHLTE
        #   ("kante", art, id)        -> edge before first change or _FEHLTE
        #   ("sammlung", sammlung)    -> the collection did not exist before
        #   ("kantenart", art)        -> the edge type did not exist before
        self._undo = None
        self._purged_nodes   = {}    # {collection: set(node_ids)} — vom Muellsammler endgueltig geloescht
        # Before loading, not after: a store in a newer format must not be
        # loaded half-way.
        self._speicherform_pruefen()
        # Before reading: a committed but interrupted transaction is
        # finished first, then the store is read.
        self._absicht_haengt = False
        self._plan = None
        self._absicht_nachholen()
        self._initialize_cache()

    def close(self):
        """Close the instance and release its share of the process lock.

        Writes outside a transaction are already on disk; close() flushes
        nothing further. After this, every write raises BestandGeschlossen.
        Calling close() more than once is harmless.
        """
        self._geschlossen = True
        self._freigeben()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _offen_pruefen(self):
        if self._geschlossen:
            raise BestandGeschlossen(
                f"Die Instanz fuer '{self.root}' ist geschlossen; es wird "
                f"nicht mehr geschrieben.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _edges_file(self, rel_type):
        safe = rel_type.replace("/", "_").replace("\\", "_")
        return os.path.join(self.dirs["edges"], f"{safe}.json")

    def _persist_kanten(self, rel_type, edge_ids):
        """Persist changed edges to disk — or buffer inside a transaction."""
        if self._transaction_depth > 0:
            self._dirty_edges.setdefault(rel_type, set()).update(edge_ids)
            return
        self._flush_kanten(rel_type, edge_ids)

    def _flush_kanten(self, rel_type, edge_ids):
        """Write the shards these edges live (or lived) in."""
        self._ablage_schreiben("edges", rel_type, edge_ids,
                               self._cache["edges"].get(rel_type, {}))

    # --- Nachbarschaftsindizes -----------------------------------------
    # These three are the only places that mutate _out_index/_in_index.
    # Anything that touches edges without going through them produces an
    # index that disagrees with the store.

    def _index_edge(self, edge_id, edge_data):
        rel_type = edge_data.get("type", "_unknown")
        self._out_index.setdefault(edge_data["source"], {}) \
                       .setdefault(rel_type, {})[edge_id] = edge_data
        self._in_index.setdefault(edge_data["target"], {}) \
                      .setdefault(rel_type, {})[edge_id] = edge_data

    def _unindex_edge(self, edge_id, edge_data):
        rel_type = edge_data.get("type", "_unknown")
        for index, ref in ((self._out_index, edge_data["source"]),
                           (self._in_index, edge_data["target"])):
            eimer = index.get(ref, {}).get(rel_type)
            if eimer is not None:
                eimer.pop(edge_id, None)
                # Prune empty buckets, or the index grows forever with
                # every node that ever had edges.
                if not eimer:
                    del index[ref][rel_type]
                    if not index[ref]:
                        del index[ref]

    def _rebuild_edge_indexes(self, rel_type=None):
        """Rebuild from the edges. No argument: everything; else one type."""
        if rel_type is None:
            self._out_index, self._in_index = {}, {}
            eimer = self._cache["edges"].items()
        else:
            for index in (self._out_index, self._in_index):
                for ref in list(index):
                    index[ref].pop(rel_type, None)
                    if not index[ref]:
                        del index[ref]
            eimer = [(rel_type, self._cache["edges"].get(rel_type, {}))]
        for art, bucket in eimer:
            for edge_id, edge in bucket.items():
                self._index_edge(edge_id, edge)

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

    def _meta_datei(self):
        return os.path.join(self.root, "datenbank", "_meta.json")

    def _speicherform_pruefen(self):
        """Refuse to silently load a newer-format store as empty.

        Missing marker means the store predates this check and is read as
        the current format, then the marker is added.
        """
        pfad = self._meta_datei()
        gefunden = None
        if os.path.exists(pfad):
            daten = self._load_json_from_disk(pfad)
            gefunden = daten.get("speicherform")
            if isinstance(gefunden, int) and gefunden > SPEICHERFORM:
                raise SpeicherformZuNeu(gefunden, SPEICHERFORM, self.root)
            if gefunden == SPEICHERFORM:
                self._umzugsreste_wegraeumen()
                return
        # Marker missing or names an older format: migrate step by step.
        if gefunden in (None, 1):
            self._migriere_auf_eine_datei_je_knoten()
        self._migriere_auf_faecher()
        self._marke_schreiben()
        self._umzugsreste_wegraeumen()

    def _marke_schreiben(self):
        self._save_json_atomic(self._meta_datei(), {
            "speicherform": SPEICHERFORM,
            "langtext_schwelle": self.longtext_threshold,
            "geschrieben_von": __version__,
            "geaendert": datetime.now(timezone.utc).isoformat(),
        })

    def _langtexte_nachziehen(self):
        """Offload any longtexts still stored inline in nodes.

        Needed because normal offloading only catches new/changed nodes.
        The threshold is stored in the format marker; this pass runs once
        whenever it changes (or is unset because the store predates it).
        """
        if not self.longtext_threshold:
            return
        marke = self._load_json_from_disk(self._meta_datei())
        if marke.get("langtext_schwelle") == self.longtext_threshold:
            return
        for collection, knoten in self._cache["nodes"].items():
            geaendert = []
            for node_id, daten in list(knoten.items()):
                vorher = dict(daten)
                self._offload_longtexts(collection, node_id, daten)
                if daten != vorher:
                    geaendert.append(node_id)
            if geaendert:
                self._knoten_schreiben(collection, geaendert)
        # Marker last, same reasoning as the format migration.
        self._marke_schreiben()

    def _migriere_auf_eine_datei_je_knoten(self):
        """Form 1 -> 2: collection file + delta become one file per node.

        Order matters: all new files are written first, then the old ones
        removed, and the caller sets the marker last. An interrupted run
        is safely restarted from scratch.
        """
        wurzel = self.dirs["nodes"]
        if not os.path.isdir(wurzel):
            return
        sammeldateien = sorted(
            d for d in os.listdir(wurzel)
            if d.endswith(".json") and not d.endswith("_temp.json"))
        for dateiname in sammeldateien:
            collection = dateiname[:-5]
            basis = self._load_json_from_disk(self._sammeldatei(collection))
            delta = self._load_json_from_disk(self._temp_file(collection))
            zusammen = {**basis, **delta}
            verzeichnis = self._knoten_verzeichnis(collection)
            os.makedirs(verzeichnis, exist_ok=True)
            for node_id, knoten in zusammen.items():
                # Offload longtexts here too, the one chance to catch an
                # old store's inline text during migration.
                self._offload_longtexts(collection, node_id, knoten)
                self._save_json_atomic(
                    self._knoten_datei(collection, node_id), knoten)
            # Remove the old files only now, once the new ones are complete.
            for alt in (self._sammeldatei(collection), self._temp_file(collection)):
                if os.path.exists(alt):
                    os.remove(alt)

    def _umzugsorte(self):
        db = os.path.join(self.root, "datenbank")
        return {
            "alt_nodes": os.path.join(db, "nodes_form2"),
            "alt_edges": os.path.join(db, "edges_form2"),
            "neu_nodes": os.path.join(db, "nodes_form3"),
            "neu_edges": os.path.join(db, "edges_form3"),
        }

    def _umzugsreste_wegraeumen(self):
        """Remove the form-2 directories the migration set aside.

        Only after the marker is written — before that they are the
        fallback if the migration is interrupted.
        """
        orte = self._umzugsorte()
        for schluessel in ("alt_nodes", "alt_edges"):
            shutil.rmtree(orte[schluessel], ignore_errors=True)

    def _migriere_auf_faecher(self):
        """Form 2 -> 3: one file per node/edge-type becomes shards.

        Order matters again, and the marker is set last by the caller:
          1. Build the new nodes_form3/ and edges_form3/ fully.
          2. Rename the old dirs aside (nodes -> nodes_form2, edges -> edges_form2).
          3. Rename the new dirs into place.
          4. (caller) write the marker, then delete the set-aside dirs.
        """
        orte = self._umzugsorte()
        nodes, edges = self.dirs["nodes"], self.dirs["edges"]

        if not os.path.exists(orte["alt_nodes"]):
            for ort in (orte["neu_nodes"], orte["neu_edges"]):
                shutil.rmtree(ort, ignore_errors=True)
                os.makedirs(ort)
            self._migrate_legacy_edges()
            for eintrag in sorted(os.listdir(nodes)):
                verzeichnis = os.path.join(nodes, eintrag)
                if not os.path.isdir(verzeichnis):
                    continue
                knoten = {
                    urllib.parse.unquote(d[:-5]): self._load_json_from_disk(
                        os.path.join(verzeichnis, d))
                    for d in os.listdir(verzeichnis) if d.endswith(".json")}
                self._ablage_befuellen(
                    os.path.join(orte["neu_nodes"], _ordnername(eintrag)), knoten)
            for dateiname in sorted(os.listdir(edges)):
                if not dateiname.endswith(".json"):
                    continue
                kanten = self._load_json_from_disk(os.path.join(edges, dateiname))
                for kante in kanten.values():
                    self._translate_legacy_edge(kante)
                self._ablage_befuellen(
                    os.path.join(orte["neu_edges"], _ordnername(dateiname[:-5])), kanten)
            os.rename(nodes, orte["alt_nodes"])

        if (not os.path.exists(orte["alt_edges"]) and os.path.exists(edges)
                and os.path.exists(orte["neu_edges"])):
            os.rename(edges, orte["alt_edges"])

        for neu, ziel in ((orte["neu_nodes"], nodes), (orte["neu_edges"], edges)):
            if not os.path.exists(neu):
                continue
            # Windows does not replace a non-empty rename target the way
            # Linux does with an empty dir; this is a no-op guard there.
            if os.path.isdir(ziel) and not os.listdir(ziel):
                os.rmdir(ziel)
            os.rename(neu, ziel)
        for pfad in (nodes, edges):
            os.makedirs(pfad, exist_ok=True)

    def _ablage_befuellen(self, verzeichnis, daten):
        """Fill shards in order and write them (migration only)."""
        ablage = _Ablage(verzeichnis)
        for k in sorted(daten):
            ablage.zuordnen(k)
        for pfad, inhalt in self._faecher_planen(ablage, set(ablage.inhalt), daten):
            self._save_json_atomic(pfad, inhalt)

    def _initialize_cache(self):
        """Read every shard once; this also builds the ID -> shard mapping."""
        self._ablagen = {"nodes": {}, "edges": {}}
        zu_reparieren = []
        for art in ("nodes", "edges"):
            wurzel = self.dirs[art]
            if not os.path.isdir(wurzel):
                continue
            for eintrag in sorted(os.listdir(wurzel)):
                verzeichnis = os.path.join(wurzel, eintrag)
                if not os.path.isdir(verzeichnis):
                    continue
                name = urllib.parse.unquote(eintrag)
                ablage = _Ablage(verzeichnis)
                self._ablagen[art][name] = ablage
                daten, reparieren = self._ablage_laden(ablage)
                if art == "nodes":
                    self._cache["nodes"][name] = daten
                else:
                    for edge_id in daten:
                        self._edge_type_index[edge_id] = name
                    self._cache["edges"][name] = daten
                if reparieren:
                    zu_reparieren.append((ablage, reparieren, daten))
        self._rebuild_edge_indexes()
        # Now that everything is read: flush any leftovers of an
        # interrupted compaction before anything else changes.
        for ablage, reparieren, daten in zu_reparieren:
            self._faecher_schreiben(ablage, reparieren, daten)
        self._langtexte_nachziehen()

    def _load_json_from_disk(self, filepath):
        """Load a JSON file from disk. Missing file → empty dict. Corrupted file → RuntimeError."""
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise DateiKaputt(f"File '{filepath}' is not valid JSON: {e}") from e
        except IOError as e:
            raise DateiKaputt(f"Could not read file '{filepath}': {e}") from e

    def _save_json_atomic(self, filepath, data):
        """Atomic write: write to .tmp first, fsync, then os.replace()."""
        self._offen_pruefen()
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        temp_file = filepath + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            # allow_nan=False: NaN is not valid JSON.
            json.dump(data, f, indent=2, ensure_ascii=False, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, filepath)
        # The directory entry itself must also be durable.
        try:
            dir_fd = os.open(os.path.dirname(filepath) or ".", os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass

    def _temp_file(self, collection_name):
        """Only used for migrating format 1: the old delta file."""
        return os.path.join(self.dirs["nodes"], f"{collection_name}_temp.json")

    def _sammeldatei(self, collection_name):
        """Only used for migrating format 1: the old collection file."""
        return os.path.join(self.dirs["nodes"], f"{collection_name}.json")

    def _knoten_verzeichnis(self, collection_name):
        return os.path.join(self.dirs["nodes"], collection_name)

    def _knoten_datei(self, collection_name, node_id):
        """One node, one file. The filename is the percent-encoded ID."""
        name = urllib.parse.quote(str(node_id), safe="")
        return os.path.join(self._knoten_verzeichnis(collection_name), f"{name}.json")

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
        self._knoten_schreiben(collection_name, dirty_ids)

    def _knoten_schreiben(self, collection_name, node_ids):
        """The single place nodes are written to disk."""
        self._ablage_schreiben("nodes", collection_name, node_ids,
                               self._cache["nodes"].get(collection_name, {}))

    def _ablage(self, art, name):
        ablage = self._ablagen[art].get(name)
        if ablage is None:
            ablage = _Ablage(os.path.join(self.dirs[art], _ordnername(name)))
            self._ablagen[art][name] = ablage
        return ablage

    def _ablage_schreiben(self, art, name, kennungen, daten):
        ablage = self._ablage(art, name)
        nummern = set()
        # Sorted: a transaction hands us a set, whose order varies between
        # runs, and the same ID must always land in the same shard.
        for k in sorted(kennungen):
            nr = ablage.zuordnen(k) if k in daten else ablage.entfernen(k)
            if nr is not None:
                nummern.add(nr)
        self._faecher_schreiben(ablage, nummern, daten)

    def _faecher_schreiben(self, ablage, nummern, daten):
        schritte = self._faecher_planen(ablage, nummern, daten)
        if self._plan is not None:
            self._plan.extend(schritte)       # Abschluss einer Transaktion
        else:
            self._schritte_ausfuehren(schritte)

    def _faecher_planen(self, ablage, nummern, daten):
        """Which shards get which content written (None: deleted)."""
        schritte = []
        for nr in sorted(nummern):
            mitglieder = ablage.inhalt.get(nr, set())
            # Anything no longer in memory (e.g. after a rollback) does
            # not belong in the shard either.
            for k in [k for k in mitglieder if k not in daten]:
                ablage.entfernen(k)
            inhalt = {k: daten[k] for k in sorted(mitglieder)}
            if not inhalt:
                ablage.inhalt.pop(nr, None)
            schritte.append((ablage.pfad(nr), inhalt or None))
        return schritte

    # --- Absichtsdatei -------------------------------------------------
    # A multi-shard write goes through an intent file so it lands on disk
    # atomically as a whole:
    #   1. write each new shard as <fach>.neu durably
    #   2. write datenbank/_absicht.json durably — the transaction is now committed
    #   3. rename the .neu files into place, remove empties
    #   4. delete the intent file
    # `_absicht_nachholen` finishes a found intent on open; every step is
    # repeatable. A single shard doesn't need any of this — os.replace is
    # already atomic for one file.

    def _absicht_datei(self):
        return os.path.join(os.path.dirname(self.dirs["nodes"]), "_absicht.json")

    def _schritte_ausfuehren(self, schritte):
        self._offen_pruefen()
        if self._absicht_haengt:
            # Finish the previous transaction first, or its recovery would
            # overwrite what is being written now.
            self._absicht_nachholen()
        if not schritte:
            return
        if len(schritte) == 1:
            pfad, inhalt = schritte[0]
            if inhalt is not None:
                self._save_json_atomic(pfad, inhalt)
            elif os.path.exists(pfad):
                os.remove(pfad)
            return

        neue = []
        try:
            for pfad, inhalt in schritte:
                if inhalt is None:
                    continue
                os.makedirs(os.path.dirname(pfad), exist_ok=True)
                neue.append(pfad + ".neu")
                with open(pfad + ".neu", "w", encoding="utf-8") as f:
                    json.dump(inhalt, f, indent=2, ensure_ascii=False, allow_nan=False)
                    f.flush()
                    os.fsync(f.fileno())
            for verzeichnis in {os.path.dirname(p) for p, _ in schritte}:
                self._verzeichnis_sichern(verzeichnis)
            absicht = {
                "absicht": 1,
                "ersetzen": [self._relativ(p) for p, i in schritte if i is not None],
                "entfernen": [self._relativ(p) for p, i in schritte if i is None],
            }
            self._save_json_atomic(self._absicht_datei(), absicht)
        except BaseException:
            # Before commit: nothing happened except leftover .neu files.
            for neu in neue:
                try:
                    os.remove(neu)
                except OSError:
                    pass
            raise
        # From here on the transaction is committed.
        try:
            self._absicht_nachholen()
        except Exception as e:
            self._absicht_haengt = True
            raise AbschlussHaengt(
                f"Die Transaktion ist festgeschrieben, aber nicht alle Faecher "
                f"stehen schon an ihrem Platz ({e}). Der naechste "
                f"Schreibvorgang oder das naechste Oeffnen holt es nach.") from e

    def _relativ(self, pfad):
        return os.path.relpath(pfad, self.root).replace(os.sep, "/")

    def _verzeichnis_sichern(self, verzeichnis):
        try:
            fd = os.open(verzeichnis, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, AttributeError):
            pass

    def _absicht_nachholen(self):
        """Finish a committed intent. Repeatable."""
        datei = self._absicht_datei()
        if os.path.exists(datei + ".tmp"):
            # An intent that never made it into place never took effect.
            os.remove(datei + ".tmp")
        if not os.path.exists(datei):
            self._absicht_haengt = False
            return
        absicht = self._load_json_from_disk(datei)
        ersetzen = [self._absicht_pfad(r, datei) for r in self._absicht_liste(absicht, "ersetzen", datei)]
        entfernen = [self._absicht_pfad(r, datei) for r in self._absicht_liste(absicht, "entfernen", datei)]
        for pfad in ersetzen:
            # Missing .neu means this step already ran.
            if os.path.exists(pfad + ".neu"):
                os.replace(pfad + ".neu", pfad)
        for pfad in entfernen:
            if os.path.exists(pfad):
                os.remove(pfad)
        for verzeichnis in {os.path.dirname(p) for p in ersetzen + entfernen}:
            self._verzeichnis_sichern(verzeichnis)
        os.remove(datei)
        self._verzeichnis_sichern(os.path.dirname(datei))
        self._absicht_haengt = False

    @staticmethod
    def _absicht_liste(absicht, schluessel, datei):
        if not isinstance(absicht, dict) or absicht.get("absicht") != 1:
            raise DateiKaputt(f"'{datei}' ist keine Absichtsdatei dieser Fassung.")
        liste = absicht.get(schluessel, [])
        if not isinstance(liste, list) or not all(isinstance(r, str) for r in liste):
            raise DateiKaputt(f"'{datei}': '{schluessel}' ist keine Liste von Pfaden.")
        return liste

    def _absicht_pfad(self, relativ, datei):
        """Only shards under nodes/ and edges/ — an intent file is data
        read from disk and must not rename or delete anything else."""
        pfad = os.path.normpath(os.path.join(self.root, relativ))
        erlaubt = any(os.path.dirname(os.path.dirname(pfad)) == os.path.normpath(self.dirs[art])
                      for art in ("nodes", "edges"))
        if not erlaubt or not re.fullmatch(r"fach_\d+\.json", os.path.basename(pfad)):
            raise DateiKaputt(f"'{datei}' nennt '{relativ}' — das ist kein Fach dieses Bestands.")
        return pfad

    def _ablage_laden(self, ablage, aufraeumen=True):
        """Read all shards of one collection/edge type. Returns (daten, zu_reparieren).

        An ID found in two shards can only come from an interrupted
        compaction; the copy in the higher-numbered (newer) shard wins and
        the other is scheduled for removal. If the two copies differ, it
        is not a recoverable interruption but real corruption.
        """
        daten, reparieren = {}, set()
        if not os.path.isdir(ablage.verzeichnis):
            return daten, reparieren
        for dateiname in sorted(os.listdir(ablage.verzeichnis)):
            if dateiname.endswith(".json.neu"):
                # Without an intent file (already recovered before this
                # runs), a leftover .neu belongs to a never-committed
                # transaction.
                if aufraeumen:
                    os.remove(os.path.join(ablage.verzeichnis, dateiname))
                continue
            treffer = re.fullmatch(r"fach_(\d+)\.json", dateiname)
            if not treffer:
                continue
            nr = int(treffer.group(1))
            pfad = ablage.pfad(nr)
            for k, v in self._load_json_from_disk(pfad).items():
                if k in daten:
                    if daten[k] != v:
                        raise DateiKaputt(
                            f"'{k}' steht in zwei Faechern mit verschiedenem "
                            f"Inhalt ({ablage.pfad(ablage.fach_von[k])} und {pfad}).")
                    reparieren.add(ablage.entfernen(k))
                daten[k] = v
                ablage.eintragen(k, nr)
        return daten, reparieren

    def _flush_pending_writes(self):
        """Flush all buffered node and edge writes to disk (transaction commit).

        All shards go through as a single plan, via the intent file, so
        after a crash it is either fully on disk or not at all.
        """
        beruehrt = [("nodes", n) for n in self._dirty_nodes] + [("edges", a) for a in self._dirty_edges]
        self._plan = []
        try:
            for collection_name in list(self._dirty_nodes.keys()):
                dirty_ids = self._dirty_nodes.pop(collection_name, set())
                if not dirty_ids:
                    continue
                self._knoten_schreiben(collection_name, dirty_ids)
            for rel_type, edge_ids in list(self._dirty_edges.items()):
                self._flush_kanten(rel_type, edge_ids)
            self._dirty_edges.clear()
            plan = self._plan
        finally:
            self._plan = None
        try:
            self._schritte_ausfuehren(plan)
        except AbschlussHaengt:
            raise
        except BaseException:
            # Not committed: disk still has the old state, but planning
            # already reassigned IDs to shards — reload those shards'
            # assignment from disk so the next write does not duplicate them.
            for art, name in beruehrt:
                alt = self._ablagen[art].get(name)
                if alt is not None:
                    frisch = _Ablage(alt.verzeichnis)
                    # No cleanup here: the original error should reach the
                    # caller, not a follow-on error from deleting a .neu.
                    self._ablage_laden(frisch, aufraeumen=False)
                    self._ablagen[art][name] = frisch
            raise

    def flush(self):
        """Write all buffered writes to disk immediately. Useful outside transaction()."""
        self._flush_pending_writes()

    @contextlib.contextmanager
    def transaction(self):
        """
        Atomic transaction: all writes are buffered in RAM and flushed to disk as a
        batch on exit. On exception: full rollback. Nested transactions join the outer one.

        Holds the instance lock over the whole block, so another thread's
        write can never land in this transaction's buffer and vanish on
        rollback without ever seeing an error.
        """
        with self._sperre:
            yield from self._transaktion_ohne_sperre()

    def _transaktion_ohne_sperre(self):
        if self._transaction_depth > 0:
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
            return

        # Each change records its prior state before its first touch
        # (_vormerken_*); rollback only resets those entries, so cost
        # scales with what the transaction touches, not the store size.
        self._undo = {}
        self._transaction_depth = 1
        try:
            yield
            self._transaction_depth = 0
            self._flush_pending_writes()
            self._undo = None
        except AbschlussHaengt:
            # Committed: memory reflects what is on disk once recovery
            # finishes. Rolling back would contradict that.
            self._undo = None
            meldungen, self._meldungen = self._meldungen, []
            self._zustellen(meldungen)
            raise
        except Exception:
            self._meldungen = []
            # Also covers errors during the write itself: they happen
            # before the intent is committed (else AbschlussHaengt), so
            # disk and memory both still show the old state.
            self._transaction_depth = 0
            self._rueckgaengig()
            raise
        # Only now, with everything on disk, and outside the try: an error
        # in the callback is not an error of the transaction.
        meldungen, self._meldungen = self._meldungen, []
        self._zustellen(meldungen)

    # --- Undo-Log ----------------------------------------------------
    # Every place that changes a node or edge in memory inside a
    # transaction must call one of these first, or rollback will not
    # undo it.

    def _vormerken_knoten(self, collection, node_id):
        if self._undo is None:
            return
        if collection not in self._cache["nodes"]:
            self._undo.setdefault(("sammlung", collection), True)
        schluessel = ("knoten", collection, node_id)
        if schluessel in self._undo:
            return      # only the state BEFORE the first change counts
        alt = self._cache["nodes"].get(collection, {}).get(node_id, _FEHLTE)
        # A copy: update_node mutates the node in place.
        self._undo[schluessel] = alt if alt is _FEHLTE else copy.deepcopy(alt)

    def _vormerken_kante(self, rel_type, edge_id):
        if self._undo is None:
            return
        if rel_type not in self._cache["edges"]:
            self._undo.setdefault(("kantenart", rel_type), True)
        schluessel = ("kante", rel_type, edge_id)
        if schluessel in self._undo:
            return
        # No copy: edges are never mutated in place, only created/deleted,
        # and keeping the same object also keeps the neighborhood index valid.
        self._undo[schluessel] = self._cache["edges"].get(rel_type, {}).get(edge_id, _FEHLTE)

    def _rueckgaengig(self):
        undo, self._undo = self._undo or {}, None
        for schluessel, alt in undo.items():
            if schluessel[0] == "knoten":
                _, col, nid = schluessel
                self._index_austragen(col, nid)
                knoten = self._cache["nodes"].setdefault(col, {})
                if alt is _FEHLTE:
                    knoten.pop(nid, None)
                else:
                    knoten[nid] = alt
                self._index_eintragen(col, nid)
            elif schluessel[0] == "kante":
                _, art, eid = schluessel
                eimer = self._cache["edges"].setdefault(art, {})
                jetzt = eimer.pop(eid, None)
                if jetzt is not None:
                    self._unindex_edge(eid, jetzt)
                    self._edge_type_index.pop(eid, None)
                if alt is not _FEHLTE:
                    eimer[eid] = alt
                    self._edge_type_index[eid] = art
                    self._index_edge(eid, alt)
        # Collections/edge types created only within the transaction:
        # remove them now that their entries are reset.
        for schluessel in undo:
            if schluessel[0] == "sammlung" and not self._cache["nodes"].get(schluessel[1]):
                self._cache["nodes"].pop(schluessel[1], None)
                self._index_cache.pop(schluessel[1], None)
            elif schluessel[0] == "kantenart" and not self._cache["edges"].get(schluessel[1]):
                self._cache["edges"].pop(schluessel[1], None)
        self._dirty_nodes.clear()
        self._dirty_edges.clear()

    def _persist_collection_full(self, collection_name):
        """In format 2+ this only handles cleanup after the GC."""
        purged = self._purged_nodes.pop(collection_name, set())
        if purged:
            self._knoten_schreiben(collection_name, purged)
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

    # --- Pre-write checks ------------------------------------------------
    # In-memory state only changes once disk is known to accept the change.

    @staticmethod
    def _name_pruefen(name, was):
        if not isinstance(name, str) or not _NAMENSREGEL.fullmatch(name):
            raise UngueltigerName(
                f"{was} {name!r} ist kein zulaessiger Name: erlaubt sind "
                f"Buchstaben, Ziffern, '_', '-', '.', am Anfang ein Buchstabe "
                f"oder eine Ziffer, hoechstens 100 Zeichen.")

    @staticmethod
    def _kennung_pruefen(node_id):
        # String-only: a numeric ID used to be stored under 5 but looked
        # up as "5" after restart.
        if not isinstance(node_id, str) or not node_id:
            raise UngueltigerName(
                f"Eine Kennung muss eine nicht leere Zeichenkette sein; "
                f"erhalten {node_id!r}.")

    @staticmethod
    def _speicherbar_pruefen(daten, wo):
        """Does `daten` come back unchanged from JSON?

        Catches values that serialize fine but change shape (tuple -> list,
        int key -> string key), which would otherwise disagree with disk
        after a restart.
        """
        try:
            text = json.dumps(daten, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise NichtSpeicherbar(f"{wo}: {e}") from e
        if json.loads(text) != daten:
            raise NichtSpeicherbar(
                f"{wo}: kaeme als JSON veraendert zurueck (Tupel werden zu "
                f"Listen, Zahlenschluessel zu Text).")

    def _is_deleted(self, node_data):
        return node_data is not None and "_deletion_flag" in node_data

    # ------------------------------------------------------------------
    # Internal helpers — vault_text
    # ------------------------------------------------------------------

    _VAULT_TEXT_PREFIX = "@vault_text/"

    # One file per CONTENT, never overwritten, named with a content hash so
    # a reference always points at the exact text it was created with;
    # orphaned old versions are swept by the GC.
    def _vt_path(self, collection_name, node_id, field, text):
        """Path of the file for exactly this text of this field."""
        safe = re.sub(r"[^\w\-]", "_", f"{collection_name}__{node_id}__{field}")
        pruef = hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
        return os.path.join(self.dirs["vault_text"], f"{safe}__{pruef}.txt")

    def _vt_schreiben(self, pfad, text):
        # If the file already exists it has the same content (hash in the
        # name) and is complete (written via os.replace).
        if os.path.exists(pfad):
            return
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        arbeit = pfad + ".tmp"
        with open(arbeit, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(arbeit, pfad)
        self._verzeichnis_sichern(os.path.dirname(pfad))

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
                path = self._vt_path(collection_name, node_id, field, value)
                self._vt_schreiben(path, value)
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

    # The field index lives only in RAM and is kept up to date per-node on
    # every write, rebuilt lazily from memory when first needed.
    #
    # Sets, not lists: removing a node from a value shared by many nodes
    # would otherwise be a linear scan.

    @staticmethod
    def _index_schluessel(val):
        return str(val).lower()

    def _index_austragen(self, collection, node_id):
        """BEFORE a change: remove the node's current values from every
        built field index of its collection."""
        felder = self._index_cache.get(collection)
        if not felder:
            return
        daten = self._cache["nodes"].get(collection, {}).get(node_id)
        if daten is None or self._is_deleted(daten):
            return
        for field, idx in felder.items():
            val = daten.get(field)
            if val is None:
                continue
            schluessel = self._index_schluessel(val)
            ids = idx.get(schluessel)
            if ids is not None:
                ids.discard(node_id)
                # Prune empty entries, or substring search pays for a dead
                # key on every future search, forever.
                if not ids:
                    del idx[schluessel]

    def _index_eintragen(self, collection, node_id):
        """AFTER a change: add the node's new values. Soft-deleted nodes
        are not indexed, same as during a rebuild."""
        felder = self._index_cache.get(collection)
        if not felder:
            return
        daten = self._cache["nodes"].get(collection, {}).get(node_id)
        if daten is None or self._is_deleted(daten):
            return
        for field, idx in felder.items():
            val = daten.get(field)
            if val is not None:
                idx.setdefault(self._index_schluessel(val), set()).add(node_id)

    def _mark_index_dirty(self, collection):
        """Discard a collection's index; only used by rare bulk paths
        (the GC) where a full rebuild is cheaper than per-node upkeep."""
        self._index_cache.pop(collection, None)

    def _build_field_index(self, collection, field):
        idx = {}
        for node_id, data in self._cache["nodes"].get(collection, {}).items():
            if self._is_deleted(data):
                continue
            val = data.get(field)
            if val is not None:
                idx.setdefault(self._index_schluessel(val), set()).add(node_id)
        return idx

    def _get_field_index(self, collection, field):
        """A field's index, built from memory on first use."""
        felder = self._index_cache.setdefault(collection, {})
        if field not in felder:
            felder[field] = self._build_field_index(collection, field)
        return felder[field]

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - NODES
    # ------------------------------------------------------------------

    def create_node(self, collection_name, node_id, data):
        """
        Create a node.
        :return: node reference as string "collection/id"
        :raises KeyError: if node_id already exists in this collection
        """
        self._offen_pruefen()
        # All checks before the first line that touches the store.
        self._name_pruefen(collection_name, "Sammlung")
        self._kennung_pruefen(node_id)
        self._validate_node(collection_name, data)
        self._speicherbar_pruefen(data, f"Knoten {collection_name}/{node_id}")

        if node_id in self._cache["nodes"].get(collection_name, {}):
            raise KnotenExistiert(
                f"Node '{node_id}' already exists in collection '{collection_name}'. "
                f"Use update_node() to modify existing nodes."
            )

        stored = copy.deepcopy(data)
        self._offload_longtexts(collection_name, node_id, stored)
        self._vormerken_knoten(collection_name, node_id)
        self._cache["nodes"].setdefault(collection_name, {})[node_id] = stored
        # Right after the in-memory change, before writing: even if the
        # write fails, the index must still match memory.
        self._index_eintragen(collection_name, node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._log_audit("create", collection_name, node_id)
        self._melden_knoten("create_node", collection_name, node_id)
        return f"{collection_name}/{node_id}"

    def get_node(self, node_ref, readonly=False):
        """
        Read a node from the RAM cache.
        Returns None if the node does not exist or is soft-deleted.

        :param readonly: If True, return a direct reference to the cached dict instead of a
                         deep copy. Faster for display-only use — caller must never mutate
                         the returned dict.
        """
        if not isinstance(node_ref, str) or "/" not in node_ref:
            raise UngueltigeReferenz(
                f"Node reference must be 'collection/id'; got {node_ref!r}.")
        col, n_id = node_ref.split("/", 1)

        node_data = self._cache["nodes"].get(col, {}).get(n_id)
        # `is not None`, not `if node_data`: a node with no fields is an
        # empty (falsy) dict and must not read as "does not exist".
        if node_data is not None and not self._is_deleted(node_data):
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
        self._offen_pruefen()
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            raise KnotenFehlt(f"Node '{node_id}' does not exist in "
                              f"collection '{collection_name}'.")

        # Simulate merge to check schema before applying
        merged = {**col_cache[node_id], **update_data}
        self._speicherbar_pruefen(merged, f"Knoten {collection_name}/{node_id}")
        schema_check_data = {k: v for k, v in merged.items()
                             if k not in _INTERNE_KNOTENFELDER}
        try:
            self._validate_node(collection_name, schema_check_data)
        except (ValueError, TypeError):
            # soft_delete only sets flatgraph's own flags — a node that
            # already didn't match the schema may still be deleted.
            if not all(k in _INTERNE_KNOTENFELDER for k in update_data):
                raise

        self._offload_longtexts(collection_name, node_id, update_data)
        self._vormerken_knoten(collection_name, node_id)
        self._index_austragen(collection_name, node_id)
        col_cache[node_id].update(update_data)
        self._index_eintragen(collection_name, node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        # Same boundary as schema validation: flatgraph's own fields are
        # not a content change and are excluded from the audit log.
        public_fields = {k: v for k, v in update_data.items()
                         if k not in _INTERNE_KNOTENFELDER}
        if public_fields:
            self._log_audit("update", collection_name, node_id, str(list(public_fields.keys())))
            self._melden_knoten("update_node", collection_name, node_id)
        return True

    def list_nodes(self, collection_name, include_deleted=False, readonly=False):
        """
        Return all nodes of a collection as dict {node_id: data}.
        Soft-deleted entries are excluded by default.

        :param readonly: If True, values are direct cache references (no deep copy).
                         Faster for display/reporting — caller must never mutate the dicts.
        """
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
            # The audit log is part of the transaction too: a rolled-back
            # change leaves no entry.
            self._vormerken_knoten("_audit_log", entry_id)
            self._cache["nodes"].setdefault("_audit_log", {})[entry_id] = entry
            self._mark_node_dirty("_audit_log", entry_id)
            self._persist_collection("_audit_log")
        finally:
            self._audit_writing = False

    # --- Meldungen bei Aenderungen ---------------------------------------
    # A storage library does not send anything itself; it reports, and the
    # caller decides what to do with it.

    def _melden(self, ereignis, ref, **mehr):
        if self.bei_aenderung is None:
            return
        meldung = {"ereignis": ereignis, "ref": ref,
                   "zeit": datetime.now(timezone.utc).isoformat(), **mehr}
        if self._transaction_depth > 0:
            self._meldungen.append(meldung)
        else:
            self._zustellen([meldung])

    def _zustellen(self, meldungen):
        for meldung in meldungen:
            try:
                self.bei_aenderung(meldung)
            except Exception:
                # The change is already on disk; raising would fake a
                # failure that never happened.
                _log.exception("bei_aenderung scheiterte an %s %s",
                               meldung["ereignis"], meldung["ref"])

    def _melden_knoten(self, ereignis, collection, node_id):
        if collection in _INTERNAL_COLLECTIONS:
            return
        self._melden(ereignis, f"{collection}/{node_id}", sammlung=collection)

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
        :return: new ID as string

        Example:
            db.next_id('documents', prefix='DOC-', padding=4)
            -> 'DOC-0001' if empty, 'DOC-0042' if DOC-0041 exists
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
                # A (id, record) pair comes from the dict form, where the
                # ID is the key and not part of the record.
                vorgabe_id = None
                if isinstance(raw, tuple):
                    vorgabe_id, raw = raw
                # apply field_map (rename keys)
                if field_map:
                    row = {field_map.get(k, k): v for k, v in raw.items()}
                    src_id_key = field_map.get(id_field, id_field)
                else:
                    row = dict(raw)
                    src_id_key = id_field
                node_id = (str(vorgabe_id) if vorgabe_id is not None
                           else str(row.get(src_id_key) or raw.get(id_field, "")))
                if not node_id:
                    raise ValueError(f"id_field '{id_field}' missing or empty in record: {raw}")
                exists = node_id in self._cache["nodes"].get(collection_name, {})
                if exists:
                    if on_conflict == "error":
                        raise KeyError(f"Node '{node_id}' already exists in '{collection_name}'.")
                    if on_conflict == "skip":
                        skipped += 1
                        continue
                    self.update_node(collection_name, node_id, row)
                else:
                    self.create_node(collection_name, node_id, row)
                imported += 1
        return {"imported": imported, "skipped": skipped}

    def import_json(self, collection_name, filepath, id_field=None,
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
        # The dict form's key IS the ID, matching export_json's output.
        if isinstance(data, dict) and id_field is None:
            records = list(data.items())
        else:
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
        """Move the node to the trash, together with everything it takes
        via cascade (see `loeschfolgen`). Runs as one transaction: on
        disk afterwards it is all or nothing."""
        ref = f"{collection_name}/{node_id}"
        with self.transaction():
            # Raises KnotenFehlt if it does not exist — update_node does that.
            result = self._weich_loeschen(collection_name, node_id, keep_asset)
            for folge in self._kaskade(ref):
                col, _, nid = folge.partition("/")
                self._weich_loeschen(col, nid, True, durch=ref)
        return result

    def _weich_loeschen(self, collection_name, node_id, keep_asset, durch=None):
        felder = {"_deletion_flag": datetime.now(timezone.utc).isoformat(),
                  "_keep_asset": keep_asset}
        if durch is not None:
            felder["_geloescht_durch"] = durch
        result = self.update_node(collection_name, node_id, felder)
        self._log_audit("soft_delete", collection_name, node_id)
        self._melden_knoten("soft_delete", collection_name, node_id)
        return result

    def _kaskade(self, ref):
        """What deleting `ref` takes with it: reachable via cascade-delete
        edges, any depth, only nodes not already in the trash."""
        return self.traverse(ref, direction="out",
                             kantenfilter=lambda k: k.get("_cascade_delete") is True)

    def restore_node(self, collection_name, node_id):
        """Restore from the trash (as long as the GC has not run yet).

        Also restores whatever was cascade-deleted along with THIS node,
        but nothing deleted independently of it.
        """
        self._offen_pruefen()
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            raise KnotenFehlt(f"Node '{node_id}' does not exist in "
                              f"collection '{collection_name}'.")
        ref = f"{collection_name}/{node_id}"
        with self.transaction():
            self._zurueckholen(collection_name, node_id)
            # Also traverse to already-deleted nodes: the ones cascaded
            # in are in the trash too; the marker decides.
            erreichbar = self.traverse(ref, direction="out", include_deleted=True,
                                       kantenfilter=lambda k: k.get("_cascade_delete") is True)
            for folge in erreichbar:
                col, _, nid = folge.partition("/")
                if self._cache["nodes"].get(col, {}).get(nid, {}).get("_geloescht_durch") == ref:
                    self._zurueckholen(col, nid)
        return True

    def _zurueckholen(self, collection_name, node_id):
        col_cache = self._cache["nodes"][collection_name]
        self._vormerken_knoten(collection_name, node_id)
        self._index_austragen(collection_name, node_id)
        for feld in _INTERNE_KNOTENFELDER:
            col_cache[node_id].pop(feld, None)
        self._index_eintragen(collection_name, node_id)
        self._mark_node_dirty(collection_name, node_id)
        self._persist_collection(collection_name)
        self._log_audit("restore", collection_name, node_id)
        self._melden_knoten("restore_node", collection_name, node_id)

    def verwendungen(self, node_ref, direction="in"):
        """"Where else is this used?" — for a delete confirmation dialog.

        All links of this node, grouped by edge type, with the node at the
        other end: {edge_type: [(edge_id, other_ref), ...]}. Default is
        incoming; `direction="both"` for callers that treat edges as
        undirected. Trashed endpoints don't count.
        """
        if direction not in ("in", "out", "both"):
            raise ValueError("direction muss 'in', 'out' oder 'both' sein.")
        ergebnis = {}
        for richtung in (("in", "out") if direction == "both" else (direction,)):
            index = self._in_index if richtung == "in" else self._out_index
            for art, eimer in index.get(node_ref, {}).items():
                for eid, kante in eimer.items():
                    andere = kante["source"] if richtung == "in" else kante["target"]
                    if self.get_node(andere, readonly=True) is None:
                        continue
                    ergebnis.setdefault(art, []).append((eid, andere))
        return ergebnis

    def loeschfolgen(self, node_ref):
        """"What else would disappear if I deleted this everywhere?" —
        without changing anything.

        {"knoten": [...], "kanten": [...]}: the nodes that `soft_delete`
        cascades into the trash, and the edges the GC then removes with
        them. A thin wrapper around `traverse` with an edge filter.
        """
        if self.get_node_raw(node_ref) is None:
            raise KnotenFehlt(f"Node '{node_ref}' does not exist.")
        knoten = self._kaskade(node_ref)
        kanten = []
        gesehen = set()
        for ref in [node_ref] + knoten:
            for index in (self._out_index, self._in_index):
                for eimer in index.get(ref, {}).values():
                    for eid in eimer:
                        if eid not in gesehen:
                            gesehen.add(eid)
                            kanten.append(eid)
        return {"knoten": knoten, "kanten": kanten}

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
        self._offen_pruefen()
        if self.get_node(source_ref) is None:
            raise ValueError(f"Source node '{source_ref}' does not exist or is soft-deleted.")
        if self.get_node(target_ref) is None:
            raise ValueError(f"Target node '{target_ref}' does not exist or is soft-deleted.")
        self._name_pruefen(rel_type, "Kantenart")
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
        self._speicherbar_pruefen(edge_data, f"Kante {source_ref} -> {target_ref}")

        self._vormerken_kante(rel_type, edge_id)
        self._cache["edges"].setdefault(rel_type, {})[edge_id] = edge_data
        self._edge_type_index[edge_id] = rel_type
        self._index_edge(edge_id, edge_data)
        self._persist_kanten(rel_type, [edge_id])
        self._melden("create_edge", edge_id, kantenart=rel_type,
                     quelle=source_ref, ziel=target_ref)
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
        self._offen_pruefen()
        rel_type = self._edge_type_index.get(edge_id)
        if not (rel_type and edge_id in self._cache["edges"].get(rel_type, {})):
            raise KanteFehlt(f"Edge '{edge_id}' does not exist.")
        self._vormerken_kante(rel_type, edge_id)
        alt = self._cache["edges"][rel_type][edge_id]
        self._unindex_edge(edge_id, alt)
        del self._cache["edges"][rel_type][edge_id]
        del self._edge_type_index[edge_id]
        self._persist_kanten(rel_type, [edge_id])
        self._melden("delete_edge", edge_id, kantenart=rel_type,
                     quelle=alt["source"], ziel=alt["target"])
        return True

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
        :return: list of node refs
        """
        # Via the neighborhood index, not all edges: cost scales with this
        # node's neighbors, not the total edge count.
        index = self._out_index if direction == "out" else self._in_index
        beim_knoten = index.get(node_ref)
        if not beim_knoten:
            return []
        if rel_type is not None:
            kanten = beim_knoten.get(rel_type, {}).values()
        else:
            kanten = [e for eimer in beim_knoten.values() for e in eimer.values()]

        # Order stays insertion order and duplicates stay duplicates,
        # matching prior behavior.
        results = []
        for edge in kanten:
            target = edge["target"] if direction == "out" else edge["source"]

            if target_collection and not target.startswith(f"{target_collection}/"):
                continue

            if not include_deleted:
                col, _, nid = target.partition("/")
                raw = self._cache["nodes"].get(col, {}).get(nid)
                if raw is None or self._is_deleted(raw):
                    continue

            results.append(target)
        return results

    def get_connected_edges(self, node_ref, direction="out", rel_type=None):
        """
        Like get_connected, but returns the full edge objects (including metadata).
        Format: [(edge_id, edge_data), ...]
        """
        index = self._out_index if direction == "out" else self._in_index
        beim_knoten = index.get(node_ref)
        if not beim_knoten:
            return []
        if rel_type is not None:
            paare = beim_knoten.get(rel_type, {}).items()
        else:
            paare = [(eid, e) for eimer in beim_knoten.values()
                     for eid, e in eimer.items()]
        return [(eid, copy.deepcopy(e)) for eid, e in paare]

    def traverse(self, start_ref, rel_type=None, direction="out",
                 max_depth=None, target_collection=None, include_deleted=False,
                 include_start=False, kantenfilter=None):
        """
        Multi-hop traversal (breadth-first).
        Find all nodes reachable from start_ref via rel_type.

        :param start_ref: starting node
        :param rel_type: filter by relationship type (None = follow all types)
        :param direction: 'out' follows edges forward, 'in' follows backward
        :param max_depth: maximum depth (None = unlimited)
        :param target_collection: optional filter by target collection
        :param include_start: include start node in result (default False)
        :param kantenfilter: predicate on the edge itself, `kantenfilter(kante)
                        -> bool`; only edges for which it is true are followed
                        (e.g. "only contracts still in force"). The edge is
                        passed read-only; do not mutate nested values.
        :return: list of node refs in BFS order (no duplicates)

        Example:
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
                if kantenfilter is None:
                    connected = self.get_connected(
                        node, direction=direction, rel_type=rel_type,
                        target_collection=target_collection, include_deleted=include_deleted,
                    )
                else:
                    connected = self._nachbarn_gefiltert(
                        node, direction, rel_type, target_collection,
                        include_deleted, kantenfilter)
                for c in connected:
                    if c not in visited:
                        visited.add(c)
                        next_level.append(c)
                        results.append(c)
            current_level = next_level
            depth += 1

        return results

    def _nachbarn_gefiltert(self, node_ref, direction, rel_type, target_collection,
                            include_deleted, kantenfilter):
        """Like get_connected, but only via edges that satisfy `kantenfilter`."""
        index = self._out_index if direction == "out" else self._in_index
        beim_knoten = index.get(node_ref)
        if not beim_knoten:
            return []
        if rel_type is not None:
            kanten = beim_knoten.get(rel_type, {}).values()
        else:
            kanten = [e for eimer in beim_knoten.values() for e in eimer.values()]
        results = []
        for edge in kanten:
            # MappingProxyType instead of a copy: free, and stops a filter
            # from mutating the cached edge.
            if not kantenfilter(types.MappingProxyType(edge)):
                continue
            target = edge["target"] if direction == "out" else edge["source"]
            if target_collection and not target.startswith(f"{target_collection}/"):
                continue
            if not include_deleted:
                col, _, nid = target.partition("/")
                raw = self._cache["nodes"].get(col, {}).get(nid)
                if raw is None or self._is_deleted(raw):
                    continue
            results.append(target)
        return results

    def collect_related(self, start_ref, rel_type_path, direction="out"):
        """
        Collect nodes along a chain of different relationship types.
        Useful for mixed traversals like:
        MainProcess --has_subprocess--> SubProcesses --needs_equipment--> Equipments

        :param rel_type_path: list of rel_types to follow per level;
                              the last level provides the results.
        :return: list of node refs at the end of the chain (no duplicates)

        Example:
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

    # Part of FlatGraphDB (the lines above are only comment). Idempotent:
    # safe to rerun after an interruption.
    #   A - Scanner:  find nodes with _deletion_flag
    #   B - Cascader: remove their edges
    #   C - Purger:   archive assets, permanently delete nodes

    def run_garbage_collection(self, verbose=False):
        """
        Run the full garbage collection cycle.
        :return: statistics dict
        """
        # Before the first change, not the first write: this also moves
        # assets, which a closed instance must not start at all.
        self._offen_pruefen()
        if self._transaction_depth > 0:
            raise NichtInTransaktion(
                "run_garbage_collection laeuft nicht in einer Transaktion: es "
                "verschiebt Anhaenge und schreibt sofort, ein Rollback koennte "
                "das nicht zuruecknehmen.")
        stats = {"scanned": 0, "edges_removed": 0, "nodes_purged": 0,
                 "assets_archived": 0, "assets_kept": 0, "vault_text_orphans": 0,
                 "faecher_verdichtet": 0}

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

        # Remove the deleted nodes from disk
        purged_collections = {col for col, _, _ in to_delete}
        for col in purged_collections:
            self._persist_collection_full(col)

        # Close the gaps deletion just left.
        stats["faecher_verdichtet"] = self._verdichten()


        # vault_text orphan cleanup: remove .txt files with no live node reference
        stats["vault_text_orphans"] = self._collect_vault_text_orphans(verbose)

        self._write_maintenance_log(stats)
        return stats

    def _verdichten(self):
        """Merge thin shards together. Returns the number resolved.

        Thin means at most half full, and only worth it from two thin
        shards in one collection/edge-type. Content moves to new shards
        placed after all existing ones; only once those are written are
        the old ones cleared and removed.
        """
        aufgeloest = 0
        for art in ("nodes", "edges"):
            for name, ablage in list(self._ablagen[art].items()):
                daten = self._cache[art].get(name, {})
                duenn = sorted(nr for nr, m in ablage.inhalt.items()
                               if 0 < len(m) <= FACH_GROESSE // 2)
                if len(duenn) < 2:
                    continue
                umzug = sorted(k for nr in duenn for k in ablage.inhalt[nr])
                erstes = max(ablage.inhalt) + 1
                neue = set()
                for i, k in enumerate(umzug):
                    ablage.entfernen(k)
                    nr = erstes + i // FACH_GROESSE
                    ablage.eintragen(k, nr)
                    neue.add(nr)
                self._faecher_schreiben(ablage, neue, daten)
                self._faecher_schreiben(ablage, set(duenn), daten)
                aufgeloest += len(duenn)
        return aufgeloest

    def _collect_vault_text_orphans(self, verbose=False):
        """Remove vault_text files no node points to any more.

        A node still in the trash counts as pointing to its text; only
        purging the node itself removes it.
        """
        vt_dir = self.dirs["vault_text"]
        if not os.path.isdir(vt_dir):
            return 0
        # Build set of all @vault_text/ references currently in the cache
        live_refs = set()
        prefix = FlatGraphDB._VAULT_TEXT_PREFIX
        for col_data in self._cache["nodes"].values():
            for node_data in col_data.values():
                for value in node_data.values():
                    if isinstance(value, str) and value.startswith(prefix):
                        rel = value[len(prefix):]
                        live_refs.add(os.path.normpath(os.path.join(self.root, rel)))
        removed = 0
        for filename in os.listdir(vt_dir):
            # .tmp: leftover from an interrupted write; never referenced.
            if not filename.endswith((".txt", ".tmp")):
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
        # Since soft_delete already takes the cascade with it, the GC only
        # finds leftovers here (older stores, or a node deleted before its
        # cascade edge existed); it does not purge them this run either,
        # only trashes them (with _geloescht_durch) for the next run.
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
                            target_node["_geloescht_durch"] = edge["source"]
                            self._mark_node_dirty(col, nid)
                            dirty_collections.add(col)
                    except ValueError:
                        pass
                    changed = True

        # Expansion done — flush all marked collections to disk
        for col in dirty_collections:
            # The cascade sets deletion markers directly, without
            # update_node, so the field index is discarded instead of
            # kept in sync (rare path, rebuild is cheap).
            self._mark_index_dirty(col)
            self._persist_collection(col)

        # Only what was already in the trash before this run gets purged.
        return direct

    def _cascade_delete_edges(self, refs_to_delete):
        """Phase B: remove all edges pointing to or from nodes being deleted."""
        dirty_types = {}
        count = 0
        for rel_type, bucket in self._cache["edges"].items():
            to_remove = [
                eid for eid, e in bucket.items()
                if e["source"] in refs_to_delete or e["target"] in refs_to_delete
            ]
            for eid in to_remove:
                self._unindex_edge(eid, bucket[eid])
                del bucket[eid]
                self._edge_type_index.pop(eid, None)
                dirty_types.setdefault(rel_type, set()).add(eid)
                count += 1
        for rel_type, eids in dirty_types.items():
            self._persist_kanten(rel_type, eids)
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


# =============================================================================
# THREADSICHERHEIT
# =============================================================================
# Every public method runs under the instance lock, applied here in a loop
# rather than as a per-method decorator so a new public method is
# protected automatically. `transaction` is excluded — it holds the lock
# over its whole block itself.

def _gesperrt(methode):
    @functools.wraps(methode)
    def unter_sperre(self, *args, **kwargs):
        with self._sperre:
            return methode(self, *args, **kwargs)
    unter_sperre._unter_sperre = True
    return unter_sperre


for _name, _methode in list(vars(FlatGraphDB).items()):
    if (not _name.startswith("_") and _name != "transaction"
            and callable(_methode)):
        setattr(FlatGraphDB, _name, _gesperrt(_methode))
del _name, _methode


class MaintenanceEngine(FlatGraphDB):
    """Former name for an instance with the garbage collector.

    Kept so `from flatgraph import MaintenanceEngine` and
    `MaintenanceEngine(wurzel).run_garbage_collection()` keep working —
    but only as the SOLE instance of the store; a FlatGraphDB open
    alongside it gets `BestandBelegt`, so `db.run_garbage_collection()`
    is the normal path.
    """
