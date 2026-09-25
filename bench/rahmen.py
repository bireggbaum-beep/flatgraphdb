"""
Messrahmen der flatgraph-Leistungsmessung — gemeinsam für alle Blöcke.

Ein Block (`block_*.py`) baut sich einen Bestand, ruft `messen()` für jede
Operation auf und gibt Zeilen zurück. Alles, was für jede Messung gleich
sein muss — Zeitnahme, Wiederholung, Umgebung, Ausgabe —, steht hier und
nur hier. Sonst misst jeder Block ein bisschen anders, und die Zahlen
zweier Blöcke lassen sich nicht mehr nebeneinanderlegen.

Welche Fassung gemessen wird, entscheidet `tests/flatgraph_laden.py`,
dieselbe Stelle wie für die Prüfsuiten: Vorgabe ist `flatgraph.py`, eine
andere Fassung über FLATGRAPH_DATEI. So lässt sich dieselbe Messung gegen
zwei Fassungen fahren.

Nur Standardbibliothek. flatgraph hat keine Fremdabhängigkeiten, und seine
Messung soll auch keine haben.
"""
import gc
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                       # noqa: E402

# Fester Samen: zwei Läufe bauen denselben Bestand. Ein Unterschied in den
# Zahlen kommt dann von der Fassung oder dem Rechner, nicht vom Zufall.
SAMEN = 20260924

# 500 Bytes Text je Knoten — dieselbe Annahme wie die Messung vom 17.09.
# in VERTRAG.md Abschnitt 5, damit die Zahlen vergleichbar bleiben.
TEXT_BYTES = 500

# Bewusst nicht dokumentförmig, wie in tests/test_flatgraph_graph.py:
# flatgraph wird ohne pDMS gemessen.
SAMMLUNG = "anlagen"
KANTENART = "versorgt"
ORTE = ["Halle 1", "Halle 2", "Keller", "Dach", "Werkstatt", "Lager"]
WOERTER = ("pumpe ventil motor sensor leitung filter kessel schieber "
           "druck temperatur wartung pruefung austausch dichtung lager").split()


class Messfehler(Exception):
    """Die gemessene Operation lieferte nicht, was sie liefern soll."""


def lade_fassung():
    return lade()


def _text(rnd, laenge):
    teile, n = [], 0
    while n < laenge:
        w = rnd.choice(WOERTER)
        teile.append(w)
        n += len(w) + 1
    return " ".join(teile)[:laenge]


def knoten_id(i):
    return f"a_{i:06d}"


def bestand_bauen(fg, wurzel, anzahl, text_bytes=TEXT_BYTES, kanten_je_knoten=0):
    """Legt `anzahl` Knoten in einer Sammlung an und öffnet den Bestand neu.

    `kanten_je_knoten` legt so viele Kanten einer Art je Knoten an, zu
    zufälligen Zielen. Vorgabe 0: wer keine Kanten misst, soll auch keine
    mitbezahlen.

    Gebaut wird in EINER Transaktion: der Aufbau ist nicht, was gemessen
    wird, und einzeln geschrieben wäre er bei grossen Beständen der
    langsamste Teil des ganzen Laufs.

    Zurück kommt eine FRISCH geöffnete Instanz. Die bauende trägt noch
    Zustand aus dem Aufbau (Indizes, Puffer); gemessen werden soll ein
    Bestand, wie ihn ein Anwender nach dem Start vorfindet.
    """
    rnd = random.Random(SAMEN)
    db = fg.FlatGraphDB(wurzel)
    with db.transaction():
        for i in range(anzahl):
            db.create_node(SAMMLUNG, knoten_id(i), {
                "name": f"Anlage {i}",
                "ort":  rnd.choice(ORTE),
                "text": _text(rnd, text_bytes),
            })
        for i in range(anzahl):
            for _ in range(kanten_je_knoten):
                db.create_edge(f"{SAMMLUNG}/{knoten_id(i)}",
                               f"{SAMMLUNG}/{knoten_id(rnd.randrange(anzahl))}",
                               KANTENART)
    schliessen(db)
    return fg.FlatGraphDB(wurzel)


