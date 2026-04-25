"""
gen_testnodes.py — Testdaten-Generator fuer FlatGraphDB Performance-Tests

Verwendung:
  python gen_testnodes.py [--nodes 5000] [--db _test_db] [--gc]

Erzeugt realistische Testnodes in mehreren Collections:
  equipments   (~40%)
  documents    (~30%)
  issues       (~20%)
  logbook      (~10%)

Und verbindet sie mit Edges (hat_issue, object_link, hat_logbuch).
"""

import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from flatgraph import FlatGraphDB, MaintenanceEngine

# --- Beispieldaten ---

EQ_NAMES = [
    "Pumpe", "Motor", "Ventil", "Kompressor", "Filter", "Wärmetauscher",
    "Rührwerk", "Gebläse", "Dosieranlage", "Reaktor", "Kühlaggregat",
    "Förderband", "Druckbehälter", "Messgerät", "Steuereinheit",
]
EQ_CATEGORIES = ["MECHANISCH", "ELEKTRISCH", "INSTRUMENT", "ROHRLEITG", "BEHAELTER"]
EQ_STATUS     = ["AKTIV", "AKTIV", "AKTIV", "INAKTIV", "STILLGELEGT"]
MANUFACTURERS = ["Siemens", "ABB", "Bosch", "KSB", "Grundfos", "Endress+Hauser", "Wilo"]
LOCATIONS     = ["Halle 1", "Halle 2", "Halle 3", "Außenanlage", "Technikraum"]
COST_CENTERS  = ["K-100", "K-200", "K-300", "K-400"]

DOC_TYPES  = ["MAN", "WAR", "PRO", "ZEI", "SDB", "PRF"]
DOC_STATUS = ["WK", "WK", "FR", "OB"]
DOC_TITLES = [
    "Wartungsanleitung", "Betriebsanleitung", "Prüfprotokoll",
    "Technische Zeichnung", "Sicherheitsdatenblatt", "Arbeitsanweisung",
    "Inbetriebnahmeprotokoll", "Kalibrierprotokoll", "Reparaturbericht",
]

ISSUE_TYPES    = ["STOERUNG", "WARTUNG", "AENDERUNG", "PRUEFUNG"]
ISSUE_STATUS   = ["OFFEN", "OFFEN", "IN_ARBEIT", "ERLEDIGT"]
ISSUE_PRIORITY = ["HOCH", "MITTEL", "MITTEL", "NIEDRIG"]
ISSUE_TITLES   = [
    "Ungewöhnliche Geräuschentwicklung", "Routinewartung fällig",
    "Dichtung erneuern", "Kalibrierung erforderlich",
    "Druckverlust festgestellt", "Temperaturanomalie",
    "Sichtprüfung durchführen", "Leckage an Flansch",
]

LOG_ENTRIES = [
    "Sichtprüfung durchgeführt, keine Auffälligkeiten.",
    "Ölwechsel durchgeführt.",
    "Techniker vor Ort, Dichtung getauscht.",
    "Messung der Betriebsparameter, alle Werte im Normbereich.",
    "Kleinere Justierung der Einstellparameter.",
    "Reinigung der Filtereinheit.",
    "Funktionstest nach Wartung bestanden.",
    "Auffälligkeit gemeldet, wird beobachtet.",
]


def rnd(lst):
    return random.choice(lst)


