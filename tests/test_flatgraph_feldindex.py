"""
Der Feldindex: nachgeführt statt verworfen — und nie falsch.

Seit 3.0.0-entwurf liegt der Feldindex nur im Speicher und wird bei jedem
Schreibvorgang für genau den einen Knoten nachgeführt, statt bei jeder
Änderung für die ganze Sammlung verworfen und neu gebaut zu werden.
Gemessen (flatgraph/bench, Block 6, 10 000 Knoten): Änderung plus Suche
kostete vorher 159 ms, die Änderung allein 0.8 ms.

Die Gefahr dabei ist nicht Langsamkeit, sondern ein Index, der etwas
anderes sagt als der Bestand: eine Suche, die einen Knoten verschweigt,
sieht aus wie ein Bestand ohne diesen Knoten. Deshalb vergleicht diese
Suite nach JEDEM Schritt jede Suche mit einem Durchgang über alle Knoten —
über eine zufällige, aber feste Folge aller Wege, auf denen sich ein
Knoten ändern kann.

Nebenbei festgenagelt: bis 3.0.0 blieb der Index nach einem Rollback
stehen. Wer in der Transaktion gesucht hatte, fand danach den verworfenen
Wert und den tatsächlichen nicht mehr. Gegen 3.0.0 fällt diese Suite
deshalb — das ist ihre Gegenprobe:

    python tests/test_flatgraph_feldindex.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_feldindex.py

Importiert `pdms` nicht.
"""
import os
import random
import re
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


WURZEL = tempfile.mkdtemp(prefix="fg_feldindex_")
S = "dinge"
ORTE = ["Keller", "Dach", "Halle 1", "Halle 2", "Werkstatt"]
FELDER = ["ort", "zahl", "marken"]
# Teilstring und Platzhalter, je so gewählt, dass sie mehrere Werte treffen.
NADELN = ["keller", "halle", "1", "a", "[", "7"]
MUSTER = ["ha*", "*1", "d*h", "*"]


def zufallswert(rnd, feld):
    """Auch None und fehlende Felder: beide fallen aus dem Index heraus."""
    if rnd.random() < 0.15:
        return None
    if feld == "ort":
        return rnd.choice(ORTE)
    if feld == "zahl":
        return rnd.randrange(20)
    return rnd.sample(["eilig", "extern", "neu"], rnd.randrange(3))


def erwartet(db, feld, nadel=None, muster=None):
    """Die Wahrheit: ein Durchgang über alle lebenden Knoten, ohne Index."""
    if muster is not None:
        rx = re.compile("^" + re.escape(muster.lower()).replace(r"\*", ".*") + "$")
    treffer = set()
    for nid, d in db.list_nodes(S).items():
        val = d.get(feld)
        if val is None:
            continue
        text = str(val).lower()
        if (nadel is not None and nadel in text) or (muster is not None and rx.match(text)):
            treffer.add(nid)
    return treffer


def abweichungen(db):
    """Jede Suche gegen die Wahrheit. Leer heisst: der Index stimmt."""
    falsch = []
    for feld in FELDER:
        for nadel in NADELN:
            ist = set(db.find_nodes(S, {feld: nadel}))
            soll = erwartet(db, feld, nadel=nadel)
            if ist != soll:
                falsch.append(f"{feld}~{nadel!r}: zu viel {sorted(ist - soll)[:3]}, "
                              f"fehlt {sorted(soll - ist)[:3]}")
        for muster in MUSTER:
            ist = set(db.find_nodes(S, {feld: muster}))
            soll = erwartet(db, feld, muster=muster)
            if ist != soll:
                falsch.append(f"{feld}={muster!r}: zu viel {sorted(ist - soll)[:3]}, "
                              f"fehlt {sorted(soll - ist)[:3]}")
    return falsch


class _Abbruch(Exception):
    pass


# =========================================================================
print("--- Zufällige Folge aller Schreibwege ---")
# =========================================================================
rnd = random.Random(20260924)
ort = tempfile.mkdtemp(dir=WURZEL)
db = fg.MaintenanceEngine(ort)
naechste_id = iter(range(10**6))


def neuer_knoten():
    daten = {}
    for feld in FELDER:
        wert = zufallswert(rnd, feld)
        if wert is not None or rnd.random() < 0.5:
            daten[feld] = wert
    db.create_node(S, f"k{next(naechste_id):04d}", daten)


def irgendeiner(auch_geloeschte=False):
    knoten = db.list_nodes(S, include_deleted=auch_geloeschte)
    return rnd.choice(sorted(knoten)) if knoten else None


