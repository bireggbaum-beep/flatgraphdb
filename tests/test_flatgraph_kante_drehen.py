"""
`reverse_edge`: eine gerichtete Kante umdrehen, ohne ihre Identität zu verlieren.

Wer löscht und neu anlegt, bekommt eine neue Kennung und ein neues
`created_at`. Geprüft wird, dass beides bleibt, dass Index, Platte und
Neustart die gedrehte Kante zeigen, und dass die Fälle abgewiesen werden,
in denen Drehen etwas Falsches täte.

    python tests/test_flatgraph_kante_drehen.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen                          # noqa: E402

fg = lade()
results = []
WURZEL = tempfile.mkdtemp(prefix="fg_drehen_")
_zaehler = [0]


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def wirft(fn):
    try:
        fn()
        return None
    except Exception as e:
        return type(e).__name__


def werk(**kw):
    _zaehler[0] += 1
    db = fg.FlatGraphDB(os.path.join(WURZEL, f"b{_zaehler[0]}"), **kw)
    db.create_node("equipment", "E1", {})
    db.create_node("raeume", "R1", {})
    return db


def index_stimmt(db):
    """Der Index gegen eine eigene Rechnung aus den Kanten (nur gerichtet)."""
    soll_out, soll_in = {}, {}
    for art, eimer in db._cache["edges"].items():
        for eid, e in eimer.items():
            soll_out.setdefault(e["source"], {}).setdefault(art, set()).add(eid)
            soll_in.setdefault(e["target"], {}).setdefault(art, set()).add(eid)
    ist_out = {r: {a: set(e) for a, e in arten.items()}
               for r, arten in db._out_index.items()}
    ist_in = {r: {a: set(e) for a, e in arten.items()}
              for r, arten in db._in_index.items()}
    return ist_out == soll_out and ist_in == soll_in


# --- Normalfall -----------------------------------------------------------
db = werk()
meldungen = []
db.bei_aenderung = meldungen.append
e = db.create_edge("raeume/R1", "equipment/E1", "steht_in", meta={"seit": "2026"})
vorher = db.get_edge(e)
meldungen.clear()
rueck = db.reverse_edge(e)
k = db.get_edge(e)
check("Gibt die Kennung zurück", rueck == e)
check("Quelle und Ziel sind getauscht",
      (k["source"], k["target"]) == ("equipment/E1", "raeume/R1"), str(k))
check("created_at bleibt", k["created_at"] == vorher["created_at"])
check("Meta und Kantenart bleiben",
      k["seit"] == "2026" and k["type"] == "steht_in")
check("Es gibt weiter genau eine Kante", list(db.list_edges("steht_in")) == [e])
check("Index: von der neuen Quelle aus ist das neue Ziel Nachbar",
      db.get_connected("equipment/E1", "out") == ["raeume/R1"]
      and db.get_connected("raeume/R1", "out") == [])
check("Index stimmt nach dem Drehen", index_stimmt(db))
check("Meldung nennt neue Quelle und neues Ziel",
      len(meldungen) == 1
      and meldungen[0]["ereignis"] == "reverse_edge"
      and meldungen[0]["quelle"] == "equipment/E1"
      and meldungen[0]["ziel"] == "raeume/R1", str(meldungen))
db.reverse_edge(e)
check("Zweimal drehen ergibt den Anfang",
      (db.get_edge(e)["source"], db.get_edge(e)["target"])
      == ("raeume/R1", "equipment/E1") and index_stimmt(db))

# --- Platte und Neustart ----------------------------------------------------
db.reverse_edge(e)
db = neu_oeffnen(db)
k = db.get_edge(e)
check("Nach dem Neustart steht die gedrehte Kante da",
      (k["source"], k["target"]) == ("equipment/E1", "raeume/R1")
      and k["created_at"] == vorher["created_at"])
check("Nach dem Neustart stimmt der Index", index_stimmt(db))

# --- Abweisungen -------------------------------------------------------------
db = werk()
check("Fehlende Kante wirft KanteFehlt",
      wirft(lambda: db.reverse_edge("link_gibtsnicht")) == "KanteFehlt")
c = db.create_edge("raeume/R1", "equipment/E1", "enthaelt", cascade_delete=True)
check("Kante mit Kaskadenlöschen wird abgewiesen",
      wirft(lambda: db.reverse_edge(c)) == "ValueError")
check("Die abgewiesene Kaskadenkante bleibt, wie sie war",
      db.get_edge(c)["source"] == "raeume/R1" and index_stimmt(db))

db = werk(edge_constraints={"steht_in": [("equipment", "raum")]})
db.create_node("raum", "R2", {})
ok = db.create_edge("equipment/E1", "raum/R2", "steht_in")
check("Ein Paar, das die Regel nicht erlaubt, wird abgewiesen",
      wirft(lambda: db.reverse_edge(ok)) == "ValueError")
check("Die abgewiesene Kante bleibt, wie sie war",
      db.get_edge(ok)["source"] == "equipment/E1" and index_stimmt(db))
db.edge_constraints["steht_in"] = [("equipment", "raum"), ("raum", "equipment")]
check("Erlaubt die Regel beide Richtungen, wird gedreht",
      wirft(lambda: db.reverse_edge(ok)) is None
      and db.get_edge(ok)["source"] == "raum/R2")

db = werk()
u = db.create_edge("raeume/R1", "equipment/E1", "grenzt", gerichtet=False)
check("Ungerichtete Kante wird abgewiesen",
      wirft(lambda: db.reverse_edge(u)) == "ValueError")

# --- Transaktion --------------------------------------------------------------
db = werk()
e = db.create_edge("raeume/R1", "equipment/E1", "steht_in")
meldungen = []
db.bei_aenderung = meldungen.append
try:
    with db.transaction():
        db.reverse_edge(e)
        raise RuntimeError("zurück")
except RuntimeError:
    pass
check("Rollback stellt die alte Richtung wieder her",
      db.get_edge(e)["source"] == "raeume/R1" and index_stimmt(db))
check("Nach einem Rollback wird nichts gemeldet", meldungen == [])
with db.transaction():
    db.reverse_edge(e)
check("Commit behält die gedrehte Kante",
      db.get_edge(e)["source"] == "equipment/E1" and index_stimmt(db))
db = neu_oeffnen(db)
check("Die in der Transaktion gedrehte Kante übersteht den Neustart",
      db.get_edge(e)["source"] == "equipment/E1")

shutil.rmtree(WURZEL, ignore_errors=True)
print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
