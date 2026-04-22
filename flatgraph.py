"""
=============================================================================
FLATGRAPH v2 - File-based JSON Graph Database
=============================================================================
Eine leichtgewichtige, eingebettete Graph-Datenbank fuer Python.

Architektur:
  /root/
    datenbank/
      nodes/      -> Objekte pro Collection (z.B. dokumente.json)
      edges/      -> Beziehungen (objektlinks.json)
    vault/        -> Aktive Binaerdateien / Assets
    vault_archive/-> Quarantaene fuer geloeschte Assets

Features:
  - Graph-Pattern: Nodes referenzieren sich nie direkt, nur ueber Edges
  - Soft-Delete mit 2-Phasen Garbage Collector (idempotent)
  - Cascade-Delete am Edge: abhaengige Ziel-Nodes sterben mit der Quelle
  - Atomic Writes via temp+rename
  - Optionales Schema pro Collection
  - RAM-Cache fuer schnelle Reads
  - Traversal ueber mehrere Hops
  - Autoincrement-Helper fuer ID-Vergabe
=============================================================================
"""

import os
import json
import shutil
import uuid
from datetime import datetime, timezone


# =============================================================================
# HAUPT-ENGINE
# =============================================================================

class FlatGraphDB:
    def __init__(self, root_dir, schemas=None):
        """
        Initialisiert die Graph-Engine.
        :param root_dir: Basisverzeichnis der Datenbank.
        :param schemas: Optional. Dict {collection_name: {field: expected_type}}.
        """
        self.root = root_dir
        self.schemas = schemas or {}

        # Verzeichnisstruktur
        self.dirs = {
            "nodes": os.path.join(root_dir, "datenbank", "nodes"),
            "edges": os.path.join(root_dir, "datenbank", "edges"),
            "vault": os.path.join(root_dir, "vault"),
            "vault_archive": os.path.join(root_dir, "vault_archive"),
        }
        for path in self.dirs.values():
            os.makedirs(path, exist_ok=True)

        # Zentrale Edge-Datei
        self.edges_file = os.path.join(self.dirs["edges"], "objektlinks.json")

        # RAM-Cache
        self._cache = {"nodes": {}, "edges": {}}
        self._initialize_cache()

    # ------------------------------------------------------------------
    # Interne Helfer
    # ------------------------------------------------------------------

    def _initialize_cache(self):
        """Laedt alle JSON-Dateien einmalig in den RAM."""
        self._cache["edges"] = self._load_json_from_disk(self.edges_file)

        if os.path.exists(self.dirs["nodes"]):
            for filename in os.listdir(self.dirs["nodes"]):
                if filename.endswith(".json"):
                    collection_name = filename.replace(".json", "")
                    path = os.path.join(self.dirs["nodes"], filename)
                    self._cache["nodes"][collection_name] = self._load_json_from_disk(path)

    def _load_json_from_disk(self, filepath):
        """Laedt JSON-Datei sicher. Gibt leeres Dict bei Fehler zurueck."""
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_json_atomic(self, filepath, data):
        """Atomic Write: erst in .tmp schreiben, dann os.replace()."""
        temp_file = filepath + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(temp_file, filepath)

    def _persist_collection(self, collection_name):
        """Schreibt eine Node-Collection vom RAM auf Disk."""
        path = os.path.join(self.dirs["nodes"], f"{collection_name}.json")
        self._save_json_atomic(path, self._cache["nodes"].get(collection_name, {}))

    def _persist_edges(self):
        """Schreibt Edges vom RAM auf Disk."""
        self._save_json_atomic(self.edges_file, self._cache["edges"])

    def _validate_node(self, collection_name, data):
        """Prueft Daten gegen das Schema, falls eines definiert ist."""
        if collection_name not in self.schemas:
            return
        schema = self.schemas[collection_name]
        for field, expected_type in schema.items():
            if field not in data:
                raise ValueError(f"Pflichtfeld '{field}' fehlt in Collection '{collection_name}'.")
            if not isinstance(data[field], expected_type):
                raise TypeError(
                    f"Feld '{field}' muss vom Typ {expected_type.__name__} sein."
                )

    def _is_deleted(self, node_data):
        """Prueft, ob ein Node soft-deleted ist."""
        return node_data is not None and "_deletion_flag" in node_data

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - NODES
    # ------------------------------------------------------------------

    def create_node(self, collection_name, node_id, data):
        """
        Erstellt einen Knoten.
        :return: Node-Referenz als String "collection/id"
        """
        self._validate_node(collection_name, data)

        if collection_name not in self._cache["nodes"]:
            self._cache["nodes"][collection_name] = {}

        self._cache["nodes"][collection_name][node_id] = data
        self._persist_collection(collection_name)
        return f"{collection_name}/{node_id}"

    def get_node(self, node_ref):
        """
        Liest einen Knoten aus dem RAM-Cache.
        Gibt None zurueck, wenn Node nicht existiert oder soft-deleted ist.
        """
        try:
            col, n_id = node_ref.split("/", 1)
        except ValueError:
            return None

        node_data = self._cache["nodes"].get(col, {}).get(n_id)
        if node_data and not self._is_deleted(node_data):
            return node_data
        return None

    def get_node_raw(self, node_ref):
        """
        Wie get_node, aber zeigt auch soft-deleted Objekte.
        Nuetzlich fuer Maintenance / Admin-Views.
        """
        try:
            col, n_id = node_ref.split("/", 1)
        except ValueError:
            return None
        return self._cache["nodes"].get(col, {}).get(n_id)

    def update_node(self, collection_name, node_id, update_data):
        """
        Aktualisiert Felder eines bestehenden Knotens.
        Validiert den Gesamtzustand gegen das Schema (falls vorhanden).
        """
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            return False

        # Simulierter Merge fuer Schema-Check
        merged = {**col_cache[node_id], **update_data}
        # Interne Felder wie _deletion_flag sollen Schema nicht brechen
        schema_check_data = {k: v for k, v in merged.items() if not k.startswith("_")}
        try:
            self._validate_node(collection_name, schema_check_data)
        except (ValueError, TypeError):
            # Bei reinen Internal-Flag-Updates (soft_delete) kein Schema-Check
            if not all(k.startswith("_") for k in update_data):
                raise

        col_cache[node_id].update(update_data)
        self._persist_collection(collection_name)
        return True

    def list_nodes(self, collection_name, include_deleted=False):
        """
        Gibt alle Nodes einer Collection zurueck als Dict {node_id: data}.
        Standardmaessig ohne soft-deleted Eintraege.
        """
        col = self._cache["nodes"].get(collection_name, {})
        if include_deleted:
            return dict(col)
        return {nid: data for nid, data in col.items() if not self._is_deleted(data)}

    def list_collections(self):
        """Gibt Namen aller bekannten Node-Collections zurueck."""
        return list(self._cache["nodes"].keys())

    def next_id(self, collection_name, prefix="", padding=0):
        """
        Ermittelt die naechste freie ID in einer Collection.
        Scannt bestehende IDs mit dem Prefix und findet das Maximum der numerischen Suffixe.

        :param collection_name: Collection in der gesucht wird
        :param prefix: ID-Praefix, z.B. 'DOC-' oder 'EQ-'
        :param padding: Zero-Padding der Nummer, z.B. 4 -> 'DOC-0001'
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
        """Markiert einen Knoten fuer den Garbage Collector."""
        return self.update_node(collection_name, node_id, {
            "_deletion_flag": datetime.now(timezone.utc).isoformat(),
            "_keep_asset": keep_asset,
        })

    def restore_node(self, collection_name, node_id):
        """
        Macht einen Soft-Delete rueckgaengig (solange GC noch nicht lief).
        """
        col_cache = self._cache["nodes"].get(collection_name, {})
        if node_id not in col_cache:
            return False
        col_cache[node_id].pop("_deletion_flag", None)
        col_cache[node_id].pop("_keep_asset", None)
        self._persist_collection(collection_name)
        return True

    # ------------------------------------------------------------------
    # OEFFENTLICHE API - EDGES
    # ------------------------------------------------------------------

    def create_edge(self, source_ref, target_ref, rel_type, meta=None, cascade_delete=False):
        """
        Erstellt eine gerichtete Verbindung.
        :param cascade_delete: Wenn True, stirbt der Ziel-Node mit dem Quell-Node.
                               Verwenden fuer: Logs die zu einem Objekt gehoeren,
                               Subprozesse die zu einem Hauptprozess gehoeren, etc.
        :return: edge_id
        """
        edge_id = f"link_{uuid.uuid4().hex[:12]}"
        edge_data = {
            "quelle": source_ref,
            "ziel": target_ref,
            "typ": rel_type,
            "erstellt_am": datetime.now(timezone.utc).isoformat(),
        }
        if cascade_delete:
            edge_data["_cascade_delete"] = True
        if meta:
            edge_data.update(meta)

        self._cache["edges"][edge_id] = edge_data
        self._persist_edges()
        return edge_id

    def get_edge(self, edge_id):
        """Liest ein Edge-Objekt."""
        return self._cache["edges"].get(edge_id)

    def delete_edge(self, edge_id):
        """Loescht eine Kante physisch (Edges haben keinen Soft-Delete)."""
        if edge_id in self._cache["edges"]:
            del self._cache["edges"][edge_id]
            self._persist_edges()
            return True
        return False

    def list_edges(self, rel_type=None):
        """
        Gibt alle Edges zurueck, optional gefiltert nach Typ.
        Format: {edge_id: edge_data}
        """
        if rel_type is None:
            return dict(self._cache["edges"])
        return {
            eid: e for eid, e in self._cache["edges"].items()
            if e.get("typ") == rel_type
        }

    def get_connected(self, node_ref, direction="out", rel_type=None,
                      target_collection=None, include_deleted=False):
        """
        Findet verknuepfte Knoten-Referenzen.
        :param direction: 'out' (ausgehend) oder 'in' (eingehend)
        :param rel_type:  optional Filter nach Beziehungstyp
        :param target_collection: optional Filter nach Ziel-Collection
                                  (z.B. 'equipments' gibt nur Refs die mit 'equipments/' beginnen)
        :param include_deleted: soft-deleted Targets mit einschliessen
        :return: Liste von Node-Refs
        """
        results = []
        for edge in self._cache["edges"].values():
            if rel_type and edge.get("typ") != rel_type:
                continue

            if direction == "out" and edge["quelle"] == node_ref:
                target = edge["ziel"]
            elif direction == "in" and edge["ziel"] == node_ref:
                target = edge["quelle"]
            else:
                continue

            # Collection-Filter
            if target_collection and not target.startswith(f"{target_collection}/"):
                continue

            # Soft-Deleted rausfiltern (konsistent mit get_node)
            if not include_deleted:
                raw = self.get_node_raw(target)
                if self._is_deleted(raw):
                    continue

            results.append(target)
        return results

    def get_connected_edges(self, node_ref, direction="out", rel_type=None):
        """
        Wie get_connected, aber gibt die vollen Edge-Objekte zurueck (inkl. Metadaten).
        Format: [(edge_id, edge_data), ...]
        """
        results = []
        for edge_id, edge in self._cache["edges"].items():
            if rel_type and edge.get("typ") != rel_type:
                continue
            if direction == "out" and edge["quelle"] == node_ref:
                results.append((edge_id, edge))
            elif direction == "in" and edge["ziel"] == node_ref:
                results.append((edge_id, edge))
        return results

    def traverse(self, start_ref, rel_type=None, direction="out",
                 max_depth=None, target_collection=None, include_deleted=False,
                 include_start=False):
        """
        Multi-Hop Traversal (Breadth-First).
        Findet alle ueber rel_type erreichbaren Nodes ab start_ref.

        :param start_ref: Startknoten
        :param rel_type: Filter auf Beziehungstyp (None = alle Typen folgen)
        :param direction: 'out' folgt Edges vorwaerts, 'in' rueckwaerts
        :param max_depth: maximale Tiefe (None = unbegrenzt)
        :param target_collection: optionaler Filter auf Ziel-Collection
        :param include_start: Startknoten in Ergebnis aufnehmen (default False)
        :return: Liste von Node-Refs in Besuchsreihenfolge (BFS, ohne Duplikate)

        Beispiel:
            # Alle Subprozesse und Sub-Subprozesse eines Hauptprozesses:
            db.traverse('processes/PROC-MAIN', rel_type='hat_subprozess')

            # Alle Equipments, die in irgendeinem Unter-Prozess benoetigt werden:
            subs = db.traverse('processes/PROC-MAIN', rel_type='hat_subprozess')
            # plus Startknoten
            all_procs = [start] + subs
            equipments = set()
            for p in all_procs:
                equipments.update(db.get_connected(p, 'out', 'benoetigt_equipment'))
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
        Sammelt Nodes entlang einer Beziehungskette verschiedener Typen.
        Nuetzlich fuer gemischte Traversals wie:
        Hauptprozess --hat_subprozess--> Subprozesse --benoetigt_equipment--> Equipments

        :param rel_type_path: Liste der zu folgenden rel_types pro Ebene
                              Die letzte Ebene liefert die Ergebnisse.
        :return: Liste von Node-Refs am Ende der Kette (ohne Duplikate)

        Beispiel:
            # Alle Equipments aller Subprozesse (und des Hauptprozesses selbst):
            db.collect_related('processes/PROC-MAIN',
                               ['hat_subprozess', 'benoetigt_equipment'])
        """
        if not rel_type_path:
            return []

        # Alle Zwischen-Nodes sammeln (erste bis vorletzte Ebene)
        current = {start_ref}
        for rel_type in rel_type_path[:-1]:
            next_set = set()
            for node in current:
                next_set.update(self.get_connected(node, direction, rel_type))
            # Auch Start-Ebene behalten, falls dort direkte Verbindungen existieren
            current = current | next_set

        # Letzte Ebene: finale Targets einsammeln
        results = set()
        final_rel = rel_type_path[-1]
        for node in current:
            results.update(self.get_connected(node, direction, final_rel))
        return sorted(results)


# =============================================================================
# WARTUNGS-MODUL: GARBAGE COLLECTOR
# =============================================================================

class MaintenanceEngine(FlatGraphDB):
    """
    Isoliertes Wartungsmodul. Laeuft manuell, zeitgesteuert oder beim App-Start.
    Idempotent: kann nach Fehlern einfach erneut gestartet werden.

    Phasen:
      A - Scanner:  findet Nodes mit _deletion_flag
      B - Cascader: loescht zugehoerige Edges
      C - Purger:   verschiebt Assets und loescht Nodes final
    """

    def run_garbage_collection(self, verbose=False):
        """
        Fuehrt den kompletten GC-Lauf aus.
        :return: Dict mit Statistik
        """
        stats = {"scanned": 0, "edges_removed": 0, "nodes_purged": 0, "assets_archived": 0, "assets_kept": 0}

        # --- SCHRITT A: Scanner ---
        to_delete = self._scan_for_deletions()
        stats["scanned"] = len(to_delete)

        if not to_delete:
            if verbose:
                print("[GC] Keine zu loeschenden Objekte gefunden.")
            return stats

        if verbose:
            print(f"[GC] {len(to_delete)} Objekt(e) zum Loeschen markiert.")

        # --- SCHRITT B: Cascader ---
        refs_to_delete = {f"{col}/{nid}" for col, nid, _ in to_delete}
        stats["edges_removed"] = self._cascade_delete_edges(refs_to_delete)

        if verbose:
            print(f"[GC] {stats['edges_removed']} verwaiste Kante(n) entfernt.")

        # --- SCHRITT C: Purger ---
        for col, nid, node_data in to_delete:
            archived = self._purge_node(col, nid, node_data)
            stats["nodes_purged"] += 1
            if archived is True:
                stats["assets_archived"] += 1
            elif archived is False:
                stats["assets_kept"] += 1

        if verbose:
            print(f"[GC] {stats['nodes_purged']} Node(s) endgueltig geloescht.")
            print(f"[GC] {stats['assets_archived']} Asset(s) archiviert, {stats['assets_kept']} behalten.")

        self._write_maintenance_log(stats)
        return stats

    # ------------------------------------------------------------------
    # Interne Phasen
    # ------------------------------------------------------------------

    def _scan_for_deletions(self):
        """
        Phase A: findet alle Nodes mit _deletion_flag.
        Erweitert die Liste um cascade_delete-Ziele (transitive Schliessung).
        """
        # Schritt 1: direkt markierte Nodes finden
        direct = []
        for col_name, col_data in self._cache["nodes"].items():
            for node_id, data in col_data.items():
                if self._is_deleted(data):
                    direct.append((col_name, node_id, data))

        if not direct:
            return []

        # Schritt 2: Cascade-Expansion - folge allen cascade_delete-Edges rekursiv
        # Sammele alle Refs die in die Loeschliste muessen
        to_delete_refs = {f"{col}/{nid}" for col, nid, _ in direct}

        changed = True
        while changed:
            changed = False
            for edge in self._cache["edges"].values():
                if not edge.get("_cascade_delete"):
                    continue
                # Quelle ist markiert, Ziel noch nicht -> expandieren
                if edge["quelle"] in to_delete_refs and edge["ziel"] not in to_delete_refs:
                    to_delete_refs.add(edge["ziel"])
                    # Ziel-Node markieren (Soft-Delete-Flag setzen) - vererbt Keep-Flag
                    try:
                        col, nid = edge["ziel"].split("/", 1)
                        target_node = self._cache["nodes"].get(col, {}).get(nid)
                        if target_node and not self._is_deleted(target_node):
                            target_node["_deletion_flag"] = datetime.now(timezone.utc).isoformat()
                            target_node.setdefault("_keep_asset", True)
                            self._persist_collection(col)
                    except ValueError:
                        pass
                    changed = True

        # Schritt 3: finale Liste aus aktuellem Zustand aufbauen
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
        """Phase B: entfernt alle Edges, die auf zu loeschende Nodes zeigen."""
        edges_to_remove = [
            eid for eid, e in self._cache["edges"].items()
            if e["quelle"] in refs_to_delete or e["ziel"] in refs_to_delete
        ]
        for eid in edges_to_remove:
            del self._cache["edges"][eid]

        if edges_to_remove:
            self._persist_edges()
        return len(edges_to_remove)

    def _purge_node(self, collection_name, node_id, node_data):
        """
        Phase C: Asset-Behandlung und endgueltiges Loeschen eines Nodes.
        :return: True wenn Asset archiviert, False wenn behalten, None wenn kein Asset
        """
        keep_asset = node_data.get("_keep_asset", True)
        asset_path = node_data.get("datei")  # Konvention: 'datei' zeigt auf Vault-Pfad
        asset_status = None

        if asset_path:
            full_asset_path = os.path.join(self.root, asset_path)
            if os.path.exists(full_asset_path):
                if keep_asset:
                    # Datei bleibt im Vault (wird zum Orphan)
                    asset_status = False
                else:
                    # Verschieben nach vault_archive (Sicherheitsnetz)
                    target_path = os.path.join(
                        self.dirs["vault_archive"],
                        os.path.basename(full_asset_path)
                    )
                    # Namenskonflikt im Archiv vermeiden
                    if os.path.exists(target_path):
                        base, ext = os.path.splitext(target_path)
                        target_path = f"{base}_{uuid.uuid4().hex[:6]}{ext}"
                    shutil.move(full_asset_path, target_path)
                    asset_status = True

        # Node endgueltig entfernen (ALLERLETZTER Schritt -> Idempotenz)
        del self._cache["nodes"][collection_name][node_id]
        self._persist_collection(collection_name)
        return asset_status

    def _write_maintenance_log(self, stats):
        """Haengt einen Eintrag ans Maintenance-Logbuch."""
        log_path = os.path.join(self.root, "datenbank", "maintenance.log")
        timestamp = datetime.now(timezone.utc).isoformat()
        line = f"[{timestamp}] {json.dumps(stats, ensure_ascii=False)}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