def auf_platte(wurzel, art, name, kennung):
    """Einen Knoten bzw. eine Kante so lesen, wie er auf der Platte steht.

    Für die Gegenprobe der Schreibmessungen: geschrieben ist erst, was in
    einer Datei steht. Liest Speicherform 2 (eine Datei je Knoten, eine je
    Kantenart) und 3 (Fächer), damit dieselbe Messung beide Fassungen misst.
    Gibt None zurück, wenn der Eintrag nicht auf der Platte steht.
    """
    basis = os.path.join(wurzel, "datenbank", art)
    if art == "nodes":
        einzeln = os.path.join(basis, name, f"{kennung}.json")
        if os.path.isfile(einzeln):
            with open(einzeln, encoding="utf-8") as f:
                return json.load(f)
    else:
        gesammelt = os.path.join(basis, f"{name}.json")
        if os.path.isfile(gesammelt):
            with open(gesammelt, encoding="utf-8") as f:
                return json.load(f).get(kennung)
    ordner = os.path.join(basis, name)
    if not os.path.isdir(ordner):
        return None
    for datei in os.listdir(ordner):
        if datei.startswith("fach_") and datei.endswith(".json"):
            with open(os.path.join(ordner, datei), encoding="utf-8") as f:
                inhalt = json.load(f)
            if kennung in inhalt:
                return inhalt[kennung]
    return None


def schliessen(db):
    """Eine Instanz schliessen, wenn es eine gibt und die Fassung close() kennt.

    Seit 3.0.0-entwurf hat ein Bestand EINE offene Instanz; wer neu öffnet,
    muss die vorige vorher schliessen. Ältere Fassungen kennen close() nicht
    — dann ist nichts zu tun, und dieselbe Messung läuft auch gegen sie.
    """
    zu = getattr(db, "close", None)
    if zu is not None:
        zu()


class Bestand:
    """Ein Wegwerfbestand in einem eigenen Verzeichnis, danach weggeräumt.

        with Bestand(fg, 2000) as db:
            ...
    """

    def __init__(self, fg, anzahl, text_bytes=TEXT_BYTES, kanten_je_knoten=0):
        self.fg, self.anzahl, self.text_bytes = fg, anzahl, text_bytes
        self.kanten_je_knoten = kanten_je_knoten
        self.wurzel = None

    def __enter__(self):
        self.wurzel = tempfile.mkdtemp(prefix="flatgraph_bench_")
        self.db = bestand_bauen(self.fg, self.wurzel, self.anzahl, self.text_bytes,
                                self.kanten_je_knoten)
        return self.db

    def __exit__(self, *_):
        schliessen(self.db)
        shutil.rmtree(self.wurzel, ignore_errors=True)


def messen(name, anzahl, aufruf, pruefen, stichproben=15, mindestdauer=0.005,
           vorher=None):
    """Misst einen Aufruf und gibt eine Ergebniszeile zurück.

    `pruefen(ergebnis)` läuft EINMAL vor der Messung und muss wahr sein.
    Das ist die Gegenprobe dieser Suite: eine Operation, die ins Leere
    greift — falsche Referenz, leere Sammlung —, ist verdächtig schnell.
    Ohne Prüfung stünde diese Zahl in der Tabelle, als wäre sie echt.

    Sehr kurze Aufrufe (get_node: ~2 µs) liegen unter der Auflösung einer
    einzelnen Zeitnahme. Deshalb wird je Stichprobe so oft wiederholt, bis
    sie mindestens `mindestdauer` dauert, und durch die Wiederholungen
    geteilt. Berichtet wird der Median der Stichproben: er ist robust gegen
    einen einzelnen Ausreisser, den ein anderer Prozess verursacht hat.

    `vorher()` läuft vor JEDER Stichprobe, ausserhalb der Zeitnahme — etwa
    um den Dateicache zu leeren. Dann gibt es genau einen Aufruf je
    Stichprobe: ein zweiter fände den Zustand schon nicht mehr vor, den
    `vorher` hergestellt hat.

    Die Speicherbereinigung ist während einer Stichprobe aus, wie bei
    `timeit`: sonst fällt ihre Arbeit zufällig in die eine oder andere
    Stichprobe und macht die Streuung grösser, als die Operation ist.
    """
    ergebnis = aufruf()
    if not pruefen(ergebnis):
        raise Messfehler(f"{name} (Bestand {anzahl}): Prüfung fehlgeschlagen, "
                         f"Ergebnis {type(ergebnis).__name__}")

    wiederholungen = 1
    while vorher is None:
        dauer = _stichprobe(aufruf, wiederholungen)
        if dauer >= mindestdauer or wiederholungen >= 1_000_000:
            break
        wiederholungen *= 2

    je_aufruf = []
    for _ in range(stichproben):
        if vorher is not None:
            vorher()
        je_aufruf.append(_stichprobe(aufruf, wiederholungen) / wiederholungen)
    return {
        "messung":        name,
        "bestand":        anzahl,
        "median_s":       statistics.median(je_aufruf),
        "min_s":          min(je_aufruf),
        "max_s":          max(je_aufruf),
        "wiederholungen": wiederholungen,
        "stichproben":    stichproben,
    }


