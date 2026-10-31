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


# ---------------------------------------------------------------- 7
UMBUCHER = r"""
import sys, importlib.util
spec = importlib.util.spec_from_file_location("fgx", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["fgx"] = m
spec.loader.exec_module(m)
db = m.FlatGraphDB(sys.argv[2])
n = 0
while True:
    with db.transaction():      # zwei Sammlungen = zwei Fächer
        db.update_node("a", "x", {"v": db.get_node("a/x")["v"] - 1})
        db.update_node("b", "y", {"v": db.get_node("b/y")["v"] + 1})
    n += 1
    print(n, flush=True)
"""


def kill_in_transaktion(seed):
    """Umbuchung zwischen zwei Fächern, Prozess abgeschossen: die Summe darf
    nie von 1000 abweichen, und nichts Bestätigtes darf fehlen."""
    wurzel, rnd = frisch(), random.Random(seed)
    db = fg.FlatGraphDB(wurzel)
    db.create_node("a", "x", {"v": 1000})
    db.create_node("b", "y", {"v": 0})
    db.close()
    p = subprocess.Popen([sys.executable, "-c", UMBUCHER, fg.__pfad__, wurzel],
                         stdout=subprocess.PIPE, text=True)
    time.sleep(0.3 + rnd.random() * 0.5)
    p.send_signal(signal.SIGKILL)
    bestaetigt = len(p.stdout.read().split())
    p.wait()
    try:
        db = fg.FlatGraphDB(wurzel)
        x = db.get_node("a/x")["v"]
        y = db.get_node("b/y")["v"]
        db.close()
    except Exception as e:
        shutil.rmtree(wurzel)
        return False, False, f"{type(e).__name__}: {e}"
    shutil.rmtree(wurzel)
    summe_ok = x + y == 1000
    # die letzte, noch nicht bestätigte Umbuchung darf gelten oder nicht
    nichts_verloren = y in (bestaetigt, bestaetigt + 1)
    return summe_ok, nichts_verloren, f"{bestaetigt} bestätigt, x={x} y={y}"


# ---------------------------------------------------------------- 8
def _stand(db):
    return (repr(sorted((k, sorted(v.items())) for k, v in db.list_nodes("a", include_deleted=True).items())),
            repr(sorted((v["source"], v["target"], v["type"]) for v in db.list_edges("t").values())))


def _vorbereiten(wurzel):
    db = fg.FlatGraphDB(wurzel)
    db.create_node("a", "1", {"v": 1})
    db.create_node("a", "2", {"v": 2})
    db.create_node("a", "3", {"v": 3})
    # zwei Kanten: wird die einzige gelöscht, wird das Fach entfernt statt
    # geschrieben, und es gäbe keinen fsync, der scheitern könnte
    db.create_edge("a/1", "a/2", "t")
    db.create_edge("a/1", "a/3", "t")
    kante = next(iter(db.list_edges("t")))
    db.soft_delete("a", "3")
    return db, kante


SCHREIBWEGE = {
    "create_node": lambda db, k: db.create_node("a", "neu", {"v": 9}),
    "update_node": lambda db, k: db.update_node("a", "1", {"v": 99}),
    "create_edge": lambda db, k: db.create_edge("a/2", "a/1", "t"),
    "delete_edge": lambda db, k: db.delete_edge(k),
    "soft_delete": lambda db, k: db.soft_delete("a", "2"),
    "restore_node": lambda db, k: db.restore_node("a", "3"),
}