def aendern():
    nid = irgendeiner()
    if nid:
        feld = rnd.choice(FELDER)
        db.update_node(S, nid, {feld: zufallswert(rnd, feld)})


def weich_loeschen():
    nid = irgendeiner()
    if nid:
        db.soft_delete(S, nid)


def wiederherstellen():
    geloescht = [k for k, v in db.list_nodes(S, include_deleted=True).items()
                 if "_deletion_flag" in v]
    if geloescht:
        db.restore_node(S, rnd.choice(sorted(geloescht)))


def einzelschritt():
    rnd.choice([neuer_knoten, aendern, aendern, weich_loeschen, wiederherstellen])()


def transaktion_abschliessen():
    with db.transaction():
        for _ in range(rnd.randrange(1, 4)):
            einzelschritt()
            # In der Transaktion suchen: das wärmt den Index mit Werten,
            # die es nach einem Rollback nicht mehr gäbe.
            db.find_nodes(S, {"ort": "a"})


def transaktion_verwerfen():
    try:
        with db.transaction():
            for _ in range(rnd.randrange(1, 4)):
                einzelschritt()
                db.find_nodes(S, {"ort": "a"})
            raise _Abbruch()
    except _Abbruch:
        pass


def aufraeumen():
    db.run_garbage_collection()


for _ in range(30):
    neuer_knoten()

schritte = {
    "Anlegen": neuer_knoten,
    "Ändern": aendern,
    "weich Löschen": weich_loeschen,
    "Wiederherstellen": wiederherstellen,
    "abgeschlossener Transaktion": transaktion_abschliessen,
    "Rollback": transaktion_verwerfen,
    "Aufräumen": aufraeumen,
}
gewichte = [3, 6, 2, 2, 2, 2, 1]
fehler = {name: [] for name in schritte}
gezaehlt = {name: 0 for name in schritte}
abweichungen(db)                                # Index einmal warm machen
for _ in range(400):
    name = rnd.choices(list(schritte), gewichte)[0]
    schritte[name]()
    gezaehlt[name] += 1
    falsch = abweichungen(db)
    if falsch:
        fehler[name].append(falsch[0])

for name in schritte:
    check(f"Nach {name} findet jede Suche genau, was im Bestand steht",
          not fehler[name],
          f"{len(fehler[name])} von {gezaehlt[name]} Schritten falsch, z. B. {fehler[name][0]}"
          if fehler[name] else f"{gezaehlt[name]} Schritte")

check("Nach einem Neustart findet jede Suche genau, was im Bestand steht",
      not abweichungen(neu_oeffnen(db, fg.FlatGraphDB)))

# =========================================================================
print("--- Der Rollback-Fall aus 3.0.0, einzeln ---")
# =========================================================================
db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
db.create_node(S, "a", {"ort": "Keller"})
try:
    with db.transaction():
        db.update_node(S, "a", {"ort": "Dach"})
        db.find_nodes(S, {"ort": "dach"})
        raise _Abbruch()
except _Abbruch:
    pass
check("Nach dem Rollback findet die Suche den verworfenen Wert nicht mehr",
      set(db.find_nodes(S, {"ort": "dach"})) == set())
check("… und den tatsächlichen wieder",
      set(db.find_nodes(S, {"ort": "keller"})) == {"a"})

# =========================================================================
print("--- Keine Datei ---")
# =========================================================================
ort = tempfile.mkdtemp(dir=WURZEL)
db = fg.FlatGraphDB(ort)
db.create_node(S, "a", {"ort": "Keller"})
db.find_nodes(S, {"ort": "keller"})
db.update_node(S, "a", {"ort": "Dach"})
db.find_nodes(S, {"ort": "dach"})
check("Suchen und Ändern schreiben keinen Index auf die Platte",
      not os.path.exists(os.path.join(ort, "datenbank", "index")))

# Ein index/-Verzeichnis, das eine ältere Fassung hinterlassen hat, mit
# einem Inhalt, der NICHT zum Bestand passt: er darf keine Rolle spielen.
alt = os.path.join(ort, "datenbank", "index")
os.makedirs(alt)
with open(os.path.join(alt, f"{S}.json"), "w", encoding="utf-8") as f:
    f.write('{"ort": {"keller": ["a"]}, "_meta": {"checksum": "egal"}}')
neu = neu_oeffnen(db)
check("Ein altes index/-Verzeichnis wird nicht gelesen",
      set(neu.find_nodes(S, {"ort": "keller"})) == set()
      and set(neu.find_nodes(S, {"ort": "dach"})) == {"a"})

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
