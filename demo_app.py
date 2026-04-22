"""
FlatGraphDB Demo-App
Szenario: Kleiner Fertigungsprozess-Katalog
  - Produkt "Fahrrad" wird in 2 Hauptprozessen gefertigt
  - Subprozesse, Equipments, Räume, Materialien, Dokumente
  - Testet jede API-Methode mindestens einmal
"""

import os
import shutil
import tempfile
from flatgraph import FlatGraphDB, MaintenanceEngine

SEP = "-" * 60


def header(title):
    print(f"\n{SEP}\n  {title}\n{SEP}")


def run_demo():
    # Temporäres Verzeichnis – wird am Ende aufgeräumt
    root = tempfile.mkdtemp(prefix="flatgraph_demo_")
    print(f"Datenbank-Root: {root}")

    # ----------------------------------------------------------------
    # 1. INITIALISIERUNG mit optionalen Schemas
    # ----------------------------------------------------------------
    header("1. INITIALISIERUNG")

    schemas = {
        "products":   {"name": str},
        "processes":  {"name": str},
        "equipments": {"name": str},
        "rooms":      {"name": str},
        "materials":  {"name": str},
        "documents":  {"title": str},
    }
    db = FlatGraphDB(root_dir=root, schemas=schemas)
    print("FlatGraphDB gestartet.")
    print(f"Collections (leer): {db.list_collections()}")

    # ----------------------------------------------------------------
    # 2. NODES ANLEGEN
    # ----------------------------------------------------------------
    header("2. NODES ANLEGEN")

    # Produkt
    prod_ref = db.create_node("products", "PROD-001", {"name": "Fahrrad"})
    print(f"Produkt angelegt:     {prod_ref}")

    # next_id Demo
    eq_id_1 = db.next_id("equipments", prefix="EQ-", padding=3)
    eq_id_2 = db.next_id("equipments", prefix="EQ-", padding=3)   # noch nichts in Collection -> beide EQ-001?
    eq_ref_1 = db.create_node("equipments", eq_id_1, {"name": "Schweißgerät"})
    eq_id_2 = db.next_id("equipments", prefix="EQ-", padding=3)   # jetzt EQ-002
    eq_ref_2 = db.create_node("equipments", eq_id_2, {"name": "Montagetisch"})
    eq_ref_3 = db.create_node("equipments", db.next_id("equipments", prefix="EQ-", padding=3),
                              {"name": "Förderbandanlage"})
    print(f"Equipments:           {eq_ref_1}, {eq_ref_2}, {eq_ref_3}")

    # Räume
    room_ref_1 = db.create_node("rooms", "ROOM-A", {"name": "Halle A – Schweißen"})
    room_ref_2 = db.create_node("rooms", "ROOM-B", {"name": "Halle B – Montage"})
    print(f"Räume:                {room_ref_1}, {room_ref_2}")

    # Materialien
    mat_ref_1 = db.create_node("materials", "MAT-001", {"name": "Stahlrohr"})
    mat_ref_2 = db.create_node("materials", "MAT-002", {"name": "Schraubenset"})
    print(f"Materialien:          {mat_ref_1}, {mat_ref_2}")

    # Prozesse – Hauptprozesse
    p_rahmen = db.create_node("processes", "PROC-001", {"name": "Rahmen fertigen"})
    p_montage = db.create_node("processes", "PROC-002", {"name": "Fahrrad montieren"})

    # Subprozesse
    p_schneiden = db.create_node("processes", "PROC-001-A", {"name": "Rohre schneiden"})
    p_schweissen = db.create_node("processes", "PROC-001-B", {"name": "Rahmen schweißen"})
    p_schrauben = db.create_node("processes", "PROC-002-A", {"name": "Komponenten verschrauben"})
    p_qk = db.create_node("processes", "PROC-002-B", {"name": "Qualitätskontrolle"})
    print(f"Prozesse angelegt:    {p_rahmen}, {p_montage} + 4 Subprozesse")

    # Dokument (mit Asset-Pfad – für GC-Test später)
    doc_ref = db.create_node("documents", "DOC-001", {
        "title": "Schweißanleitung v1",
        "datei": "vault/schweissanleitung.pdf",
    })
    # Fake-Asset im Vault anlegen
    vault_path = os.path.join(root, "vault", "schweissanleitung.pdf")
    os.makedirs(os.path.dirname(vault_path), exist_ok=True)
    with open(vault_path, "w") as f:
        f.write("FAKE PDF CONTENT")
    print(f"Dokument + Asset:     {doc_ref}")

    # ----------------------------------------------------------------
    # 3. EDGES ANLEGEN
    # ----------------------------------------------------------------
    header("3. EDGES ANLEGEN")

    # Produkt-Prozess
    db.create_edge(p_montage, prod_ref, "erzeugt")

    # Prozess-Hierarchie mit Reihenfolge am Edge
    db.create_edge(p_rahmen, p_schneiden, "hat_subprozess", meta={"reihenfolge": 10})
    db.create_edge(p_rahmen, p_schweissen, "hat_subprozess", meta={"reihenfolge": 20})
    db.create_edge(p_montage, p_schrauben, "hat_subprozess", meta={"reihenfolge": 10})
    db.create_edge(p_montage, p_qk, "hat_subprozess", meta={"reihenfolge": 20})

    # Equipment-Zuordnung
    db.create_edge(p_schweissen, eq_ref_1, "benoetigt_equipment")
    db.create_edge(p_schrauben, eq_ref_2, "benoetigt_equipment")
    db.create_edge(p_qk, eq_ref_3, "benoetigt_equipment")

    # Equipment in Räumen
    db.create_edge(eq_ref_1, room_ref_1, "steht_in")
    db.create_edge(eq_ref_2, room_ref_2, "steht_in")
    db.create_edge(eq_ref_3, room_ref_2, "steht_in")

    # Material-Verbrauch mit Menge am Edge
    db.create_edge(p_schneiden, mat_ref_1, "verbraucht_material", meta={"menge_m": 3.5})
    db.create_edge(p_schrauben, mat_ref_2, "verbraucht_material", meta={"anzahl": 24})

    # Dokument an Prozess – mit cascade_delete: Anleitung stirbt mit dem Prozess
    db.create_edge(p_schweissen, doc_ref, "hat_dokument", cascade_delete=True)

    print(f"Edges angelegt: {len(db.list_edges())} Kanten")

    # ----------------------------------------------------------------
    # 4. LESEN & ABFRAGEN
    # ----------------------------------------------------------------
    header("4. LESEN & ABFRAGEN")

    # get_node
    rahmen = db.get_node(p_rahmen)
    print(f"get_node({p_rahmen}):   {rahmen}")

    # list_nodes
    alle_equipments = db.list_nodes("equipments")
    print(f"list_nodes equipments:  {list(alle_equipments.keys())}")

    # list_collections
    print(f"list_collections:       {db.list_collections()}")

    # get_connected – ausgehend
    subs = db.get_connected(p_rahmen, direction="out", rel_type="hat_subprozess")
    print(f"Subprozesse von {p_rahmen}: {subs}")

    # get_connected_edges – mit Reihenfolge-Metadaten
    print(f"\nSubprozesse geordnet:")
    edges = db.get_connected_edges(p_montage, direction="out", rel_type="hat_subprozess")
    for eid, e in sorted(edges, key=lambda x: x[1].get("reihenfolge", 0)):
        node = db.get_node(e["ziel"])
        print(f"  [{e['reihenfolge']:>2}] {e['ziel']} – {node['name']}")

    # get_connected – eingehend (welche Prozesse brauchen Schweißgerät?)
    nutzer = db.get_connected(eq_ref_1, direction="in", rel_type="benoetigt_equipment")
    print(f"\nProzesse die {eq_ref_1} nutzen: {nutzer}")

    # traverse – alle Subprozesse rekursiv
    alle_subs = db.traverse(p_rahmen, rel_type="hat_subprozess", include_start=True)
    print(f"\ntraverse({p_rahmen}) inkl. Start: {alle_subs}")

    # collect_related – alle Equipments aller Subprozesse von p_montage
    equips = db.collect_related(p_montage, ["hat_subprozess", "benoetigt_equipment"])
    print(f"collect_related Equipments von {p_montage}: {equips}")

    # Materialmenge aus Edge-Metadaten lesen
    mat_edges = db.get_connected_edges(p_schneiden, direction="out", rel_type="verbraucht_material")
    for eid, e in mat_edges:
        mat = db.get_node(e["ziel"])
        print(f"\nMaterial-Edge: {mat['name']} – Menge: {e.get('menge_m')} m")

    # ----------------------------------------------------------------
    # 5. UPDATE
    # ----------------------------------------------------------------
    header("5. UPDATE")

    ok = db.update_node("equipments", "EQ-001", {"standort_notiz": "Hinten links"})
    print(f"update_node EQ-001: {ok}")
    print(f"Nach Update: {db.get_node(eq_ref_1)}")

    # ----------------------------------------------------------------
    # 6. SOFT DELETE & RESTORE
    # ----------------------------------------------------------------
    header("6. SOFT DELETE & RESTORE")

    db.soft_delete("materials", "MAT-002", keep_asset=False)
    print(f"Nach soft_delete MAT-002:")
    print(f"  get_node:     {db.get_node('materials/MAT-002')}   (None = unsichtbar)")
    print(f"  get_node_raw: {db.get_node_raw('materials/MAT-002')}  (raw zeigt _deletion_flag)")
    print(f"  list_nodes:   {list(db.list_nodes('materials').keys())}  (nur MAT-001)")
    print(f"  include_del:  {list(db.list_nodes('materials', include_deleted=True).keys())}")

    db.restore_node("materials", "MAT-002")
    print(f"\nNach restore_node MAT-002:")
    print(f"  get_node: {db.get_node('materials/MAT-002')}")

    # ----------------------------------------------------------------
    # 7. EDGE LÖSCHEN
    # ----------------------------------------------------------------
    header("7. EDGE DIREKT LÖSCHEN")

    alle_edges = db.list_edges()
    eine_edge_id = list(alle_edges.keys())[0]
    print(f"Lösche Edge {eine_edge_id}: {alle_edges[eine_edge_id]}")
    db.delete_edge(eine_edge_id)
    print(f"Nach delete_edge: get_edge = {db.get_edge(eine_edge_id)}")
    print(f"Verbleibende Edges: {len(db.list_edges())}")

    # ----------------------------------------------------------------
    # 8. GARBAGE COLLECTION mit CASCADE DELETE
    # ----------------------------------------------------------------
    header("8. GARBAGE COLLECTION (CASCADE DELETE)")

    # Schweißprozess soft-deleten -> cascade trägt Dokument mit
    print(f"Asset vor GC vorhanden: {os.path.exists(vault_path)}")
    print(f"Soft-Delete {p_schweissen} (cascade_delete=True auf hat_dokument-Edge)")
    db.soft_delete("processes", "PROC-001-B", keep_asset=False)

    gc = MaintenanceEngine(root_dir=root, schemas=schemas)
    stats = gc.run_garbage_collection(verbose=True)
    print(f"\nGC-Stats: {stats}")

    print(f"\nNach GC:")
    print(f"  PROC-001-B existiert noch: {gc.get_node(p_schweissen)}")
    print(f"  DOC-001 existiert noch:    {gc.get_node(doc_ref)}")
    print(f"  Asset im Vault:            {os.path.exists(vault_path)}")
    archived = os.listdir(os.path.join(root, "vault_archive"))
    print(f"  Dateien in vault_archive:  {archived}")

    # ----------------------------------------------------------------
    # 9. ABSCHLIESSENDE ÜBERSICHT
    # ----------------------------------------------------------------
    header("9. FINALE ÜBERSICHT")

    db2 = FlatGraphDB(root_dir=root)  # Neuladen vom Disk
    print("Datenbank neu geladen vom Disk:")
    for col in db2.list_collections():
        nodes = db2.list_nodes(col)
        print(f"  {col:<15} {len(nodes):>2} Nodes: {list(nodes.keys())}")
    print(f"\n  Edges gesamt: {len(db2.list_edges())}")

    # ----------------------------------------------------------------
    # AUFRÄUMEN
    # ----------------------------------------------------------------
    shutil.rmtree(root)
    print(f"\n{SEP}")
    print("  Demo abgeschlossen. Temporäres Verzeichnis gelöscht.")
    print(SEP)


if __name__ == "__main__":
    run_demo()
