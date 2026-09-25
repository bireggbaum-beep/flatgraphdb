"""
Transaktion mit Undo-Log: ein Rollback stellt GENAU den Stand davor her.

Bis 3.0.0 kopierte `transaction()` beim Betreten den ganzen Bestand tief
und setzte bei einem Fehler auf diese Kopie zurück — bei 10 000 Knoten
156 ms, auch für eine einzige Änderung. Seit 3.0.0-entwurf merkt sich
jede Änderung vor ihrer ersten Berührung den alten Zustand, und ein
Rollback setzt nur diese Einträge zurück.

Die Gefahr dabei: eine Stelle, die ändert, ohne vorher vorzumerken, wird
beim Rollback nicht zurückgesetzt. Deshalb vergleicht diese Suite nach
jedem Rollback den VOLLSTÄNDIGEN Zustand über die öffentliche
Schnittstelle — alle Knoten samt weich gelöschten, alle Kanten, die
Nachbarschaft jedes Knotens in beide Richtungen, Suchergebnisse,
Sammlungen — im Speicher UND nach einem Neustart.

Gegen 3.0.0 bestehen die Rollback-Prüfungen, denn die volle Kopie ist
richtig, nur teuer. Fallen muss dort nur die Prüfung, dass der
Müllsammler in einer Transaktion abgewiesen wird. Die eigentliche
Gegenprobe ist, im Entwurf eine Vormerk-Stelle zu entfernen.

    python tests/test_flatgraph_transaktion.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_transaktion.py

Importiert `pdms` nicht.
"""
import os
import random
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen                          # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


class _Abbruch(Exception):
    pass


WURZEL = tempfile.mkdtemp(prefix="fg_transaktion_")


def zustand(db):
    """Alles, was ein Aufrufer über den Bestand erfahren kann."""
    sammlungen = sorted(db.list_collections())
    knoten = {s: db.list_nodes(s, include_deleted=True) for s in sammlungen}
    kanten = {eid: (e["source"], e["target"], e["type"]) for eid, e in db.list_edges().items()}
    refs = [f"{s}/{k}" for s in sammlungen for k in knoten[s]]
    nachbarn = {r: (sorted(db.get_connected(r, "out", include_deleted=True)),
                    sorted(db.get_connected(r, "in", include_deleted=True))) for r in refs}
    suche = {s: sorted(db.find_nodes(s, {"ort": "a"})) for s in sammlungen}
    return {"sammlungen": sammlungen, "knoten": knoten, "kanten": kanten,
            "nachbarn": nachbarn, "suche": suche}


def unterschied(a, b):
    for teil in a:
        if a[teil] != b[teil]:
            return teil
    return None


def verworfen(db, schritte):
    """Führt `schritte` in einer Transaktion aus und bricht dann ab."""
    try:
        with db.transaction():
            schritte()
            raise _Abbruch()
    except _Abbruch:
        pass


def grundbestand(**kw):
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), **kw)
    for i in range(6):
        db.create_node("dinge", f"d{i}", {"ort": ["Keller", "Dach", "Halle"][i % 3], "n": i})
    db.create_node("orte", "o1", {"ort": "Lager"})
    db.create_edge("dinge/d0", "dinge/d1", "teil")
    db.create_edge("dinge/d1", "dinge/d2", "teil")
    db.create_edge("dinge/d0", "orte/o1", "steht_in")
    db.soft_delete("dinge", "d5")
    db.find_nodes("dinge", {"ort": "a"})          # Feldindex warm
    return db


# =========================================================================
print("--- Jede Schreibmethode einzeln, dann Rollback ---")
# =========================================================================
faelle = {
    "create_node": lambda db: db.create_node("dinge", "neu", {"ort": "Dach"}),
    "create_node in neuer Sammlung": lambda db: db.create_node("frisch", "x", {"ort": "a"}),
    "update_node": lambda db: db.update_node("dinge", "d0", {"ort": "Garten", "n": 99}),
    "soft_delete": lambda db: db.soft_delete("dinge", "d1"),
    "restore_node": lambda db: db.restore_node("dinge", "d5"),
    "create_edge": lambda db: db.create_edge("dinge/d3", "dinge/d4", "teil"),
    "create_edge neuer Art": lambda db: db.create_edge("dinge/d3", "orte/o1", "frisch"),
    "delete_edge": lambda db: db.delete_edge(db.get_connected_edges("dinge/d0", "out", "teil")[0][0]),
    "derselbe Knoten zweimal": lambda db: (db.update_node("dinge", "d2", {"ort": "A"}),
                                           db.update_node("dinge", "d2", {"ort": "B"})),
    "anlegen, dann löschen": lambda db: (db.create_node("dinge", "kurz", {"ort": "a"}),
                                        db.soft_delete("dinge", "kurz")),
    "Kante anlegen, dann löschen": lambda db: db.delete_edge(
        db.create_edge("dinge/d2", "dinge/d3", "teil")),
    "alles zusammen": lambda db: (
        db.create_node("frisch", "y", {"ort": "Dach"}),
        db.update_node("dinge", "d0", {"ort": "Nirgends"}),
        db.soft_delete("dinge", "d3"),
        db.restore_node("dinge", "d5"),
        db.create_edge("frisch/y", "dinge/d0", "neu_art"),
        db.delete_edge(db.get_connected_edges("dinge/d1", "out", "teil")[0][0]),
        db.find_nodes("dinge", {"ort": "a"})),
}
for name, schritt in faelle.items():
    db = grundbestand()
    vorher = zustand(db)
    verworfen(db, lambda: schritt(db))
    im_speicher = unterschied(vorher, zustand(db))
    db = neu_oeffnen(db)
    auf_platte = unterschied(vorher, zustand(db))
    check(f"Rollback nach {name}: Speicher und Platte wie vorher",
          im_speicher is None and auf_platte is None,
          f"Speicher: {im_speicher}, Platte: {auf_platte}")

