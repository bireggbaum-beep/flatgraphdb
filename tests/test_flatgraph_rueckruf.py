"""
Rückruf bei Änderungen statt Webhooks.

Bis 3.0.0 verschickte flatgraph selbst HTTP an konfigurierte URLs: je
Ereignis ein neuer Thread ohne Obergrenze, jeder Fehler still verschluckt,
und in einer Transaktion SOFORT — also auch für Änderungen, die ein
Rollback danach zurücknahm. Seit 4.0 verschickt flatgraph nichts, sondern
ruft `bei_aenderung(meldung)` auf; was damit geschieht, entscheidet der
Anwender.

Zugesagt und hier geprüft:
  - jede Änderung an Knoten und Kanten wird gemeldet, mit Ereignis und Ref
  - in einer Transaktion erst nach dem erfolgreichen Abschluss, bei einem
    Rollback gar nicht
  - ein Fehler im Rückruf macht die Änderung nicht rückgängig und wird
    protokolliert statt verschluckt
  - `webhooks=` wirft, statt still wirkungslos zu sein

Gegen 3.0.0 fällt diese Suite — das ist ihre Gegenprobe:

    python tests/test_flatgraph_rueckruf.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_rueckruf.py

Importiert `pdms` nicht.
"""
import logging
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


def fehler(fn):
    try:
        fn()
    except Exception as e:
        return e
    return None


class _Abbruch(Exception):
    pass


WURZEL = tempfile.mkdtemp(prefix="fg_rueckruf_")
gemeldet = []


def neu(**kw):
    gemeldet.clear()
    try:
        return fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), bei_aenderung=gemeldet.append, **kw)
    except TypeError:
        # Eine Fassung ohne bei_aenderung: ohne Rückruf öffnen, die
        # Prüfungen unten fallen dann — genau das ist die Gegenprobe.
        return fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), **kw)


def ereignisse():
    return [(m["ereignis"], m["ref"]) for m in gemeldet]


# =========================================================================
print("--- Jede Änderung wird gemeldet ---")
# =========================================================================
db = neu()
db.create_node("dinge", "a", {"v": 1})
db.create_node("dinge", "b", {"v": 2})
db.update_node("dinge", "a", {"v": 3})
kante = db.create_edge("dinge/a", "dinge/b", "teil")
db.delete_edge(kante)
db.soft_delete("dinge", "b")
db.restore_node("dinge", "b")
check("Anlegen, Ändern, Kanten, Löschen und Wiederherstellen werden gemeldet, in Reihenfolge",
      ereignisse() == [("create_node", "dinge/a"), ("create_node", "dinge/b"),
                       ("update_node", "dinge/a"), ("create_edge", kante),
                       ("delete_edge", kante), ("soft_delete", "dinge/b"),
                       ("restore_node", "dinge/b")], str(ereignisse()))
kanten_meldung = next((m for m in gemeldet if m["ereignis"] == "create_edge"), {})
check("Eine Kantenmeldung nennt Art, Quelle und Ziel",
      (kanten_meldung.get("kantenart"), kanten_meldung.get("quelle"), kanten_meldung.get("ziel"))
      == ("teil", "dinge/a", "dinge/b"), str(kanten_meldung))
check("Eine Knotenmeldung nennt die Sammlung und einen Zeitpunkt",
      gemeldet and gemeldet[0].get("sammlung") == "dinge" and bool(gemeldet[0].get("zeit")))

# =========================================================================
print("--- Transaktion ---")
# =========================================================================
db = neu()
im_block = []
with db.transaction():
    db.create_node("dinge", "a", {"v": 1})
    db.create_node("dinge", "b", {"v": 2})
    im_block.extend(gemeldet)
check("In einer Transaktion wird vor dem Abschluss nichts gemeldet", im_block == [], str(im_block))
check("… und nach dem Abschluss alles, in Reihenfolge",
      ereignisse() == [("create_node", "dinge/a"), ("create_node", "dinge/b")], str(ereignisse()))

gemeldet.clear()
try:
    with db.transaction():
        db.update_node("dinge", "a", {"v": 99})
        raise _Abbruch()
except _Abbruch:
    pass
check("Nach einem Rollback wird nichts gemeldet", gemeldet == [], str(ereignisse()))
# Verworfene Meldungen dürfen auch nicht SPÄTER auftauchen: ohne Leeren
# beim Rollback kämen sie mit dem nächsten erfolgreichen Abschluss.
with db.transaction():
    db.update_node("dinge", "b", {"v": 3})
check("… auch nicht mit dem nächsten erfolgreichen Abschluss",
      ereignisse() == [("update_node", "dinge/b")], str(ereignisse()))

gemeldet.clear()
try:
    with db.transaction():
        db.update_node("dinge", "a", {"v": 5})
        with db.transaction():
            db.update_node("dinge", "b", {"v": 6})
        raise _Abbruch()
except _Abbruch:
    pass
check("Auch eine abgeschlossene innere Transaktion meldet nichts, wenn die äussere zurückrollt",
      gemeldet == [], str(ereignisse()))

# =========================================================================
print("--- Fehler im Rückruf ---")
# =========================================================================
protokoll = []


class Sammler(logging.Handler):
    def emit(self, record):
        protokoll.append(record)


logging.getLogger("flatgraph").addHandler(Sammler())


def kaputt(meldung):
    raise RuntimeError("Empfänger kaputt")


try:
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), bei_aenderung=kaputt)
except TypeError:
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
e = fehler(lambda: db.create_node("dinge", "a", {"v": 1}))
check("Ein Fehler im Rückruf erreicht den Aufrufer nicht", e is None, repr(e))
check("… die Änderung steht trotzdem auf der Platte",
      neu_oeffnen(db).get_node("dinge/a") == {"v": 1})
check("… und der Fehler ist protokolliert, nicht verschluckt",
      any("bei_aenderung" in r.getMessage() for r in protokoll),
      str([r.getMessage() for r in protokoll]))

# Der Rückruf darf selbst lesen: die Sperre ist wiedereintrittsfähig.
gelesen = []
try:
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL),
                        bei_aenderung=lambda m: gelesen.append(db.get_node(m["ref"])))
except TypeError:
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
db.create_node("dinge", "x", {"v": 7})
check("Der Rückruf kann die Datenbank lesen, ohne zu blockieren", gelesen == [{"v": 7}], str(gelesen))

# =========================================================================
print("--- Webhooks ---")
# =========================================================================
e = fehler(lambda: fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL),
                                  webhooks=[{"url": "http://example.invalid/"}]))
check("webhooks= wirft, statt still wirkungslos zu sein",
      isinstance(e, TypeError) and "bei_aenderung" in str(e), repr(e))
quelle = open(fg.__pfad__, encoding="utf-8").read()
check("flatgraph verschickt kein HTTP mehr", "urllib.request" not in quelle)

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
