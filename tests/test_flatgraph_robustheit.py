"""
Robustheit: klein und breit. Hält der Bestand, was der Vertrag zusagt,
wenn Prozesse sterben, Dateien kaputt sind und die Platte nicht mitspielt?

Sechs Proben, jede mit festem Seed und wenigen Sekunden Laufzeit:

  1. Zufallsmodell   Zufallsoperationen gegen ein dict; nach Neustart gleich
  2. Kill-Schleife   Schreiber mit SIGKILL abschiessen; bestätigte Schreib-
                     vorgänge müssen da sein, der Bestand muss sich öffnen
  3. Beschädigung    abgeschnittene / vermüllte Fächer: Fehler, nie stilles
                     Weiterlesen von Unsinn
  4. Platte voll     fsync scheitert mit ENOSPC: Speicher und Platte bleiben
                     beim alten Stand
  5. Zweiter Prozess ein zweiter Prozess auf demselben Bestand wird abgewiesen
  6. Dauerlauf       viele Transaktionen: Dateizahl bleibt beschränkt, Stand
                     nach Neustart stimmt

Grenze: SIGKILL ist ein Prozess-, kein Stromausfall. Der Seitencache des
Kernels bleibt dabei erhalten; ob `fsync` wirklich wirkt, zeigt das nicht.

    python tests/test_flatgraph_robustheit.py
    FLATGRAPH_DATEI=kopie.py python tests/test_flatgraph_robustheit.py
"""
import errno
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen                          # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def frisch():
    return tempfile.mkdtemp(prefix="fg_robust_")


def dateien(wurzel):
    return sum(len(fs) for _, _, fs in os.walk(wurzel))


# ---------------------------------------------------------------- 1
def zufallsmodell(seed, schritte=400):
    wurzel, rnd = frisch(), random.Random(seed)
    db = fg.FlatGraphDB(wurzel)
    knoten, kanten = {}, set()
    for i in range(schritte):
        aktion = rnd.choice(["neu", "neu", "aendern", "kante", "rollback", "neustart"])
        if aktion == "neu":
            k = str(rnd.randrange(60))
            if k not in knoten:
                db.create_node("a", k, {"v": i})
                knoten[k] = {"v": i}
        elif aktion == "aendern" and knoten:
            k = rnd.choice(sorted(knoten))
            db.update_node("a", k, {"v": -i})
            knoten[k] = {"v": -i}
        elif aktion == "kante" and len(knoten) > 1:
            q, z = rnd.sample(sorted(knoten), 2)
            if (q, z) not in kanten:
                db.create_edge(f"a/{q}", f"a/{z}", "t")
                kanten.add((q, z))
        elif aktion == "rollback":
            try:
                with db.transaction():
                    db.create_node("a", "x" + str(i), {"v": 0})
                    raise ZeroDivisionError
            except ZeroDivisionError:
                pass
        elif aktion == "neustart":
            db = neu_oeffnen(db)
    db = neu_oeffnen(db)
    stand = {k: dict(v) for k, v in db.list_nodes("a").items()} if knoten else {}
    kantenstand = {(e["source"].split("/")[1], e["target"].split("/")[1])
                   for e in db.list_edges("t").values()} if kanten else set()
    db.close()
    shutil.rmtree(wurzel)
    return stand == knoten, kantenstand == kanten


# ---------------------------------------------------------------- 2
SCHREIBER = r"""
import sys, importlib.util
spec = importlib.util.spec_from_file_location("fgx", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["fgx"] = m
spec.loader.exec_module(m)
db = m.FlatGraphDB(sys.argv[2])
i = 0
while True:
    db.create_node("a", str(i), {"v": i})
    print(i, flush=True)      # erst NACH der Rückkehr: bestätigt
    i += 1
"""


def kill_lauf(seed):
    wurzel, rnd = frisch(), random.Random(seed)
    p = subprocess.Popen([sys.executable, "-c", SCHREIBER, fg.__pfad__, wurzel],
                         stdout=subprocess.PIPE, text=True)
    time.sleep(0.3 + rnd.random() * 0.5)
    p.send_signal(signal.SIGKILL)
    ausgabe = p.stdout.read().split()
    p.wait()
    bestaetigt = [int(x) for x in ausgabe]
    try:
        db = fg.FlatGraphDB(wurzel)
        fehlt = [i for i in bestaetigt if db.get_node(f"a/{i}", readonly=True)["v"] != i]
        db.close()
    except Exception as e:
        shutil.rmtree(wurzel)
        return False, f"{type(e).__name__}: {e}"
    shutil.rmtree(wurzel)
    return (not fehlt and len(bestaetigt) > 0), f"{len(bestaetigt)} bestätigt, {len(fehlt)} fehlen"