# Das Protokoll gehört zur Transaktion.
db = grundbestand(audit=True)
eintraege = len(db.list_nodes("_audit_log"))
verworfen(db, lambda: db.update_node("dinge", "d0", {"ort": "Garten"}))
check("Eine zurückgenommene Änderung hinterlässt keinen Protokolleintrag",
      len(db.list_nodes("_audit_log")) == eintraege
      and len(neu_oeffnen(db).list_nodes("_audit_log")) == eintraege)

# =========================================================================
print("--- Verschachtelt, abgeschlossen, Müllsammler ---")
# =========================================================================
db = grundbestand()
vorher = zustand(db)
try:
    with db.transaction():
        db.update_node("dinge", "d0", {"ort": "Aussen"})
        with db.transaction():
            db.update_node("dinge", "d1", {"ort": "Innen"})
            raise _Abbruch()
except _Abbruch:
    pass
check("Ein Fehler in der inneren Transaktion nimmt beide zurück",
      unterschied(vorher, zustand(db)) is None)

db = grundbestand()
with db.transaction():
    db.update_node("dinge", "d0", {"ort": "Garten"})
    db.create_edge("dinge/d0", "dinge/d4", "teil")
danach = zustand(db)
check("Eine abgeschlossene Transaktion bleibt, auch nach dem Neustart",
      danach["knoten"]["dinge"]["d0"]["ort"] == "Garten"
      and unterschied(danach, zustand(neu_oeffnen(db))) is None)

db = grundbestand()
db.soft_delete("dinge", "d2")
vorher = zustand(db)
e = None
try:
    with db.transaction():
        db.run_garbage_collection() if hasattr(db, "run_garbage_collection") else None
except Exception as x:
    e = x
check("Der Müllsammler wird in einer Transaktion abgewiesen",
      type(e).__name__ == "NichtInTransaktion", repr(e))
check("… und hat nichts verändert", unterschied(vorher, zustand(db)) is None)

# =========================================================================
print("--- Zufällige Folgen, jede verworfen ---")
# =========================================================================
rnd = random.Random(20260924)
db = grundbestand()
zaehler = iter(range(10**6))


def zufallsschritt():
    lebend = sorted(db.list_nodes("dinge"))
    alle = sorted(db.list_nodes("dinge", include_deleted=True))
    geloescht = sorted(set(alle) - set(lebend))
    kanten = sorted(db.list_edges())
    wahl = rnd.randrange(7)
    if wahl == 0 or not lebend:
        db.create_node(rnd.choice(["dinge", "neu"]), f"z{next(zaehler)}",
                       {"ort": rnd.choice(["Keller", "Dach", "Halle"])})
    elif wahl == 1:
        db.update_node("dinge", rnd.choice(lebend), {"ort": rnd.choice(["Au", "Bach", None])})
    elif wahl == 2:
        db.soft_delete("dinge", rnd.choice(lebend))
    elif wahl == 3 and geloescht:
        db.restore_node("dinge", rnd.choice(geloescht))
    elif wahl == 4 and len(lebend) > 1:
        db.create_edge(f"dinge/{rnd.choice(lebend)}", f"dinge/{rnd.choice(lebend)}",
                       rnd.choice(["teil", "neben", "frisch"]))
    elif wahl == 5 and kanten:
        db.delete_edge(rnd.choice(kanten))
    else:
        db.find_nodes("dinge", {"ort": "a"})


falsch = []
for runde in range(60):
    # Zwischen den verworfenen Runden auch etwas bleiben lassen, damit der
    # Bestand sich verändert und nicht immer derselbe Ausgangspunkt geprüft
    # wird.
    for _ in range(rnd.randrange(3)):
        zufallsschritt()
    vorher = zustand(db)
    verworfen(db, lambda: [zufallsschritt() for _ in range(rnd.randrange(1, 8))])
    teil = unterschied(vorher, zustand(db))
    if teil:
        falsch.append((runde, teil))
check("60 zufällige Folgen, jede verworfen: der Speicher ist jedes Mal wie vorher",
      not falsch, str(falsch[:3]))
vorher = zustand(db)
check("… und die Platte auch", unterschied(vorher, zustand(neu_oeffnen(db))) is None)

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
