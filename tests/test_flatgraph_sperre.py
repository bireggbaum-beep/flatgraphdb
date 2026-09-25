"""
Ein Bestand, eine offene Instanz.

Seit 3.0.0-entwurf wird ein Bestand beim Öffnen gesperrt. Jede weitere
Instanz — aus einem anderen Prozess oder aus diesem — bekommt
`BestandBelegt`, bis die erste mit `close()` geschlossen oder weggeräumt
ist. Entschieden am 24.09.2026.

Warum: zwei Instanzen halten je einen eigenen Stand im Speicher, und was
die eine schreibt, sieht die andere nie. Bis 3.0.0 flickte das
Kantenschreiben das halb, indem es vor jedem Schreiben die Kantendatei
nachlas; `file_lock=True` schützte nur den einzelnen Schreibvorgang.
Nachgewiesen: eine App-Instanz plus eine zweite für den Müllsammler,
danach schrieb die App eine Kante — und nach dem Neustart zeigte eine
Kante auf einen gelöschten Knoten. Der Müllsammler ist deshalb eine
Methode jeder Instanz, und das Nachlesen ist weg.

Gegen 3.0.0 fällt diese Suite — das ist ihre Gegenprobe:

    python tests/test_flatgraph_sperre.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_sperre.py

Importiert `pdms` nicht.
"""
import gc
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import warnings

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                       # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def fehler(fn):
    try:
        fn()
    except Exception as e:
        return e
    return None


def ist(e, name):
    return type(e).__name__ == name


WURZEL = tempfile.mkdtemp(prefix="fg_sperre_")


def ort():
    return tempfile.mkdtemp(dir=WURZEL)


def schliessen(db):
    getattr(db, "close", lambda: None)()


def fremder_prozess(pfad, danach=""):
    """Öffnet den Bestand in einem EIGENEN Prozess und meldet, was geschah.

    Dieselbe Fassung wie diese Suite, über dieselbe Ladestelle."""
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
        from flatgraph_laden import lade
        fg = lade()
        try:
            db = fg.FlatGraphDB({pfad!r})
        except Exception as e:
            print(type(e).__name__)
        else:
            print("geoeffnet")
            {danach}
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return r.stdout.strip() or r.stderr.strip()[-200:]


# =========================================================================
print("--- Zwischen Prozessen ---")
# =========================================================================
p = ort()
db = fg.FlatGraphDB(p)
check("Ein zweiter Prozess bekommt BestandBelegt, solange eine Instanz offen ist",
      fremder_prozess(p) == "BestandBelegt", fremder_prozess(p))
schliessen(db)
check("Nach close() kann ein anderer Prozess öffnen",
      fremder_prozess(p) == "geoeffnet", fremder_prozess(p))

db = fg.FlatGraphDB(p)
del db
gc.collect()
check("Auch ohne close(): eine weggeräumte Instanz gibt die Sperre frei",
      fremder_prozess(p) == "geoeffnet", fremder_prozess(p))

# Ein Prozess, der mit offener Instanz endet — ohne close(), ohne
# Aufräumen: die Sperre überlebt ihn nicht.
check("Ein beendeter Prozess hinterlässt keine Sperre",
      fremder_prozess(p, danach="os._exit(0)") == "geoeffnet"
      and fremder_prozess(p) == "geoeffnet")

with fg.FlatGraphDB(p) if hasattr(fg.FlatGraphDB, "__enter__") else open(os.devnull) as _:
    pass
check("Ein with-Block gibt die Sperre am Ende frei",
      fremder_prozess(p) == "geoeffnet", fremder_prozess(p))

# =========================================================================
print("--- Im selben Prozess ---")
# =========================================================================
p = ort()
db = fg.FlatGraphDB(p)
e = fehler(lambda: fg.FlatGraphDB(p))
check("Eine zweite Instanz im selben Prozess bekommt BestandBelegt",
      ist(e, "BestandBelegt"), repr(e))
check("… mit dem Hinweis, dass es dieser Prozess ist",
      getattr(e, "im_selben_prozess", None) is True)
check("BestandBelegt ist ein FlatGraphFehler und ein RuntimeError",
      isinstance(e, RuntimeError) and isinstance(e, getattr(fg, "FlatGraphFehler", ())))
e = fehler(lambda: fg.MaintenanceEngine(p))
check("Auch MaintenanceEngine neben einer offenen Instanz",
      ist(e, "BestandBelegt"), repr(e))