def main():
    parser = argparse.ArgumentParser(description="FlatGraphDB Testdaten-Generator")
    parser.add_argument("--nodes", type=int, default=5000, help="Anzahl Nodes gesamt")
    parser.add_argument("--db",    type=str, default="_test_db", help="Datenbankpfad")
    parser.add_argument("--gc",    action="store_true", help="GC nach Generierung ausfuehren")
    parser.add_argument("--clean", action="store_true", help="DB vorher loeschen")
    args = parser.parse_args()

    if args.clean and os.path.exists(args.db):
        import shutil
        shutil.rmtree(args.db)
        print(f"DB '{args.db}' geloescht.")

    n_eq  = int(args.nodes * 0.40)
    n_doc = int(args.nodes * 0.30)
    n_iss = int(args.nodes * 0.20)
    n_log = args.nodes - n_eq - n_doc - n_iss

    print(f"\nGeneriere {args.nodes} Nodes in '{args.db}':")
    print(f"  {n_eq} Equipments  |  {n_doc} Dokumente  |  {n_iss} Issues  |  {n_log} Logbuch-Eintraege")

    db = FlatGraphDB(args.db)
    t0 = time.perf_counter()

    with db.transaction():
        # --- Equipments ---
        eq_refs = []
        t1 = time.perf_counter()
        for i in range(1, n_eq + 1):
            nid = f"EQ-{i:05d}"
            db.create_node("equipments", nid, {
                "description":       f"{rnd(EQ_NAMES)} {rnd(LOCATIONS)} {i}",
                "category":          rnd(EQ_CATEGORIES),
                "status":            rnd(EQ_STATUS),
                "manufacturer":      rnd(MANUFACTURERS),
                "model":             f"MOD-{random.randint(100,999)}",
                "serial_nr":         f"SN{random.randint(100000,999999)}",
                "construction_year": str(random.randint(2010, 2024)),
                "cost_center":       rnd(COST_CENTERS),
                "created_by":        "gen_testnodes",
                "created_at":        "2026-01-01T00:00:00Z",
                "changed_at":        "2026-01-01T00:00:00Z",
            })
            eq_refs.append(f"equipments/{nid}")
        print(f"  Equipments:  {time.perf_counter()-t1:.2f}s")

        # --- Dokumente ---
        doc_refs = []
        t1 = time.perf_counter()
        for i in range(1, n_doc + 1):
            nid = f"DOC-{i:05d}"
            db.create_node("documents", nid, {
                "title":      f"{rnd(DOC_TITLES)} {i}",
                "doc_type":   rnd(DOC_TYPES),
                "doc_part":   "001",
                "status":     rnd(DOC_STATUS),
                "language":   "DE",
                "created_by": "gen_testnodes",
                "created_at": "2026-01-01T00:00:00Z",
                "changed_at": "2026-01-01T00:00:00Z",
            })
            doc_refs.append(f"documents/{nid}")
            if eq_refs:
                db.create_edge(f"documents/{nid}", random.choice(eq_refs), "object_link")
        print(f"  Dokumente:   {time.perf_counter()-t1:.2f}s")

        # --- Issues ---
        t1 = time.perf_counter()
        for i in range(1, n_iss + 1):
            nid = f"ISS-{i:05d}"
            linked = random.choice(eq_refs) if eq_refs else None
            db.create_node("issues", nid, {
                "title":       f"{rnd(ISSUE_TITLES)} #{i}",
                "issue_type":  rnd(ISSUE_TYPES),
                "status":      rnd(ISSUE_STATUS),
                "priority":    rnd(ISSUE_PRIORITY),
                "created_by":  "gen_testnodes",
                "created_at":  "2026-01-01T00:00:00Z",
                "changed_at":  "2026-01-01T00:00:00Z",
                "linked_ref":  linked or "",
            })
            if linked:
                db.create_edge(linked, f"issues/{nid}", "hat_issue")
        print(f"  Issues:      {time.perf_counter()-t1:.2f}s")

        # --- Logbuch ---
        t1 = time.perf_counter()
        for i in range(1, n_log + 1):
            nid = f"LOG-{i:05d}"
            linked = random.choice(eq_refs) if eq_refs else None
            db.create_node("logbook", nid, {
                "entry_date": f"2026-{random.randint(1,4):02d}-{random.randint(1,28):02d}",
                "text":       rnd(LOG_ENTRIES),
                "created_by": "gen_testnodes",
                "created_at": "2026-01-01T00:00:00Z",
                "ref":        linked or "",
            })
            if linked:
                db.create_edge(linked, f"logbook/{nid}", "hat_logbuch")
        print(f"  Logbuch:     {time.perf_counter()-t1:.2f}s")

    total_write = time.perf_counter() - t0
    print(f"\nSchreiben gesamt: {total_write:.2f}s")

    # ---------------------------------------------------------------
    # BENCHMARK 1: Kaltstart — DB neu laden von Disk
    # ---------------------------------------------------------------
    print("\nBenchmark 1 — Kaltstart (alle Collections von Disk laden):")
    t1 = time.perf_counter()
    db2 = FlatGraphDB(args.db)
    t_load = time.perf_counter() - t1
    total_nodes = sum(len(db2._cache["nodes"].get(c, {})) for c in db2.list_collections())
    print(f"  Ladezeit: {t_load:.3f}s  ({total_nodes} Nodes, {len(db2.list_collections())} Collections)")

    # ---------------------------------------------------------------
    # BENCHMARK 2: Reads — verschiedene Abfragetypen
    # ---------------------------------------------------------------
    print("\nBenchmark 2 — Reads:")

    t1 = time.perf_counter()
    all_eq = db2.list_nodes("equipments")
    print(f"  list_nodes(equipments):                    {time.perf_counter()-t1:.4f}s  ({len(all_eq)} Nodes)")

    t1 = time.perf_counter()
    aktiv = db2.find_nodes("equipments", {"status": "AKTIV"})
    print(f"  find_nodes(status=AKTIV):                  {time.perf_counter()-t1:.4f}s  ({len(aktiv)} Treffer)")

    t1 = time.perf_counter()
    gefiltert = db2.find_nodes("equipments", {"category": "MECHANISCH", "status": "AKTIV"})
    print(f"  find_nodes(category+status, 2 Filter):     {time.perf_counter()-t1:.4f}s  ({len(gefiltert)} Treffer)")

    t1 = time.perf_counter()
    offene = db2.find_nodes("issues", {"status": "OFFEN"})
    print(f"  find_nodes(issues, status=OFFEN):          {time.perf_counter()-t1:.4f}s  ({len(offene)} Treffer)")

    t1 = time.perf_counter()
    hoch = db2.find_nodes("issues", {"priority": "HOCH", "status": "OFFEN"})
    print(f"  find_nodes(issues, priority+status):       {time.perf_counter()-t1:.4f}s  ({len(hoch)} Treffer)")

    # get_connected auf einen Node mit vielen Edges
    busiest = max(eq_refs, key=lambda r: len(db2.get_connected(r, direction="out")))
    t1 = time.perf_counter()
    connected = db2.get_connected(busiest, direction="out")
    print(f"  get_connected(busiest eq, out):            {time.perf_counter()-t1:.4f}s  ({len(connected)} Edges)")

    t1 = time.perf_counter()
    for ref in list(all_eq.keys())[:100]:
        db2.get_node(f"equipments/{ref}")
    t_100 = time.perf_counter() - t1
    print(f"  get_node() x100 einzeln:                   {t_100:.4f}s  ({t_100/100*1000:.3f}ms/Node)")

    # ---------------------------------------------------------------
    # BENCHMARK 3: Einzelschreiben (ausserhalb Transaktion)
    # ---------------------------------------------------------------
    print("\nBenchmark 3 — Einzelschreiben (kein transaction()):")

    t1 = time.perf_counter()
    for i in range(100):
        db2.update_node("equipments", f"EQ-{i+1:05d}", {"changed_at": "2026-04-25T12:00:00Z"})
    t_100w = time.perf_counter() - t1
    print(f"  update_node() x100:                        {t_100w:.3f}s  ({t_100w/100*1000:.1f}ms/Write)")

    t1 = time.perf_counter()
    db2.update_node("equipments", "EQ-00001", {"cost_center": "K-999"})
    print(f"  update_node() einzeln:                     {time.perf_counter()-t1:.4f}s")

    # ---------------------------------------------------------------
    # BENCHMARK 4: Bulk-Write mit transaction()
    # ---------------------------------------------------------------
    print("\nBenchmark 4 — Bulk-Write mit transaction():")

    t1 = time.perf_counter()
    with db2.transaction():
        for i in range(100):
            db2.update_node("equipments", f"EQ-{i+1:05d}", {"cost_center": "K-100"})
    t_tx = time.perf_counter() - t1
    print(f"  update_node() x100 in transaction():       {t_tx:.3f}s  ({t_tx/100*1000:.2f}ms/Write)")
    print(f"  Speedup vs. Einzelschreiben:               {t_100w/t_tx:.0f}x")

    # ---------------------------------------------------------------
    # BENCHMARK 5: RAM-Verbrauch
    # ---------------------------------------------------------------
    print("\nBenchmark 5 — RAM-Verbrauch (Schaetzung):")
    import sys as _sys
    try:
        import tracemalloc
        tracemalloc.start()
        db3 = FlatGraphDB(args.db)
        _ = db3.list_nodes("equipments")
        _ = db3.list_nodes("documents")
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        print(f"  Peak RAM nach Laden:                       {peak/1024/1024:.1f} MB")
        print(f"  Aktuell belegt:                            {current/1024/1024:.1f} MB")
    except Exception as e:
        print(f"  (tracemalloc nicht verfuegbar: {e})")

    # ---------------------------------------------------------------
    # BENCHMARK 6: GC mit echten Loeschungen
    # ---------------------------------------------------------------
    if args.gc:
        print("\nBenchmark 6 — Garbage Collection:")
        n_delete = max(10, n_eq // 20)  # ~5% loeschen
        with db2.transaction():
            for i in range(1, n_delete + 1):
                db2.soft_delete("equipments", f"EQ-{i:05d}")

        gc = MaintenanceEngine(args.db)
        t1 = time.perf_counter()
        stats = gc.run_garbage_collection(verbose=False)
        t_gc = time.perf_counter() - t1
        print(f"  {n_delete} Nodes soft-deleted, GC-Laufzeit: {t_gc:.3f}s")
        print(f"  {stats}")

    print(f"\nFertig. DB liegt in: {os.path.abspath(args.db)}")


if __name__ == "__main__":
    main()