# ---------------------------------------------------------------- 3
def beschaedigt(wie):
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    for i in range(5):
        db.create_node("a", str(i), {"v": i})
    db.close()
    fach = os.path.join(wurzel, "datenbank", "nodes", "a", "fach_000001.json")
    roh = open(fach, "rb").read()
    neu = {"abgeschnitten": roh[: len(roh) // 2],
           "muell_am_ende": roh + b"\x00\xffMUELL",
           "leer": b""}[wie]
    open(fach, "wb").write(neu)
    try:
        db = fg.FlatGraphDB(wurzel)
        daten = [db.get_node(f"a/{i}", readonly=True)["v"] for i in range(5)]
        db.close()
        ergebnis = ("still", daten)
    except fg.DateiKaputt:
        ergebnis = ("fehler", None)
    except Exception as e:
        ergebnis = ("anderer", type(e).__name__)
    shutil.rmtree(wurzel)
    return ergebnis


# ---------------------------------------------------------------- 4
def platte_voll():
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    db.create_node("a", "alt", {"v": 1})
    echt = os.fsync

    def voll(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    os.fsync = voll
    try:
        try:
            db.create_node("a", "neu", {"v": 2})
            geworfen = False
        except OSError:
            geworfen = True
    finally:
        os.fsync = echt
    im_speicher = "neu" in [n for n in _kennungen(db)]
    db = neu_oeffnen(db)
    auf_platte = "neu" in _kennungen(db)
    alt_ok = db.get_node("a/alt")["v"] == 1
    db.close()
    shutil.rmtree(wurzel)
    return geworfen, im_speicher, auf_platte, alt_ok


def _kennungen(db):
    return [k for k in (db._cache["nodes"].get("a", {}))]


# ---------------------------------------------------------------- 5
ZWEITER = r"""
import sys, importlib.util
spec = importlib.util.spec_from_file_location("fgx", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["fgx"] = m
spec.loader.exec_module(m)
try:
    m.FlatGraphDB(sys.argv[2]); print("offen")
except m.BestandBelegt:
    print("belegt")
"""


def zweiter_prozess():
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    db.create_node("a", "1", {"v": 1})
    aus = subprocess.run([sys.executable, "-c", ZWEITER, fg.__pfad__, wurzel],
                         capture_output=True, text=True).stdout.strip()
    db.close()
    shutil.rmtree(wurzel)
    return aus


# ---------------------------------------------------------------- 6
def dauerlauf(n=1500):
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    for i in range(n):
        with db.transaction():
            db.create_node("a", str(i % 50), {"v": i}) if i < 50 else \
                db.update_node("a", str(i % 50), {"v": i})
        if i == 100:
            nach100 = dateien(wurzel)
    ende = dateien(wurzel)
    db = neu_oeffnen(db)
    stimmt = all(db.get_node(f"a/{k}")["v"] == max(j for j in range(n) if j % 50 == k)
                 for k in range(50))
    db.close()
    shutil.rmtree(wurzel)
    return nach100, ende, stimmt


if __name__ == "__main__":
    for seed in (1, 2, 3):
        k, e = zufallsmodell(seed)
        check(f"Zufallsmodell (Seed {seed}): Knoten nach Neustart wie im Modell", k)
        check(f"Zufallsmodell (Seed {seed}): Kanten nach Neustart wie im Modell", e)

    for seed in range(8):
        ok, d = kill_lauf(seed)
        check(f"Kill-Schleife (Seed {seed}): bestätigte Schreibvorgänge überleben SIGKILL", ok, d)

    for wie in ("abgeschnitten", "muell_am_ende", "leer"):
        art, _ = beschaedigt(wie)
        check(f"Beschädigung ({wie}): DateiKaputt statt stillem Weiterlesen", art == "fehler", art)

    geworfen, mem, platte, alt = platte_voll()
    check("Platte voll: der Fehler kommt beim Aufrufer an", geworfen)
    check("Platte voll: Speicher zeigt den gescheiterten Knoten nicht", not mem)
    check("Platte voll: nach Neustart fehlt der gescheiterte Knoten", not platte)
    check("Platte voll: der alte Stand ist unversehrt", alt)

    check("Zweiter Prozess auf demselben Bestand wird abgewiesen", zweiter_prozess() == "belegt")

    a, b, stimmt = dauerlauf()
    check("Dauerlauf: Dateizahl wächst bei Überschreiben nicht", b == a, f"{a} -> {b}")
    check("Dauerlauf: Stand nach 1500 Transaktionen und Neustart stimmt", stimmt)

    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\n{n_ok}/{len(results)} Checks bestanden")
    sys.exit(0 if n_ok == len(results) else 1)
