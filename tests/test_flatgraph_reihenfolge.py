"""
`topological_sort` und `detect_cycles` (Issue #38): Reihenfolge und Kreise
entlang EINER Kantenart.

Der Anlass: `traverse` sortiert nach Abstand vom Start, nicht nach
Abhängigkeit. Bei A→B→D und A→C→E→D liefert es D vor E, obwohl D auf E
wartet. Die erste Prüfung hält genau das fest.

    python tests/test_flatgraph_reihenfolge.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                       # noqa: E402

fg = lade()
results = []
WURZEL = tempfile.mkdtemp(prefix="fg_reihe_")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def fehler(fn):
    try:
        fn()
        return None
    except Exception as e:
        return e


def bestand(name, knoten, kanten, art="vor"):
    db = fg.FlatGraphDB(os.path.join(WURZEL, name))
    for k in knoten:
        sam, _, nid = k.partition("/")
        db.create_node(sam, nid, {})
    for q, z in kanten:
        db.create_edge(q, z, art)
    return db


# Verzweigung und Zusammenführung: A→B→D, A→C→E→D
db = bestand("zweig", ["s/A", "s/B", "s/C", "s/D", "s/E"],
             [("s/A", "s/B"), ("s/B", "s/D"), ("s/A", "s/C"), ("s/C", "s/E"), ("s/E", "s/D")])
stufen = db.topological_sort("vor")
check("Stufen: was parallel laufen kann, steht in derselben Stufe",
      stufen == [["s/A"], ["s/B", "s/C"], ["s/E"], ["s/D"]], str(stufen))
flach = [k for s in stufen for k in s]
check("Ein Knoten kommt erst nach ALLEN Vorgängern (D nach E, anders als traverse)",
      flach.index("s/E") < flach.index("s/D"))
check("direction='in' kehrt die Reihenfolge um (für Arten wie „folgt auf“)",
      db.topological_sort("vor", direction="in") == [["s/D"], ["s/B", "s/E"], ["s/C"], ["s/A"]],
      str(db.topological_sort("vor", direction="in")))
check("Gleicher Bestand, gleiches Ergebnis", db.topological_sort("vor") == stufen)
check("Ohne Kreis meldet detect_cycles nichts", db.detect_cycles("vor") == [])
check("Eine unbekannte Kantenart hat keine Reihenfolge und keinen Kreis",
      db.topological_sort("gibtsnicht") == [] and db.detect_cycles("gibtsnicht") == [])
e = fehler(lambda: db.topological_sort("vor", direction="both"))
check("direction='both' wird abgewiesen (ValueError)", isinstance(e, ValueError), repr(e))
db.close()

# Kreise
db = bestand("kreis", ["s/A", "s/B", "s/C", "s/D", "s/X", "s/Y"],
             [("s/A", "s/B"), ("s/B", "s/C"), ("s/C", "s/B"), ("s/C", "s/D"),
              ("s/X", "s/X")])
check("detect_cycles nennt die beteiligten Knoten, je Kreis sortiert",
      db.detect_cycles("vor") == [["s/B", "s/C"], ["s/X"]], str(db.detect_cycles("vor")))
e = fehler(lambda: db.topological_sort("vor"))
check("topological_sort wirft ZyklusGefunden, auch ein ValueError",
      isinstance(e, fg.ZyklusGefunden) and isinstance(e, ValueError), repr(e))
check("… und der Fehler trägt die Kreise", getattr(e, "zyklen", None) == [["s/B", "s/C"], ["s/X"]])
check("Eine Kante auf sich selbst ist ein Kreis",
      ["s/X"] in db.detect_cycles("vor"))
db.soft_delete("s", "C")
check("Ein weich gelöschter Knoten bricht seinen Kreis",
      db.detect_cycles("vor") == [["s/X"]], str(db.detect_cycles("vor")))
db.soft_delete("s", "X")
stufen = db.topological_sort("vor")
check("Weich gelöschte Knoten stehen nicht in der Reihenfolge",
      all(k not in ("s/C", "s/X") for s in stufen for k in s) and stufen == [["s/A"], ["s/B"]],
      str(stufen))
db.close()

# Sammlung, Doppelkanten, ungerichtete Kanten
db = bestand("sammlung", ["s/A", "s/B", "s/L", "t/Z"],
             [("s/A", "s/B"), ("s/A", "s/B"), ("s/B", "t/Z"), ("t/Z", "s/A")])
check("Ohne Sammlung zählt auch die Kante in eine andere Sammlung (hier ein Kreis)",
      db.detect_cycles("vor") == [["s/A", "s/B", "t/Z"]], str(db.detect_cycles("vor")))
check("Mit Sammlung nur deren Kanten; Knoten ohne Kante stehen in der ersten Stufe",
      db.topological_sort("vor", sammlung="s") == [["s/A", "s/L"], ["s/B"]],
      str(db.topological_sort("vor", sammlung="s")))
db.create_edge("s/L", "s/B", "nachbar", gerichtet=False)
e = fehler(lambda: db.topological_sort("nachbar"))
check("Eine ungerichtete Kante wird abgewiesen (ValueError), nicht als Kreis gemeldet",
      isinstance(e, ValueError) and not isinstance(e, fg.ZyklusGefunden), repr(e))
check("… auch von detect_cycles", isinstance(fehler(lambda: db.detect_cycles("nachbar")), ValueError))
db.close()

# Lange Kette: keine Rekursionsgrenze
n = 5000
db = fg.FlatGraphDB(os.path.join(WURZEL, "lang"))
with db.transaction():
    for i in range(n):
        db.create_node("k", f"{i:05d}", {})
    for i in range(n - 1):
        db.create_edge(f"k/{i:05d}", f"k/{i + 1:05d}", "vor")
check(f"Kette aus {n} Knoten: {n} Stufen in der richtigen Folge",
      [s[0] for s in db.topological_sort("vor")] == [f"k/{i:05d}" for i in range(n)])
db.create_edge(f"k/{n - 1:05d}", "k/00000", "vor")
z = fehler(lambda: db.detect_cycles("vor"))
check(f"Ein Kreis über {n} Knoten wird gefunden, ohne Rekursionsfehler",
      z is None and len(db.detect_cycles("vor")) == 1 and len(db.detect_cycles("vor")[0]) == n,
      repr(z))
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)
n_ok = sum(1 for _, ok, _ in results if ok)
print(f"\n{n_ok}/{len(results)} Checks bestanden")
sys.exit(0 if n_ok == len(results) else 1)
