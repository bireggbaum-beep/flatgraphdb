"""
Ausgelagerte Langtexte (vault_text) sind so sicher wie der Knoten selbst.

Bis 3.0.0 hiess die Datei eines Langtexts nur nach Knoten und Feld und
wurde mit einem schlichten `open(..., "w")` an Ort und Stelle überschrieben,
an Transaktion und Absichtsdatei vorbei. Daraus drei Datenverluste:

  - Nach einem Rollback zeigte der Knoten wieder auf „seine“ Datei — die
    aber schon den neuen Text enthielt.
  - Ein Absturz nach dem Schreiben des Texts, vor dem des Knotens, liess den
    alten Knoten auf den neuen (oder halben) Text zeigen.
  - Jedes Sonderzeichen wurde zu „_“: `DOC.1` und `DOC_1` teilten sich eine
    Datei und überschrieben einander lautlos.

Dazu, aus #34: der Müllsammler zählte Knoten im Papierkorb nicht als
Verweis und löschte ihren Text; wer sie zurückholte, fand einen Pfad statt
des Texts.

Gegenprobe: gegen die Fassung davor fallen alle vier.

Importiert `pdms` nicht.
"""
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


class Absturz(BaseException):
    """Kein Exception: flatgraph soll ihn nicht abfangen können."""


WURZEL = tempfile.mkdtemp(prefix="fg_langtext_")
ALT, NEU = "A" * 500, "B" * 500


def frisch():
    return fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), longtext_threshold=100)


def text(db, ref):
    return (db.get_node_full(ref) or {}).get("t")


def dateien(db):
    vt = os.path.join(db.root, "vault_text")
    return sorted(os.listdir(vt)) if os.path.isdir(vt) else []


# =========================================================================
print("--- Rollback ---")
# =========================================================================
db = frisch()
db.create_node("x", "a", {"t": ALT})
try:
    with db.transaction():
        db.update_node("x", "a", {"t": NEU})
        raise RuntimeError
except RuntimeError:
    pass
check("Nach einem Rollback steht der alte Langtext da", text(db, "x/a") == ALT,
      str(text(db, "x/a"))[:10])
db = neu_oeffnen(db, longtext_threshold=100)
check("… auch nach dem Neustart", text(db, "x/a") == ALT, str(text(db, "x/a"))[:10])
db.close()

# =========================================================================
print("--- Absturz zwischen Text und Knoten ---")
# =========================================================================
db = frisch()
db.create_node("x", "a", {"t": ALT})
echt = os.replace
aufrufe = []


def stolpern(*a, **kw):
    # Der Langtext darf noch ankommen; beim Knoten stirbt der Prozess.
    aufrufe.append(a[1])
    if len(aufrufe) >= 2:
        raise Absturz()
    return echt(*a, **kw)


os.replace = stolpern
try:
    db.update_node("x", "a", {"t": NEU})
except Absturz:
    pass
finally:
    os.replace = echt
db = neu_oeffnen(db, longtext_threshold=100)
check("Stirbt der Prozess nach dem Text, vor dem Knoten: der alte Text gilt",
      text(db, "x/a") == ALT, str(text(db, "x/a"))[:10])
db.close()

# Ein Absturz MITTEN im Schreiben des Texts: die Arbeitsdatei bleibt liegen.
db = frisch()
db.create_node("x", "a", {"t": ALT})
os.replace = lambda *a, **kw: (_ for _ in ()).throw(Absturz())
try:
    db.update_node("x", "a", {"t": NEU})
except Absturz:
    pass
finally:
    os.replace = echt
db = neu_oeffnen(db, longtext_threshold=100)
check("Stirbt er mitten im Text: der alte Text gilt", text(db, "x/a") == ALT)
db.run_garbage_collection()
check("… und der Müllsammler räumt die halbe Arbeitsdatei weg",
      not any(d.endswith(".tmp") for d in dateien(db)), str(dateien(db)))
db.close()

# =========================================================================
print("--- Ähnliche Kennungen ---")
# =========================================================================
db = frisch()
db.create_node("x", "DOC.1", {"t": ALT})
db.create_node("x", "DOC_1", {"t": NEU})
check("DOC.1 und DOC_1 überschreiben einander nicht",
      text(db, "x/DOC.1") == ALT and text(db, "x/DOC_1") == NEU,
      f"{str(text(db, 'x/DOC.1'))[:5]} {str(text(db, 'x/DOC_1'))[:5]}")
db.close()

# =========================================================================
print("--- Müllsammler ---")
# =========================================================================
db = frisch()
db.create_node("x", "a", {"t": ALT})
db.update_node("x", "a", {"t": NEU})
db.run_garbage_collection()
check("Nach einer Änderung räumt der Müllsammler die alte Fassung weg",
      len(dateien(db)) == 1 and text(db, "x/a") == NEU, str(dateien(db)))
db.close()

# Ein Knoten, der WÄHREND des Laufs in den Papierkorb kommt und ihn
# überlebt: ein Kaskaden-Ziel einer alten Löschmarke (siehe #34).
db = frisch()
db.create_node("x", "a", {})
db.create_node("x", "b", {"t": ALT})
db.create_edge("x/a", "x/b", "teil", cascade_delete=True)
db.update_node("x", "a", {"_deletion_flag": "2026-09-01T00:00:00+00:00", "_keep_asset": True})
db.run_garbage_collection()
db.restore_node("x", "b")
check("Ein Knoten, der im Papierkorb einen Lauf überlebt, behält seinen Text",
      text(db, "x/b") == ALT, str(text(db, "x/b"))[:30])
db.soft_delete("x", "b")
db.run_garbage_collection()
check("… und mit ihm endgültig gelöscht geht auch der Text", dateien(db) == [],
      str(dateien(db)))
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