def platte_voll_weg(name):
    """Speicher und Platte müssen nach dem gescheiterten Schreiben gleich
    sein — gleich dem alten Stand, den wir vorher gesehen haben."""
    wurzel = frisch()
    db, kante = _vorbereiten(wurzel)
    alt = _stand(db)
    echt = os.fsync

    def voll(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    os.fsync = voll
    try:
        try:
            SCHREIBWEGE[name](db, kante)
            geworfen = False
        except OSError:
            geworfen = True
    finally:
        os.fsync = echt
    speicher = _stand(db)
    db = neu_oeffnen(db)
    platte = _stand(db)
    db.close()
    shutil.rmtree(wurzel)
    return geworfen, speicher == alt, platte == alt


# ---------------------------------------------------------------- 9
def threads_last(n_threads=4, je=100):
    import threading
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    fehler = []

    def arbeit(t):
        try:
            for i in range(je):
                db.create_node("a", f"{t}-{i}", {"v": i})
        except Exception as e:
            fehler.append(e)

    ts = [threading.Thread(target=arbeit, args=(t,)) for t in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    db = neu_oeffnen(db)
    anzahl = len(db.list_nodes("a"))
    db.close()
    shutil.rmtree(wurzel)
    return anzahl, fehler


# ---------------------------------------------------------------- 10
def bitflips(art, n=300, seed=1):
    """Ein Bit in einem Fach kippen, öffnen, alles lesen. Erlaubt sind drei
    Ausgänge: gleicher Stand (Kippen in Leerraum), `DateiKaputt`, oder ein
    stilles anderes Ergebnis — Letzteres lässt sich ohne Prüfsumme nicht
    erkennen und wird nur GEZÄHLT. Jeder andere Fehlertyp ist ein Fehler."""
    rnd, zaehler, andere = random.Random(seed), {"gleich": 0, "kaputt": 0, "still": 0}, {}
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    for i in range(8):
        db.create_node("a", str(i), {"name": f"Knoten {i}", "v": i * 7, "liste": [1, 2, 3], "t": "äöü"})
    for i in range(7):
        db.create_edge(f"a/{i}", f"a/{i + 1}", "t", meta={"g": i})
    db.close()
    pfad = os.path.join(wurzel, "datenbank", *(("nodes", "a") if art == "nodes" else ("edges", "t")),
                        "fach_000001.json")
    roh = open(pfad, "rb").read()

    def lies(db):
        return repr(sorted(db.list_nodes("a").items())) + repr(sorted(db.list_edges("t").items()))

    db = fg.FlatGraphDB(wurzel)
    soll = lies(db)
    db.close()
    for _ in range(n):
        b = bytearray(roh)
        b[rnd.randrange(len(b))] ^= 1 << rnd.randrange(8)
        open(pfad, "wb").write(bytes(b))
        db = None
        try:
            db = fg.FlatGraphDB(wurzel)
            zaehler["gleich" if lies(db) == soll else "still"] += 1
        except fg.DateiKaputt:
            zaehler["kaputt"] += 1
        except Exception as e:
            andere[type(e).__name__] = andere.get(type(e).__name__, 0) + 1
        finally:
            if db is not None:
                db.close()
    shutil.rmtree(wurzel)
    return zaehler, andere


# ---------------------------------------------------------------- 11
GROSS_N = 20000      # klein genug für die Suite; 100 000 stehen im Vertrag


def grosser_bestand(n=GROSS_N):
    wurzel = frisch()
    db = fg.FlatGraphDB(wurzel)
    with db.transaction():
        for i in range(n):
            db.create_node("a", str(i), {"v": i, "n": f"Knoten {i}"})
        for i in range(2 * n):
            db.create_edge(f"a/{i % n}", f"a/{(i * 7 + 1) % n}", "t" if i < n else "u")
    t = time.time()
    db = neu_oeffnen(db)
    oeffnen = time.time() - t
    knoten = len(db.list_nodes("a", readonly=True))
    kanten = len(db.list_edges("t")) + len(db.list_edges("u"))
    # Kante i geht von i % n nach (7i+1) % n. Knoten 5 hat Kante 5 (Art t)
    # und Kante n+5 (Art u); beide treffen denselben Knoten, daher zweimal
    ziel = f"a/{(5 * 7 + 1) % n}"
    stichprobe = (db.get_node(f"a/{n - 1}")["v"] == n - 1
                  and db.get_connected("a/5") == [ziel, ziel])
    db.close()
    shutil.rmtree(wurzel)
    return knoten, kanten, stichprobe, oeffnen


GROSSER_SCHREIBER = r"""
import sys, importlib.util
spec = importlib.util.spec_from_file_location("fgx", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["fgx"] = m
spec.loader.exec_module(m)
db = m.FlatGraphDB(sys.argv[2])
with db.transaction():
    for i in range(int(sys.argv[3])):
        db.create_node("a", str(i), {"v": i, "n": "Knoten " + str(i)})
print("fertig", flush=True)
"""


def kill_grosse_transaktion(seed, anteile, dauer):
    """Eine Transaktion über viele Fächer, irgendwo abgeschossen: danach
    entweder keiner oder alle Knoten, nie ein Teil."""
    wurzel, rnd = frisch(), random.Random(seed)
    p = subprocess.Popen([sys.executable, "-c", GROSSER_SCHREIBER, fg.__pfad__, wurzel, str(GROSS_N)],
                         stdout=subprocess.PIPE, text=True)
    time.sleep(dauer * (anteile[0] + rnd.random() * (anteile[1] - anteile[0])))
    p.send_signal(signal.SIGKILL)
    p.wait()
    try:
        db = fg.FlatGraphDB(wurzel)
        anzahl = len(db.list_nodes("a", readonly=True)) if "a" in db.list_collections() else 0
        db.close()
    except Exception as e:
        shutil.rmtree(wurzel)
        return None, f"{type(e).__name__}: {e}"
    shutil.rmtree(wurzel)
    return anzahl, ""


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

    for seed in range(6):
        summe, verl, d = kill_in_transaktion(seed)
        check(f"Kill in Transaktion (Seed {seed}): Summe bleibt 1000 (alles oder nichts)", summe, d)
        check(f"Kill in Transaktion (Seed {seed}): keine bestätigte Umbuchung geht verloren", verl, d)

    for name in SCHREIBWEGE:
        geworfen, mem, pl = platte_voll_weg(name)
        check(f"Platte voll bei {name}: Fehler kommt an", geworfen)
        check(f"Platte voll bei {name}: Speicher bleibt beim alten Stand", mem)
        check(f"Platte voll bei {name}: Platte bleibt beim alten Stand", pl)

    anzahl, fehler = threads_last()
    check("Threads: 4 x 100 gleichzeitige create_node, nichts geht verloren", anzahl == 400 and not fehler,
          f"{anzahl}/400, Fehler: {fehler[:1]}")

    for art in ("nodes", "edges"):
        z, andere = bitflips(art)
        check(f"Bitflip im {'Knoten' if art == 'nodes' else 'Kanten'}fach (300 Kippen): "
              "nie ein anderer Fehler als DateiKaputt", not andere,
              f"{z['kaputt']} DateiKaputt, {z['gleich']} gleich, {z['still']} still verändert"
              + (f", ANDERE: {andere}" if andere else ""))

    kn, ka, probe, oeffnen = grosser_bestand()
    check(f"Grosser Bestand: {GROSS_N} Knoten und {2 * GROSS_N} Kanten in einer Transaktion, "
          "nach Neustart vollständig", kn == GROSS_N and ka == 2 * GROSS_N, f"{kn} Knoten, {ka} Kanten")
    check("Grosser Bestand: Stichproben (letzter Knoten, Nachbar) stimmen", probe, f"Öffnen {oeffnen:.1f} s")

    t0 = time.time()
    wurzel = frisch()
    subprocess.run([sys.executable, "-c", GROSSER_SCHREIBER, fg.__pfad__, wurzel, str(GROSS_N)],
                   capture_output=True)
    dauer = time.time() - t0
    shutil.rmtree(wurzel)
    zustaende = []
    for seed in range(8):
        anzahl, d = kill_grosse_transaktion(seed, (0.2, 1.0), dauer)
        zustaende.append(anzahl)
        check(f"Kill in grosser Transaktion (Seed {seed}): alle oder keine {GROSS_N} Knoten",
              anzahl in (0, GROSS_N), d or f"{anzahl} Knoten")
    print(f"      (Zustände: {sum(1 for z in zustaende if z == 0)}x leer, "
          f"{sum(1 for z in zustaende if z == GROSS_N)}x ganz)")

    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\n{n_ok}/{len(results)} Checks bestanden")
    sys.exit(0 if n_ok == len(results) else 1)
