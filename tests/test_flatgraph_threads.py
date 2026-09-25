"""
Threadsicherheit: mehrere Threads auf einer Instanz, und nichts geht verloren.

Seit 3.0.0-entwurf läuft jede öffentliche Methode unter einer Sperre der
Instanz, und eine Transaktion hält sie über ihren ganzen Block. Bis 3.0.0
gab es keine. pDMS hat das am 09.09.2026 mit Datenverlust bezahlt: ein
Lasttest mit drei Threads ergab 1195 Fehler, darunter halb geschriebenes
JSON (Commit 31dc756). Die Reparatur lag danach im Anwender, nicht in der
Bibliothek.

Nebenläufigkeitsfehler zeigen sich zufällig. Diese Suite muss sie deshalb
VERLÄSSLICH hervorrufen, sonst kann ihre Gegenprobe nicht fallen:

  - Transaktion gegen Schreibvorgang wird über Events getaktet und ist
    deterministisch: ohne Sperre landet der fremde Schreibvorgang im Puffer
    der Transaktion und verschwindet mit ihrem Rollback — ohne Fehler.
  - Die Lastprobe setzt das Umschaltintervall der Threads auf eine
    Mikrosekunde, damit sie sich möglichst oft ins Wort fallen.

    python tests/test_flatgraph_threads.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_threads.py

Importiert `pdms` nicht.
"""
import os
import shutil
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen                          # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


WURZEL = tempfile.mkdtemp(prefix="fg_threads_")


class _Abbruch(Exception):
    pass


def neu():
    return fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))


def gleichzeitig(*ziele):
    """Startet jedes Ziel in einem Thread und sammelt, was sie werfen."""
    fehler = []

    def huelle(ziel):
        try:
            ziel()
        except Exception as e:                   # noqa: BLE001 — genau das zählen wir
            fehler.append(f"{type(e).__name__}: {str(e)[:80]}")

    threads = [threading.Thread(target=huelle, args=(z,)) for z in ziele]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return fehler


# =========================================================================
print("--- Eine Transaktion verschluckt keinen fremden Schreibvorgang ---")
# =========================================================================
db = neu()
db.create_node("dinge", "a", {"v": 1})
drin = threading.Event()
geschrieben = threading.Event()


def transaktion_mit_rollback():
    try:
        with db.transaction():
            db.update_node("dinge", "a", {"v": 2})
            drin.set()
            # Warten, bis der andere Thread geschrieben hat — oder, mit
            # Sperre, bis er es vergeblich versucht hat: dann wartet er auf
            # uns, und wir brechen nach einer halben Sekunde ab.
            geschrieben.wait(0.5)
            raise _Abbruch()
    except _Abbruch:
        pass


def fremder_schreibvorgang():
    drin.wait()
    db.create_node("dinge", "b", {"v": "von aussen"})
    geschrieben.set()


fehler = gleichzeitig(transaktion_mit_rollback, fremder_schreibvorgang)
check("Ein Schreibvorgang während einer fremden Transaktion bleibt erhalten",
      db.get_node("dinge/b") == {"v": "von aussen"}, f"b = {db.get_node('dinge/b')}")
check("… auch auf der Platte",
      neu_oeffnen(db).get_node("dinge/b") == {"v": "von aussen"})
check("… und der Rollback der Transaktion gilt trotzdem",
      fehler == [] and fg.FlatGraphDB is not None
      and neu_oeffnen(db).get_node("dinge/a") == {"v": 1}, str(fehler))

# =========================================================================
print("--- Last: vier Schreiber, ein Leser ---")
# =========================================================================
alt_intervall = sys.getswitchinterval()
sys.setswitchinterval(1e-6)
try:
    db = neu()
    for i in range(20):
        db.create_node("orte", f"o{i}", {"ort": "Keller"})
    SCHREIBER, JE = 4, 60
    fertig = threading.Event()

    def schreiber(nr):
        def lauf():
            for i in range(JE):
                nid = f"s{nr}_{i:03d}"
                db.create_node("dinge", nid, {"ort": "Halle", "n": 0})
                db.update_node("dinge", nid, {"n": i})
                db.create_edge(f"dinge/{nid}", f"orte/o{i % 20}", "steht_in")
        return lauf

    def leser():
        while not fertig.is_set():
            db.find_nodes("dinge", {"ort": "hall"})
            db.list_nodes("dinge")
            db.get_connected("orte/o0", "in")
            db.list_edges("steht_in")

    lese_fehler = []
    lese_thread = threading.Thread(
        target=lambda: lese_fehler.extend(gleichzeitig(leser)))
    lese_thread.start()
    fehler = gleichzeitig(*[schreiber(n) for n in range(SCHREIBER)])
    fertig.set()
    lese_thread.join()
finally:
    sys.setswitchinterval(alt_intervall)

check("Kein Schreib-Thread bekommt einen Fehler", fehler == [],
      f"{len(fehler)} Fehler, z. B. {fehler[:2]}")
check("Kein Lese-Thread bekommt einen Fehler", lese_fehler == [],
      f"{len(lese_fehler)} Fehler, z. B. {lese_fehler[:2]}")
soll = {f"s{nr}_{i:03d}": i for nr in range(SCHREIBER) for i in range(JE)}
ist = {k: v.get("n") for k, v in db.list_nodes("dinge").items()}
check("Jeder geschriebene Knoten ist da, mit seinem letzten Wert",
      ist == soll, f"{len(ist)} von {len(soll)}, "
      f"falsch: {[k for k in soll if ist.get(k) != soll[k]][:3]}")
check("Jede Kante ist da", len(db.list_edges("steht_in")) == SCHREIBER * JE,
      f"{len(db.list_edges('steht_in'))} von {SCHREIBER * JE}")
check("Die Suche stimmt mit dem Bestand überein",
      set(db.find_nodes("dinge", {"ort": "hall"})) == set(soll))
check("Die Nachbarschaft stimmt mit den Kanten überein",
      sorted(db.get_connected("orte/o0", "in"))
      == sorted(e["source"] for e in db.list_edges("steht_in").values()
                if e["target"] == "orte/o0"))
platte = neu_oeffnen(db)
check("Die Platte trägt denselben Stand",
      {k: v.get("n") for k, v in platte.list_nodes("dinge").items()} == soll
      and len(platte.list_edges("steht_in")) == SCHREIBER * JE)

# =========================================================================
print("--- next_id und create_node, unteilbar in einer Transaktion ---")
# =========================================================================
db = neu()
vergeben = []


def nummern_ziehen():
    for _ in range(25):
        with db.transaction():
            nid = db.next_id("belege", prefix="B-", padding=4)
            db.create_node("belege", nid, {})
            vergeben.append(nid)


alt_intervall = sys.getswitchinterval()
sys.setswitchinterval(1e-6)
try:
    fehler = gleichzeitig(*[nummern_ziehen for _ in range(4)])
finally:
    sys.setswitchinterval(alt_intervall)
check("Vier Threads ziehen je 25 Nummern ohne Fehler", fehler == [],
      f"{len(fehler)} Fehler, z. B. {fehler[:2]}")
check("… und keine Nummer ist doppelt",
      len(vergeben) == 100 and len(set(vergeben)) == 100,
      f"{len(vergeben)} vergeben, {len(set(vergeben))} verschieden")

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