check("Auch über einen anderen Pfad zum selben Verzeichnis",
      ist(fehler(lambda: fg.FlatGraphDB(os.path.join(p, "datenbank", ".."))),
          "BestandBelegt"))
schliessen(db)
check("Nach close() öffnet eine neue Instanz",
      fehler(lambda: schliessen(fg.FlatGraphDB(p))) is None)
check("Zweimal close() schadet nicht",
      fehler(lambda: (schliessen(db), schliessen(db))) is None)

# Ein Öffnen, das scheitert, darf die Sperre nicht festhalten — es gibt
# keine Instanz, die man schliessen könnte.
p = ort()
schliessen(fg.FlatGraphDB(p))
with open(os.path.join(p, "datenbank", "_meta.json"), "w", encoding="utf-8") as f:
    json.dump({"speicherform": fg.SPEICHERFORM + 1}, f)
# Die Ausnahme FESTHALTEN, wie ein Aufrufer, der sie protokolliert: ihr
# Traceback hält die halb geöffnete Instanz am Leben. Würde sie weggeworfen,
# räumte Python die Instanz sofort weg und gäbe die Sperre auch ohne
# eigene Freigabe frei — die Prüfung könnte dann nicht fallen.
festgehalten = fehler(lambda: fg.FlatGraphDB(p))
with open(os.path.join(p, "datenbank", "_meta.json"), "w", encoding="utf-8") as f:
    json.dump({"speicherform": fg.SPEICHERFORM}, f)
check("Ein gescheitertes Öffnen gibt die Sperre sofort wieder frei",
      ist(festgehalten, "SpeicherformZuNeu")
      and fehler(lambda: schliessen(fg.FlatGraphDB(p))) is None,
      repr(fehler(lambda: schliessen(fg.FlatGraphDB(p)))))
del festgehalten

# =========================================================================
print("--- Nach close() wird nicht mehr geschrieben ---")
# =========================================================================
p = ort()
db = fg.FlatGraphDB(p)
db.create_node("dinge", "a", {"v": 1})
db.create_node("dinge", "b", {"v": 2})
kante = db.create_edge("dinge/a", "dinge/b", "r")
db.soft_delete("dinge", "b")
schliessen(db)
for name, aufruf in [
    ("create_node", lambda: db.create_node("dinge", "c", {})),
    ("update_node", lambda: db.update_node("dinge", "a", {"v": 9})),
    ("restore_node", lambda: db.restore_node("dinge", "b")),
    ("create_edge", lambda: db.create_edge("dinge/a", "dinge/a", "r")),
    ("delete_edge", lambda: db.delete_edge(kante)),
]:
    check(f"{name} nach close() wirft BestandGeschlossen",
          ist(fehler(aufruf), "BestandGeschlossen"))
check("… und hat den Speicher nicht verändert",
      db.get_node("dinge/c") is None and db.get_node("dinge/a") == {"v": 1}
      and db.get_node("dinge/b") is None and db.get_edge(kante) is not None)
neu = fg.FlatGraphDB(p)
check("… und die Platte auch nicht",
      neu.get_node("dinge/c") is None and neu.get_node("dinge/a") == {"v": 1}
      and neu.get_edge(kante) is not None)
schliessen(neu)

# =========================================================================
print("--- Der Müllsammler gehört zur Instanz ---")
# =========================================================================
p = ort()
db = fg.FlatGraphDB(p)
check("FlatGraphDB hat run_garbage_collection",
      callable(getattr(db, "run_garbage_collection", None)))
for k in "abc":
    db.create_node("n", k, {})
db.create_edge("n/a", "n/b", "r")
db.soft_delete("n", "b")
if callable(getattr(db, "run_garbage_collection", None)):
    db.run_garbage_collection()
db.create_edge("n/a", "n/c", "r")
schliessen(db)
neu = fg.FlatGraphDB(p)
check("Nach Aufräumen und weiterem Schreiben zeigt keine Kante ins Leere",
      neu.get_connected("n/a", rel_type="r", include_deleted=True) == ["n/c"],
      str(neu.get_connected("n/a", rel_type="r", include_deleted=True)))
schliessen(neu)

# =========================================================================
print("--- file_lock ---")
# =========================================================================
p = ort()
with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    db = fg.FlatGraphDB(p, file_lock=True)
check("file_lock=True startet weiter, mit DeprecationWarning",
      any(issubclass(x.category, DeprecationWarning) for x in w))
check("… und schützt trotzdem gegen einen zweiten Prozess",
      fremder_prozess(p) == "BestandBelegt")
schliessen(db)

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
