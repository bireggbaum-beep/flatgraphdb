"""
`find_path`: kürzester Weg nach Sprüngen, ungewichtet.

    python tests/test_flatgraph_weg.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                       # noqa: E402

fg = lade()
results = []
WURZEL = tempfile.mkdtemp(prefix="fg_weg_")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def wirft(fn):
    try:
        fn()
        return None
    except Exception as e:
        return type(e).__name__


db = fg.FlatGraphDB(os.path.join(WURZEL, "b"))
for k in "ABCDEFG":
    db.create_node("n", k, {})
# A -> B -> C -> D (lang), A -> E -> D (kurz), F einzeln, G -> A (nur rückwärts)
e = {}
for q, z, art in [("A", "B", "x"), ("B", "C", "x"), ("C", "D", "x"),
                  ("A", "E", "x"), ("E", "D", "y"), ("G", "A", "x")]:
    e[q + z] = db.create_edge(f"n/{q}", f"n/{z}", art)
refs = lambda weg: [r for r, _ in weg]                                 # noqa: E731

w = db.find_path("n/A", "n/D")
check("Der kürzeste Weg gewinnt, nicht der zuerst angelegte",
      refs(w) == ["n/A", "n/E", "n/D"], str(w))
check("Zu jedem Knoten gehört die Kante, über die man kam",
      w == [("n/A", None), ("n/E", e["AE"]), ("n/D", e["ED"])], str(w))
check("Start gleich Ziel ist der Weg aus dem Start allein",
      db.find_path("n/A", "n/A") == [("n/A", None)])
check("Ohne Weg kommt None", db.find_path("n/A", "n/F") is None)
check("Gerichtet: gegen die Richtung gibt es keinen Weg",
      db.find_path("n/D", "n/A") is None)
check("direction='in' geht gegen die Richtung",
      refs(db.find_path("n/D", "n/A", direction="in")) == ["n/D", "n/E", "n/A"])
check("direction='both' findet über beide Richtungen",
      refs(db.find_path("n/G", "n/D", direction="both"))[-1] == "n/D")
check("rel_type beschränkt die Kantenart",
      refs(db.find_path("n/A", "n/D", rel_type="x")) == ["n/A", "n/B", "n/C", "n/D"])
check("max_depth kappt den Weg",
      db.find_path("n/A", "n/D", rel_type="x", max_depth=2) is None
      and db.find_path("n/A", "n/D", rel_type="x", max_depth=3) is not None)
check("kantenfilter sperrt Kanten",
      refs(db.find_path("n/A", "n/D", kantenfilter=lambda k: k["type"] == "x"))
      == ["n/A", "n/B", "n/C", "n/D"])
db.soft_delete("n", "E")
check("Weich gelöschte Knoten liegen nie auf dem Weg",
      refs(db.find_path("n/A", "n/D")) == ["n/A", "n/B", "n/C", "n/D"])
check("Fehlendes Ziel wirft KnotenFehlt, ist nicht 'kein Weg'",
      wirft(lambda: db.find_path("n/A", "n/ZZ")) == "KnotenFehlt")
check("Fehlender Start wirft KnotenFehlt",
      wirft(lambda: db.find_path("n/ZZ", "n/A")) == "KnotenFehlt")
check("Weich gelöschtes Ziel wirft KnotenFehlt",
      wirft(lambda: db.find_path("n/A", "n/E")) == "KnotenFehlt")
check("Ein Kreis hält die Suche nicht auf",
      (db.create_edge("n/D", "n/A", "x"), db.find_path("n/A", "n/F"))[1] is None)
db.close()

# Ungerichtet: von beiden Enden, egal wie angelegt
db = fg.FlatGraphDB(os.path.join(WURZEL, "u"))
for k in "XYZ":
    db.create_node("n", k, {})
db.create_edge("n/Y", "n/X", "nah", gerichtet=False)
db.create_edge("n/Y", "n/Z", "nah", gerichtet=False)
check("Ungerichtet: Weg über beide Kanten, unabhängig von der Anlegerichtung",
      refs(db.find_path("n/X", "n/Z", direction="both")) == ["n/X", "n/Y", "n/Z"])
check("Ungerichtet: auch mit direction='out' von beiden Enden",
      db.find_path("n/Z", "n/X") is not None)
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)
print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
