"""
next_id vergibt eine Nummer nicht noch einmal, auch nicht nach dem
endgültigen Löschen der höchsten (Issue #37).

    python tests/test_flatgraph_nummern.py
    FLATGRAPH_DATEI=/pfad/zu/flatgraph.py python tests/test_flatgraph_nummern.py
"""
import json
import os
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


WURZEL = tempfile.mkdtemp(prefix="fg_nummern_")
_zaehler = [0]


def werk(n=3, prefix="d_", sammlung="dok"):
    _zaehler[0] += 1
    db = fg.FlatGraphDB(os.path.join(WURZEL, f"b{_zaehler[0]}"))
    for i in range(1, n + 1):
        db.create_node(sammlung, f"{prefix}{i:06d}", {"i": i})
    return db


def weg(db, sammlung, kennung):
    """Weich löschen und endgültig entfernen lassen."""
    db.soft_delete(sammlung, kennung)
    db.run_garbage_collection()


def marke(db):
    pfad = os.path.join(db.root, "datenbank", "_nummern.json")
    if not os.path.exists(pfad):
        return None
    with open(pfad, encoding="utf-8") as f:
        return json.load(f)


# --- Der Fehler selbst --------------------------------------------------
db = werk(3)
weg(db, "dok", "d_000003")
check("Die höchste Nummer ist endgültig weg",
      db.get_node_raw("dok/d_000003") is None)
check("next_id gibt die gelöschte höchste Nummer nicht noch einmal aus",
      db.next_id("dok", prefix="d_", padding=6) == "d_000004")

db = neu_oeffnen(db)
check("Auch nach einem Neustart",
      db.next_id("dok", prefix="d_", padding=6) == "d_000004")
db.create_node("dok", "d_000004", {})
weg(db, "dok", "d_000004")
weg_zwei = db.next_id("dok", prefix="d_", padding=6)
check("Und nach dem nächsten Löschen wieder eine weiter", weg_zwei == "d_000005", weg_zwei)

# --- Was sich nicht ändern darf -----------------------------------------
db = werk(3)
weg(db, "dok", "d_000002")
check("Eine niedrigere Nummer zu löschen ändert nichts",
      db.next_id("dok", prefix="d_", padding=6) == "d_000004")

db = werk(3)
db.soft_delete("dok", "d_000003")
check("Nur im Papierkorb zählt die Nummer wie bisher (Knoten ist noch da)",
      db.next_id("dok", prefix="d_", padding=6) == "d_000004"
      and marke(db) is None)

db = werk(0)
check("Leere Sammlung fängt bei 1 an",
      db.next_id("dok", prefix="d_", padding=6) == "d_000001")

db = werk(3)
check("Ohne Löschen entsteht keine Datei mit Nummern", marke(db) is None)

# --- Sammlungen und Präfixe getrennt ------------------------------------
db = werk(3)
for i in (1, 2):
    db.create_node("dok", f"q_{i:04d}", {})
weg(db, "dok", "d_000003")
check("Ein anderes Präfix in derselben Sammlung bleibt unberührt",
      db.next_id("dok", prefix="q_", padding=4) == "q_0003")
db.create_node("log", "d_000001", {})
check("Dasselbe Präfix in einer anderen Sammlung bleibt unberührt",
      db.next_id("log", prefix="d_", padding=6) == "d_000002")

# --- Ganz leergeräumte Sammlung -----------------------------------------
db = werk(2)
for k in ("d_000001", "d_000002"):
    db.soft_delete("dok", k)
db.run_garbage_collection()
check("Auch wenn die Sammlung leer ist, geht es bei 3 weiter",
      db.list_nodes("dok") == {}
      and db.next_id("dok", prefix="d_", padding=6) == "d_000003")

# --- Mehrere auf einmal, Kennungen ohne Zahl ----------------------------
db = werk(5)
db.create_node("dok", "Anhang", {})
for k in ("d_000003", "d_000005", "Anhang"):
    db.soft_delete("dok", k)
db.run_garbage_collection()
check("Mehrere auf einmal: die höchste gilt",
      db.next_id("dok", prefix="d_", padding=6) == "d_000006")
check("Eine Kennung ohne Zahl am Ende stört nicht",
      db.next_id("dok", prefix="", padding=0) == "1")

db = werk(0)
db.create_node("z", "7", {})
weg(db, "z", "7")
check("Ohne Präfix: reine Zahlen zählen auch",
      db.next_id("z") == "8")

# --- Erst festhalten, dann löschen --------------------------------------
db = werk(3)
db.soft_delete("dok", "d_000003")
echte = db._purge_node


class Abbruch(Exception):
    pass


def bricht(*a, **kw):
    raise Abbruch()


db._purge_node = bricht
try:
    db.run_garbage_collection()
except Abbruch:
    pass
check("Bricht das Löschen ab, steht die Nummer schon in der Datei",
      (marke(db) or {}).get("dok", {}).get("d_") == 3, str(marke(db)))
check("Der Knoten ist dann noch da (nichts halb getan)",
      db.get_node_raw("dok/d_000003") is not None)
db._purge_node = echte
db.run_garbage_collection()
check("Der nächste Lauf räumt auf, die Nummer bleibt vergeben",
      db.get_node_raw("dok/d_000003") is None
      and db.next_id("dok", prefix="d_", padding=6) == "d_000004")

# --- Die Datei ----------------------------------------------------------
db = werk(3)
weg(db, "dok", "d_000003")
check("Die Datei ist reines JSON: {sammlung: {praefix: zahl}}",
      marke(db) == {"dok": {"d_": 3}}, str(marke(db)))
check("Kein Rest der Arbeitsdatei bleibt liegen",
      not os.path.exists(os.path.join(db.root, "datenbank", "_nummern.json.tmp")))

db = werk(3)
weg(db, "dok", "d_000003")
db = neu_oeffnen(db)
db.create_node("dok", "d_000004", {})
db.create_node("dok", "d_000005", {})
weg(db, "dok", "d_000004")
check("Die Marke sinkt nie: 4 gelöscht, 5 lebt, es geht bei 6 weiter",
      db.next_id("dok", prefix="d_", padding=6) == "d_000006"
      and marke(db) == {"dok": {"d_": 4}}, str(marke(db)))

db = werk(3)
weg(db, "dok", "d_000003")
weg(db, "dok", "d_000001")
check("Eine niedrigere Nummer später gelöscht senkt die Marke nicht",
      marke(db) == {"dok": {"d_": 3}}
      and db.next_id("dok", prefix="d_", padding=6) == "d_000004", str(marke(db)))

# --- Ein Bestand aus der Zeit davor -------------------------------------
db = werk(3)
weg(db, "dok", "d_000003")
os.remove(os.path.join(db.root, "datenbank", "_nummern.json"))
db = neu_oeffnen(db)
check("Fehlt die Datei, gilt wie bisher der Bestand",
      db.next_id("dok", prefix="d_", padding=6) == "d_000003")

shutil.rmtree(WURZEL, ignore_errors=True)

print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
