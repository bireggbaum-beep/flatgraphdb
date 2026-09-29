"""
Ungerichtete Kanten (`create_edge(..., gerichtet=False)`) und
`direction="both"` in allen Abfragen — und dass sich für gerichtete Kanten
nichts ändert.

    python tests/test_flatgraph_ungerichtet.py
    FLATGRAPH_DATEI=/pfad/zu/flatgraph.py python tests/test_flatgraph_ungerichtet.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen, platte                  # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def wirft(typ, f, *a, **kw):
    try:
        f(*a, **kw)
    except typ:
        return True
    except Exception as e:                                           # noqa: BLE001
        return f"falscher Fehler: {type(e).__name__}: {e}"
    return "kein Fehler"


def index_stimmt(db):
    """Der Index gegen eine eigene Rechnung aus den Kanten, die die
    Richtung berücksichtigt: ungerichtet heisst bei beiden Enden in beiden
    Verzeichnissen, gerichtet aus bei der Quelle und ein beim Ziel."""
    soll_out, soll_in = {}, {}
    for art, eimer in db._cache["edges"].items():
        for eid, e in eimer.items():
            enden = [e["source"], e["target"]]
            aus = enden if e.get("gerichtet") is False else enden[:1]
            ein = enden if e.get("gerichtet") is False else enden[1:]
            for r in aus:
                soll_out.setdefault(r, {}).setdefault(art, set()).add(eid)
            for r in ein:
                soll_in.setdefault(r, {}).setdefault(art, set()).add(eid)
    ist_out = {r: {a: set(e) for a, e in arten.items()}
               for r, arten in db._out_index.items()}
    ist_in = {r: {a: set(e) for a, e in arten.items()}
              for r, arten in db._in_index.items()}
    return ist_out == soll_out and ist_in == soll_in


def _in_tx(d):
    with d.transaction():
        d.create_edge("orte/D", "orte/A", "x", gerichtet=False)
        d.create_edge("orte/A", "orte/D", "x", gerichtet=False)


WURZEL = tempfile.mkdtemp(prefix="fg_ungerichtet_")
_zaehler = [0]


def werk(**kw):
    _zaehler[0] += 1
    db = fg.FlatGraphDB(os.path.join(WURZEL, f"b{_zaehler[0]}"), **kw)
    for k in ("A", "B", "C", "D"):
        db.create_node("orte", k, {"name": k})
    return db


# --- Gerichtet bleibt, wie es war ---------------------------------------
db = werk()
e = db.create_edge("orte/A", "orte/B", "fuehrt_zu")
kante = db.get_edge(e)
check("Gerichtete Kante bekommt kein neues Feld",
      "gerichtet" not in kante, str(sorted(kante)))
check("Gerichtet: von der Quelle aus ist das Ziel Nachbar",
      db.get_connected("orte/A", "out") == ["orte/B"])
check("Gerichtet: vom Ziel aus gibt es ausgehend nichts",
      db.get_connected("orte/B", "out") == [])
check("Gerichtet: eingehend am Ziel ist die Quelle",
      db.get_connected("orte/B", "in") == ["orte/A"])
check("Gerichtet: eingehend an der Quelle gibt es nichts",
      db.get_connected("orte/A", "in") == [])
check("Gerichtet: traverse in Gegenrichtung findet nichts",
      db.traverse("orte/B", direction="out") == [])
check("Gerichtet: A->B und B->A sind zwei Kanten, kein Doppelschutz",
      isinstance(db.create_edge("orte/B", "orte/A", "fuehrt_zu"), str)
      and len(db.list_edges("fuehrt_zu")) == 2)
check("Gerichtet: zweimal dieselbe Kante bleibt erlaubt (wie bisher)",
      isinstance(db.create_edge("orte/A", "orte/B", "fuehrt_zu"), str))
check("Index stimmt bei gerichteten Kanten", index_stimmt(db))
check("Auf der Platte steht bei gerichteten Kanten kein 'gerichtet'",
      all("gerichtet" not in k for k in platte(db.root, "edges")["fuehrt_zu"].values()))

# --- Ungerichtet: von beiden Enden --------------------------------------
db = werk()
u = db.create_edge("orte/A", "orte/B", "grenzt_an", gerichtet=False)
check("Ungerichtete Kante trägt gerichtet=False",
      db.get_edge(u).get("gerichtet") is False)
for richtung in ("out", "in", "both"):
    check(f"Ungerichtet: A findet B mit direction={richtung!r}",
          db.get_connected("orte/A", richtung) == ["orte/B"])
    check(f"Ungerichtet: B findet A mit direction={richtung!r}",
          db.get_connected("orte/B", richtung) == ["orte/A"])
check("Ungerichtet: get_connected_edges am zweiten Ende liefert die Kante",
      [i for i, _ in db.get_connected_edges("orte/B", "out")] == [u])
check("Ungerichtet: get_connected_edges 'both' liefert sie nur einmal",
      [i for i, _ in db.get_connected_edges("orte/A", "both")] == [u])
check("Ungerichtet: traverse von B findet A",
      db.traverse("orte/B", direction="out") == ["orte/A"])
db.create_edge("orte/B", "orte/C", "grenzt_an", gerichtet=False)
db.create_edge("orte/D", "orte/C", "grenzt_an", gerichtet=False)
check("Ungerichtet: traverse läuft über mehrere Stufen in beide Richtungen",
      sorted(db.traverse("orte/A", rel_type="grenzt_an", direction="out"))
      == ["orte/B", "orte/C", "orte/D"])
check("Ungerichtet: traverse mit max_depth=1 bleibt bei den Nachbarn",
      sorted(db.traverse("orte/C", direction="both", max_depth=1))
      == ["orte/B", "orte/D"])
check("Ungerichtet: traverse mit kantenfilter geht auch von der anderen Seite",
      sorted(db.traverse("orte/B", direction="out",
                         kantenfilter=lambda k: k.get("gerichtet") is False,
                         max_depth=1)) == ["orte/A", "orte/C"])
check("Ungerichtet: collect_related von beiden Enden",
      db.collect_related("orte/B", ["grenzt_an"]) == ["orte/A", "orte/C"])
check("Ungerichtet: verwendungen('both') am zweiten Ende",
      db.verwendungen("orte/B", "both")["grenzt_an"][0][1] in ("orte/A", "orte/C")
      and len(db.verwendungen("orte/B", "both")["grenzt_an"]) == 2)
check("Ungerichtet: verwendungen('both') zählt eine Kante einmal",
      len(db.verwendungen("orte/A", "both")["grenzt_an"]) == 1)
check("Index stimmt mit ungerichteten Kanten", index_stimmt(db))

# --- Doppelschutz -------------------------------------------------------
db = werk()
db.create_edge("orte/A", "orte/B", "grenzt_an", gerichtet=False)
check("Doppelschutz: dieselbe Kante nochmal wird abgewiesen",
      wirft(fg.KanteExistiert, db.create_edge, "orte/A", "orte/B",
            "grenzt_an", gerichtet=False) is True)
check("Doppelschutz: B–A ist dieselbe Kante wie A–B",
      wirft(fg.KanteExistiert, db.create_edge, "orte/B", "orte/A",
            "grenzt_an", gerichtet=False) is True)
check("Doppelschutz: die Ausnahme ist auch ein ValueError (wie create_edge sonst)",
      wirft(ValueError, db.create_edge, "orte/B", "orte/A",
            "grenzt_an", gerichtet=False) is True)
check("Doppelschutz: nach dem Abweisen gibt es genau eine Kante",
      len(db.list_edges("grenzt_an")) == 1)
check("Doppelschutz: eine andere Kantenart zwischen denselben Knoten geht",
      isinstance(db.create_edge("orte/A", "orte/B", "nachbar", gerichtet=False), str))
check("Doppelschutz: eine gerichtete Kante derselben Art daneben geht",
      isinstance(db.create_edge("orte/A", "orte/B", "grenzt_an"), str))
check("Doppelschutz: A–A (Schleife) einmal ja, zweimal nein",
      isinstance(db.create_edge("orte/C", "orte/C", "grenzt_an", gerichtet=False), str)
      and wirft(fg.KanteExistiert, db.create_edge, "orte/C", "orte/C",
                "grenzt_an", gerichtet=False) is True)
check("Schleife: der Nachbar von C ist C, einmal",
      db.get_connected("orte/C", "both") == ["orte/C"])
check("Doppelschutz gilt auch in einer Transaktion",
      wirft(fg.KanteExistiert, lambda: _in_tx(db)) is True)


check("Ein Rollback nimmt die erste der beiden Kanten wieder mit",
      db.list_edges("x") == {} and index_stimmt(db))

# --- Löschen ------------------------------------------------------------
db = werk()
u = db.create_edge("orte/A", "orte/B", "grenzt_an", gerichtet=False)
db.delete_edge(u)
check("delete_edge: von keinem Ende mehr zu finden",
      db.get_connected("orte/A", "both") == []
      and db.get_connected("orte/B", "both") == [])
check("delete_edge: der Index ist leer", index_stimmt(db)
      and not db._out_index and not db._in_index,
      f"{db._out_index} {db._in_index}")

db = werk()
u = db.create_edge("orte/A", "orte/B", "grenzt_an", gerichtet=False)
db.soft_delete("orte", "B")
check("Papierkorb: das gelöschte Ende taucht bei A nicht mehr auf",
      db.get_connected("orte/A", "both") == [])
check("Papierkorb: die Kante bleibt bis zum Müllsammler",
      db.get_edge(u) is not None)
db.restore_node("orte", "B")
check("Papierkorb: nach restore_node wieder von beiden Enden zu finden",
      db.get_connected("orte/A", "out") == ["orte/B"]
      and db.get_connected("orte/B", "out") == ["orte/A"])
db.soft_delete("orte", "A")
db.run_garbage_collection()
check("Müllsammler: entfernt die ungerichtete Kante an einem gelöschten Knoten",
      db.get_edge(u) is None and db.list_edges("grenzt_an") == {})
check("Müllsammler: der Index ist danach sauber",
      index_stimmt(db) and db.get_connected("orte/B", "both") == [],
      f"{db._out_index} {db._in_index}")

db = werk()
u = db.create_edge("orte/A", "orte/B", "grenzt_an", gerichtet=False)
c = db.create_edge("orte/B", "orte/C", "gehoert", cascade_delete=True)
folgen = db.loeschfolgen("orte/B")
check("loeschfolgen: nennt die ungerichtete Kante am Knoten",
      u in folgen["kanten"] and c in folgen["kanten"], str(folgen))
check("loeschfolgen: nennt sie nur einmal",
      len(folgen["kanten"]) == len(set(folgen["kanten"])))
db.soft_delete("orte", "B")
check("Kaskade über eine gerichtete Kante geht wie vorher",
      db.get_node("orte/C") is None)
check("Kaskadenlöschen zusammen mit gerichtet=False wird abgewiesen",
      wirft(ValueError, db.create_edge, "orte/A", "orte/D", "gehoert",
            cascade_delete=True, gerichtet=False) is True)

# --- Dauerhaft ----------------------------------------------------------
db = werk()
u = db.create_edge("orte/A", "orte/B", "grenzt_an", gerichtet=False)
g = db.create_edge("orte/C", "orte/D", "grenzt_an")
db = neu_oeffnen(db)
check("Nach dem Neustart: ungerichtet von beiden Enden",
      db.get_connected("orte/A", "out") == ["orte/B"]
      and db.get_connected("orte/B", "out") == ["orte/A"])
check("Nach dem Neustart: gerichtet weiterhin nur in eine Richtung",
      db.get_connected("orte/C", "out") == ["orte/D"]
      and db.get_connected("orte/D", "out") == [])
check("Nach dem Neustart: Index stimmt", index_stimmt(db))
auf_platte = platte(db.root, "edges")["grenzt_an"]
check("Auf der Platte: nur die ungerichtete Kante trägt das Feld",
      auf_platte[u].get("gerichtet") is False and "gerichtet" not in auf_platte[g])
check("Nach dem Neustart gilt der Doppelschutz weiter",
      wirft(fg.KanteExistiert, db.create_edge, "orte/B", "orte/A",
            "grenzt_an", gerichtet=False) is True)
db.close()

# --- Regeln, Meta, Fehler -----------------------------------------------
db = werk(edge_constraints={"fuehrt": [("orte", "orte")], "von_nach": [("orte", "dinge")]})
db.create_node("dinge", "T1", {})
check("Kantenregel: gerichtet in der falschen Reihenfolge wird abgewiesen",
      wirft(ValueError, db.create_edge, "dinge/T1", "orte/A", "von_nach") is True)
check("Kantenregel: ungerichtet gilt in beiden Reihenfolgen",
      isinstance(db.create_edge("dinge/T1", "orte/A", "von_nach", gerichtet=False), str))
check("Kantenregel: ungerichtet lässt trotzdem nichts Fremdes durch",
      wirft(ValueError, db.create_edge, "orte/A", "orte/B", "von_nach",
            gerichtet=False) is True)
e = db.create_edge("orte/A", "orte/B", "fuehrt", meta={"gerichtet": False})
check("meta kann die Richtung nicht ändern",
      "gerichtet" not in db.get_edge(e)
      and db.get_connected("orte/B", "out") == [])
for f, name in ((db.get_connected, "get_connected"),
                (db.get_connected_edges, "get_connected_edges"),
                (db.traverse, "traverse"),
                (lambda r, direction: db.verwendungen(r, direction), "verwendungen")):
    check(f"{name}: eine unbekannte Richtung wird abgewiesen",
          wirft(ValueError, f, "orte/A", direction="quer") is True)

# --- Gerichtet und ungerichtet nebeneinander, 'both' -------------------
db = werk()
db.create_edge("orte/A", "orte/B", "x")                       # A -> B
db.create_edge("orte/C", "orte/A", "x")                       # C -> A
db.create_edge("orte/A", "orte/D", "x", gerichtet=False)      # A – D
check("both: gerichtet aus + gerichtet ein + ungerichtet",
      sorted(db.get_connected("orte/A", "both")) == ["orte/B", "orte/C", "orte/D"])
check("out: gerichtet aus + ungerichtet, nicht das Eingehende",
      sorted(db.get_connected("orte/A", "out")) == ["orte/B", "orte/D"])
check("in: gerichtet ein + ungerichtet, nicht das Ausgehende",
      sorted(db.get_connected("orte/A", "in")) == ["orte/C", "orte/D"])
check("rel_type-Filter greift auch bei 'both'",
      db.get_connected("orte/A", "both", rel_type="y") == [])

# --- Interning ----------------------------------------------------------
db = werk()
ids = [db.create_edge("orte/A", f"orte/{k}", "grenzt_an") for k in ("B", "C", "D")]
kanten = [db._cache["edges"]["grenzt_an"][i] for i in ids]
check("Interning: dieselbe Quelle ist im Speicher ein Objekt",
      kanten[0]["source"] is kanten[1]["source"] is kanten[2]["source"])
check("Interning: der Schlüssel im Index ist dasselbe Objekt wie in der Kante",
      next(k for k in db._out_index if k == "orte/A") is kanten[0]["source"])
db = neu_oeffnen(db)
kanten = list(db._cache["edges"]["grenzt_an"].values())
check("Interning: auch nach dem Einlesen ist die Quelle ein Objekt",
      len(kanten) == 3 and kanten[0]["source"] is kanten[1]["source"]
      is kanten[2]["source"])
check("Interning: die Kantenart ist ein Objekt",
      kanten[0]["type"] is kanten[1]["type"])
check("Interning ändert keine Werte", sorted(k["target"] for k in kanten)
      == ["orte/B", "orte/C", "orte/D"])
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)

print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