def _stichprobe(aufruf, wiederholungen):
    gc_war_an = gc.isenabled()
    gc.disable()
    try:
        t0 = time.perf_counter()
        for _ in range(wiederholungen):
            aufruf()
        return time.perf_counter() - t0
    finally:
        if gc_war_an:
            gc.enable()


DROP_CACHES = "/proc/sys/vm/drop_caches"


def dateicache_leerbar():
    """Ob dieser Lauf den Dateicache des Systems leeren darf (Linux, root)."""
    return os.path.exists(DROP_CACHES) and os.access(DROP_CACHES, os.W_OK)


def dateicache_leeren():
    """Seitencache verwerfen, damit der nächste Zugriff von der Platte liest.

    `sync` zuerst: schmutzige Seiten lassen sich nicht verwerfen, nur
    schreiben. Ohne sync bliebe ein Teil des Bestands im Speicher, und die
    Kaltmessung wäre lauwarm.
    """
    os.sync()
    with open(DROP_CACHES, "w") as f:
        f.write("3\n")


def _dateisystem(pfad):
    try:
        return subprocess.run(["stat", "-f", "-c", "%T", pfad], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "?"


def umgebung(fg):
    """Was neben den Zahlen stehen muss, damit sie etwas bedeuten."""
    return {
        "zeitpunkt":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fassung":      getattr(fg, "__version__", "?"),
        "datei":        getattr(fg, "__pfad__", "?"),
        "speicherform": getattr(fg, "SPEICHERFORM", "?"),
        "commit":       _git_stand(),
        "python":       platform.python_version(),
        "system":       f"{platform.system()} {platform.release()}",
        "prozessor":    platform.processor() or platform.machine(),
        "kerne":        os.cpu_count(),
        # Ab dem Öffnen liest und schreibt die Messung auf der Platte; dann
        # gehört dazu, auf welcher.
        "ablage":       f"{tempfile.gettempdir()} ({_dateisystem(tempfile.gettempdir())})",
    }


def _git_stand():
    """Commit plus Hinweis auf ungesicherte Änderungen.

    Eine Zahl, gemessen an einem Stand mit offenen Änderungen, gehört zu
    keinem Commit — das soll man ihr ansehen.
    """
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                capture_output=True, text=True, check=True).stdout.strip()
        offen = subprocess.run(["git", "status", "--porcelain", "--", "flatgraph.py"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return commit + ("+geändert" if offen else "")
    except (OSError, subprocess.CalledProcessError):
        return "?"


def _zeit(sekunden):
    if sekunden < 1e-3:
        return f"{sekunden * 1e6:9.2f} µs"
    if sekunden < 1:
        return f"{sekunden * 1e3:9.2f} ms"
    return f"{sekunden:9.2f} s "


def tabelle(zeilen):
    kopf = f"{'Messung':<32} {'Bestand':>8} {'Median':>12} {'Min':>12} {'Max':>12} {'Wdh.':>8}"
    print(kopf)
    print("-" * len(kopf))
    for z in zeilen:
        print(f"{z['messung']:<32} {z['bestand']:>8} {_zeit(z['median_s']):>12} "
              f"{_zeit(z['min_s']):>12} {_zeit(z['max_s']):>12} {z['wiederholungen']:>8}")
