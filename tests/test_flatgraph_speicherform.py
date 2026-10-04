"""
Der Umzug auf eine Datei je Knoten — SPEICHERFORM 1 -> 2.

Diese Suite gibt es, weil genau dieser Umbau am 16.09.2026 schon einmal
gebaut und zurückgenommen werden musste. Damals zog eine Fassung den
Bestand um, und eine ältere las ihn danach **kommentarlos als leer**: die
Dateien hiessen anders, sie fand nichts, und meldete auch nichts.

Zwei Dinge halten das auseinander, und beide werden hier geprüft:

  die Marke       `datenbank/_meta.json` sagt, in welcher Form der Bestand
                  liegt. Eine Fassung, die sie nicht versteht, verweigert
                  den Dienst statt zu raten.
  die Reihenfolge Erst alle neuen Dateien, dann die alten weg, die Marke
                  ZULETZT. Ein Abbruch lässt damit einen unveränderten
                  Bestand zurück, keinen halb umgezogenen.

Importiert `pdms` nicht.

    python tests/test_flatgraph_speicherform.py
"""
import json
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


def wirft(fn):
    try:
        fn()
        return None
    except Exception as e:
        return type(e).__name__


WURZEL = tempfile.mkdtemp(prefix="fg_form_")
NODES = ("datenbank", "nodes")


def bestand_in_form_1(knoten, delta=None):
    """Einen Bestand von Hand in der ALTEN Form hinlegen.

    Von Hand und nicht mit einer alten Fassung der Bibliothek: die Suite
    soll auch dann noch laufen, wenn es die alte Fassung im Repo nicht mehr
    gibt. Geprüft wird das Format, nicht der Code, der es einst schrieb.
    """
    wurzel = tempfile.mkdtemp(dir=WURZEL)
    ordner = os.path.join(wurzel, *NODES)
    os.makedirs(ordner, exist_ok=True)
    with open(os.path.join(ordner, "d.json"), "w", encoding="utf-8") as f:
        json.dump(knoten, f)
    if delta:
        with open(os.path.join(ordner, "d_temp.json"), "w", encoding="utf-8") as f:
            json.dump(delta, f)
    return wurzel


if getattr(fg, "SPEICHERFORM", 1) < 3:
    # Die Prüfungen für Form 2 stehen in der Git-Geschichte dieser Datei
    # (bis Commit 67d066a). Ab hier prüft die Suite Form 3 samt Umzug 1 -> 3.
    print("Diese Suite prüft SPEICHERFORM 3; der Prüfling liegt in Form "
          f"{getattr(fg, 'SPEICHERFORM', 1)}. Nichts zu tun.")
    print("0/0 Checks bestanden")
    raise SystemExit(0)

# =========================================================================
# Der Umzug
# =========================================================================
print("--- Umzug 1 -> 3 ---")

wurzel = bestand_in_form_1({"d_1": {"titel": "eins"}, "d_2": {"titel": "zwei"}},
                           delta={"d_2": {"titel": "zwei, geaendert"},
                                  "d_3": {"titel": "drei"}})
db = fg.FlatGraphDB(wurzel)

check("Alle Knoten sind nach dem Umzug da",
      set(db.list_nodes("d")) == {"d_1", "d_2", "d_3"}, str(sorted(db.list_nodes("d"))))

# Das Delta lag in Form 1 ÜBER der Sammeldatei. Wer beim Umzug die
# Reihenfolge vertauscht, verliert genau die zuletzt geschriebenen Werte —
# und merkt es nicht, weil der Knoten ja da ist.
check("Das Delta gewinnt gegen die Sammeldatei",
      db.get_node("d/d_2")["titel"] == "zwei, geaendert",
      db.get_node("d/d_2")["titel"])

# Seit Speicherform 3 zieht ein Bestand in Form 1 über Form 2 weiter in
# Fächer um. Geprüft wird, was auf der Platte steht, nicht der Dateiname.
check("Alle Knoten stehen nach dem Umzug auf der Platte",
      set(platte(wurzel)["d"]) == {"d_1", "d_2", "d_3"}, str(sorted(platte(wurzel)["d"])))

check("Sammeldatei und Delta sind weg",
      not os.path.exists(os.path.join(wurzel, *NODES, "d.json"))
      and not os.path.exists(os.path.join(wurzel, *NODES, "d_temp.json")))

marke = json.load(open(os.path.join(wurzel, "datenbank", "_meta.json"), encoding="utf-8"))
check("Die Marke steht auf der aktuellen Form",
      marke.get("speicherform") == fg.SPEICHERFORM, str(marke))

# 04.10.2026 im Betrieb: eine Sammlung, die nie verdichtet wurde, hat nur
# die _temp-Datei. Der Umzug suchte nur Sammeldateien, fand nichts, setzte
# die Marke und loeschte das Alte — 874 Dokumente weg, ohne Fehlermeldung.
wurzel = tempfile.mkdtemp(dir=WURZEL)
ordner = os.path.join(wurzel, *NODES)
os.makedirs(ordner)
with open(os.path.join(ordner, "d_temp.json"), "w", encoding="utf-8") as f:
    json.dump({"d_1": {"titel": "nur im Delta"}}, f)
db = fg.FlatGraphDB(wurzel)
check("Eine Sammlung nur mit Delta-Datei geht beim Umzug nicht verloren",
      set(db.list_nodes("d")) == {"d_1"}, str(sorted(db.list_nodes("d"))))
check("Sie steht danach auch auf der Platte",
      set(platte(wurzel).get("d", {})) == {"d_1"}, str(platte(wurzel)))

# =========================================================================
# Die Reihenfolge — ein Abbruch darf nichts kosten
# =========================================================================
print("--- Abbruch mittendrin ---")

wurzel2 = bestand_in_form_1({"d_1": {"titel": "eins"}, "d_2": {"titel": "zwei"}})
vorher = open(os.path.join(wurzel2, *NODES, "d.json"), encoding="utf-8").read()

echt_remove = os.remove


def remove_faellt_aus(pfad):
    # Genau dann abbrechen, wenn die neuen Dateien geschrieben sind und das
    # Alte weggeraeumt werden soll. Das ist der gefaehrlichste Augenblick.
    if pfad.endswith("d.json") and os.sep + "nodes" + os.sep + "d.json" in pfad:
        raise OSError("Strom weg")
    return echt_remove(pfad)


os.remove = remove_faellt_aus
try:
    fehler = wirft(lambda: fg.FlatGraphDB(wurzel2))
finally:
    os.remove = echt_remove

check("Ein Abbruch beim Aufräumen schlägt durch, statt zu schweigen",
      fehler == "OSError", str(fehler))
check("Die Sammeldatei ist unversehrt",
      open(os.path.join(wurzel2, *NODES, "d.json"), encoding="utf-8").read() == vorher)
check("Und die Marke steht NICHT auf 2 — der nächste Start zieht erneut um",
      not os.path.exists(os.path.join(wurzel2, "datenbank", "_meta.json")))

# Der Beweis, dass der unterbrochene Umzug wirklich nachholbar ist.
db2 = fg.FlatGraphDB(wurzel2)
check("Der zweite Anlauf bringt den Bestand vollständig herüber",
      set(db2.list_nodes("d")) == {"d_1", "d_2"}, str(sorted(db2.list_nodes("d"))))

# =========================================================================
# Die Marke schützt nach vorn
# =========================================================================
print("--- Eine zu neue Form ---")

wurzel3 = tempfile.mkdtemp(dir=WURZEL)
os.makedirs(os.path.join(wurzel3, "datenbank"), exist_ok=True)
with open(os.path.join(wurzel3, "datenbank", "_meta.json"), "w", encoding="utf-8") as f:
    json.dump({"speicherform": fg.SPEICHERFORM + 1, "geschrieben_von": "aus der Zukunft"}, f)

check("Eine neuere Speicherform wird verweigert, nicht geraten",
      wirft(lambda: fg.FlatGraphDB(wurzel3)) == "SpeicherformZuNeu")

# =========================================================================
# Ein Knoten, eine Datei — auch beim Löschen
# =========================================================================
print("--- Schreiben und Löschen ---")

# MaintenanceEngine statt FlatGraphDB: nur sie kennt den Muellsammler, und
# genau der ist hier interessant.
db3 = fg.MaintenanceEngine(tempfile.mkdtemp(dir=WURZEL))
db3.create_node("d", "d_1", {"titel": "bleibt"})
db3.create_node("d", "d_2", {"titel": "geht"})
db3.flush()
check("Beide Knoten stehen auf der Platte",
      set(platte(db3.root)["d"]) == {"d_1", "d_2"}, str(platte(db3.root)["d"]))

db3.soft_delete("d", "d_2")
db3.run_garbage_collection()
# Weich geloescht und dann endgueltig entfernt: in Form 1 blieb der Eintrag
# in der Sammeldatei stehen, bis jemand verdichtete. Seit Form 2 gilt:
# weg ist weg, sobald der Müllsammler gelaufen ist.
check("Ein endgültig gelöschter Knoten steht nicht mehr auf der Platte",
      set(platte(db3.root)["d"]) == {"d_1"}, str(platte(db3.root)["d"]))

# =========================================================================
# Langtexte — beim Umzug UND nachtraeglich
# =========================================================================
print("--- Langtexte wandern mit ---")

# Am 20.09.2026 im Betrieb aufgefallen: der Umzug war durch, das
# Verzeichnis fuer die Langtexte blieb leer. Grund war, dass der Umzug die
# Knoten direkt schrieb und dabei an der Auslagerung vorbeiging — ein
# alter Bestand haette seinen Volltext fuer immer im Knoten behalten, und
# der Start waere genauso teuer geblieben wie ohne Auslagerung.
LANG = "Kalibrierprotokoll Ultraschallpruefkopf. " * 80

wurzel4 = bestand_in_form_1({"d_1": {"titel": "Bericht", "text": LANG}})
db4 = fg.FlatGraphDB(wurzel4, longtext_threshold=2000)
roh = platte(wurzel4)["d"]["d_1"]
check("Beim Umzug wandert ein langer Text hinaus",
      str(roh.get("text", "")).startswith("@vault_text/"), str(roh.get("text"))[:50])
check("Und ist ueber get_node_full vollstaendig da",
      db4.get_node_full("d/d_1")["text"] == LANG)

# Der zweite Fall, und er betrifft jeden Bestand, der SCHON in der
# aktuellen Form liegt: dort laeuft kein Umzug mehr.
wurzel5 = tempfile.mkdtemp(dir=WURZEL)
ordner5 = os.path.join(wurzel5, *NODES, "d")
os.makedirs(ordner5, exist_ok=True)
# Von Hand in der AKTUELLEN Form: ein Fach mit einem Knoten.
with open(os.path.join(ordner5, "fach_000001.json"), "w", encoding="utf-8") as f:
    json.dump({"d_9": {"titel": "Alt", "text": LANG}}, f)
with open(os.path.join(wurzel5, "datenbank", "_meta.json"), "w", encoding="utf-8") as f:
    json.dump({"speicherform": fg.SPEICHERFORM, "geschrieben_von": "aelter"}, f)

db5 = fg.FlatGraphDB(wurzel5, longtext_threshold=2000)
roh5 = platte(wurzel5)["d"]["d_9"]
check("Ein bereits umgezogener Bestand zieht die Langtexte nach",
      str(roh5.get("text", "")).startswith("@vault_text/"), str(roh5.get("text"))[:50])
check("Auch dort bleibt der Text vollstaendig",
      db5.get_node_full("d/d_9")["text"] == LANG)

marke5 = json.load(open(os.path.join(wurzel5, "datenbank", "_meta.json"), encoding="utf-8"))
check("Die Schwelle steht in der Marke, damit es nur einmal laeuft",
      marke5.get("langtext_schwelle") == 2000, str(marke5))

db6 = neu_oeffnen(db5, longtext_threshold=2000)
check("Ein zweiter Start laesst den Text unversehrt",
      db6.get_node_full("d/d_9")["text"] == LANG)

# =========================================================================
# Ids, die keine schönen Dateinamen sind
# =========================================================================
print("--- Unbequeme Ids ---")

db4 = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
heikel = "a/b c:d?e"
db4.create_node("d", heikel, {"titel": "kodiert"})
db4.flush()
check("Eine Id mit Schrägstrich macht kein Verzeichnis auf",
      all(not os.path.isdir(os.path.join(db4.root, *NODES, "d", e))
          for e in os.listdir(os.path.join(db4.root, *NODES, "d")))
      and heikel in platte(db4.root)["d"],
      str(os.listdir(os.path.join(db4.root, *NODES, "d"))))

db5 = neu_oeffnen(db4)
check("Und sie wird beim Einlesen wieder zu genau dieser Id",
      db5.get_node(f"d/{heikel}") is not None
      and db5.get_node(f"d/{heikel}")["titel"] == "kodiert",
      str(list(db5.list_nodes("d"))))

# =========================================================================
# Speicherform 3 — Fächer
# =========================================================================
print("--- Fächer ---")
FACH = getattr(fg, "FACH_GROESSE", 25)


def faecher(wurzel, art, name):
    ordner = os.path.join(wurzel, "datenbank", art, name)
    return sorted(d for d in os.listdir(ordner) if d.startswith("fach_"))


def fachinhalt(wurzel, art, name):
    ordner = os.path.join(wurzel, "datenbank", art, name)
    return [len(json.load(open(os.path.join(ordner, d), encoding="utf-8")))
            for d in faecher(wurzel, art, name)]


db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
with db.transaction():
    for i in range(2 * FACH + 10):
        db.create_node("d", f"k_{i:03d}", {"n": i})
check(f"Fächer werden aufgefüllt: {2 * FACH + 10} Knoten liegen in {FACH}/{FACH}/10",
      fachinhalt(db.root, "nodes", "d") == [FACH, FACH, 10], str(fachinhalt(db.root, "nodes", "d")))
db.create_node("d", "k_neu", {"n": -1})
check("Ein neuer Knoten kommt ins letzte, nicht volle Fach",
      fachinhalt(db.root, "nodes", "d") == [FACH, FACH, 11], str(fachinhalt(db.root, "nodes", "d")))
db.update_node("d", "k_000", {"n": 999})
check("Eine Änderung schreibt nur ihr Fach und bleibt, wo sie ist",
      fachinhalt(db.root, "nodes", "d") == [FACH, FACH, 11]
      and platte(db.root)["d"]["k_000"] == {"n": 999})

with db.transaction():
    for i in range(FACH + 3):
        db.create_edge(f"d/k_{i:03d}", f"d/k_{i + 1:03d}", "folgt")
check(f"Kanten liegen ebenso in Fächern ({FACH + 3} Kanten: {FACH}/3)",
      fachinhalt(db.root, "edges", "folgt") == [FACH, 3], str(fachinhalt(db.root, "edges", "folgt")))
eine = db.get_connected_edges("d/k_000", "out", "folgt")[0][0]
db.delete_edge(eine)
check("Eine gelöschte Kante verschwindet aus ihrem Fach",
      eine not in platte(db.root, "edges")["folgt"]
      and len(platte(db.root, "edges")["folgt"]) == FACH + 2)

# Verdichten: der Müllsammler reisst Lücken und schliesst sie wieder.
db = neu_oeffnen(db, fg.MaintenanceEngine)
for i in range(0, 2 * FACH):
    if i % 5:                                     # 4 von 5 in den ersten zwei Fächern
        db.soft_delete("d", f"k_{i:03d}")
vorher = len(faecher(db.root, "nodes", "d"))
stats = db.run_garbage_collection()
danach = fachinhalt(db.root, "nodes", "d")
check("Der Müllsammler legt dünne Fächer zusammen",
      len(danach) < vorher and stats.get("faecher_verdichtet", 0) >= 2,
      f"vorher {vorher} Fächer, danach {danach}, {stats.get('faecher_verdichtet')}")
lebend = set(db.list_nodes("d"))
db = neu_oeffnen(db)
check("… und kein Knoten geht dabei verloren, auch nach dem Neustart",
      set(db.list_nodes("d")) == lebend == set(platte(db.root)["d"]),
      f"{len(lebend)} lebend, {len(platte(db.root)['d'])} auf der Platte")
check("… und kein Fach bleibt leer zurück", 0 not in fachinhalt(db.root, "nodes", "d"))

# Abgebrochenes Verdichten: ein Knoten steht in zwei Fächern, gleicher Inhalt.
# Ein eigener Bestand mit ZWEI vollen Fächern — nach dem Verdichten oben gibt
# es nur noch eines, und eine Kopie „ins andere Fach“ landete in derselben
# Datei. So war diese Prüfung im ersten Anlauf wertlos.
getattr(db, "close", lambda: None)()
db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
with db.transaction():
    for i in range(2 * FACH):
        db.create_node("d", f"k_{i:03d}", {"n": i})
w = db.root
getattr(db, "close", lambda: None)()
ordner = os.path.join(w, *NODES, "d")
erstes, zweites = [os.path.join(ordner, d) for d in faecher(w, "nodes", "d")]
# Die Kopie aus dem Inhalt des ERSTEN Fachs nehmen, nicht eine feste Kennung
# annehmen: sonst hängt die Prüfung davon ab, wo flatgraph einsortiert.
kopie_id, kopie = next(iter(json.load(open(erstes, encoding="utf-8")).items()))
inhalt = json.load(open(zweites, encoding="utf-8"))
inhalt[kopie_id] = kopie
json.dump(inhalt, open(zweites, "w", encoding="utf-8"))


def kopien():
    return sum(kopie_id in json.load(open(os.path.join(ordner, d), encoding="utf-8"))
               for d in faecher(w, "nodes", "d"))


vorher = kopien()
db = fg.FlatGraphDB(w)
check("Ein doppelt stehender Knoten (abgebrochenes Verdichten) wird beim Öffnen bereinigt",
      vorher == 2 and kopien() == 1 and db.get_node(f"d/{kopie_id}") == kopie,
      f"vorher {vorher}, danach {kopien()} Kopien")
getattr(db, "close", lambda: None)()
inhalt = json.load(open(erstes, encoding="utf-8"))
inhalt[kopie_id] = {"n": "anders"}
json.dump(inhalt, open(erstes, "w", encoding="utf-8"))
check("Stehen zwei VERSCHIEDENE Fassungen darin, wird nicht geraten (DateiKaputt)",
      kopien() == 2 and wirft(lambda: fg.FlatGraphDB(w)) == "DateiKaputt",
      f"{kopien()} Kopien")

# =========================================================================
# Umzug 2 -> 3, auch abgebrochen
# =========================================================================
print("--- Umzug 2 -> 3 ---")


def bestand_in_form_2():
    """Von Hand in Form 2: eine Datei je Knoten, eine Datei je Kantenart."""
    w = tempfile.mkdtemp(dir=WURZEL)
    for i in range(30):
        pfad = os.path.join(w, *NODES, "d", f"d_{i:02d}.json")
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        json.dump({"n": i}, open(pfad, "w", encoding="utf-8"))
    kanten = {f"link_{i:04d}": {"source": f"d/d_{i:02d}", "target": f"d/d_{(i + 1) % 30:02d}",
                                "type": "folgt", "created_at": "2026-09-24T00:00:00+00:00"}
              for i in range(30)}
    os.makedirs(os.path.join(w, "datenbank", "edges"), exist_ok=True)
    json.dump(kanten, open(os.path.join(w, "datenbank", "edges", "folgt.json"), "w", encoding="utf-8"))
    json.dump({"speicherform": 2}, open(os.path.join(w, "datenbank", "_meta.json"), "w", encoding="utf-8"))
    return w


def vollstaendig(w):
    d = fg.FlatGraphDB(w)
    try:
        return (len(d.list_nodes("d")) == 30 and len(d.list_edges("folgt")) == 30
                and d.get_node("d/d_07") == {"n": 7}
                and d.get_connected("d/d_07", "out", "folgt") == ["d/d_08"])
    finally:
        getattr(d, "close", lambda: None)()


w = bestand_in_form_2()
check("Ein Bestand in Form 2 zieht beim Öffnen vollständig um", vollstaendig(w))
check("… in Fächer, Knoten und Kanten",
      faecher(w, "nodes", "d") == ["fach_000001.json", "fach_000002.json"]
      and faecher(w, "edges", "folgt") == ["fach_000001.json", "fach_000002.json"])
check("… und hinterlässt keine alten Verzeichnisse",
      sorted(e for e in os.listdir(os.path.join(w, "datenbank")) if "form" in e) == [])

# Kanten mit den deutschen Feldnamen von v0.9 (quelle/ziel/typ/erstellt_am):
# übersetzt wird NUR beim Umzug, nicht mehr bei jedem Öffnen.
w = bestand_in_form_2()
alt_pfad = os.path.join(w, "datenbank", "edges", "folgt.json")
alte = json.load(open(alt_pfad, encoding="utf-8"))
alte["link_alt"] = {"quelle": "d/d_00", "ziel": "d/d_05", "typ": "folgt",
                    "erstellt_am": "2025-01-01T00:00:00+00:00"}
json.dump(alte, open(alt_pfad, "w", encoding="utf-8"))
d = fg.FlatGraphDB(w)
check("Alte deutsche Feldnamen werden beim Umzug übersetzt",
      "d/d_05" in d.get_connected("d/d_00", "out", "folgt")
      and platte(w, "edges")["folgt"]["link_alt"].get("source") == "d/d_00"
      and "quelle" not in platte(w, "edges")["folgt"]["link_alt"])
getattr(d, "close", lambda: None)()

# Abbruch an jeder Stelle des Umzugs: beim n-ten Umbenennen bzw. beim n-ten
# Schreiben. Jeder Abbruch muss beim nächsten Öffnen zu Ende geführt werden.
echt_rename, echt_replace = os.rename, os.replace
for wie, anzahl in (("rename", 3), ("replace", 4)):
    for n in range(1, anzahl + 1):
        w = bestand_in_form_2()
        zaehler = {"n": 0}

        def faellt_aus(a, b, _echt={"rename": echt_rename, "replace": echt_replace}[wie], _n=n):
            zaehler["n"] += 1
            if zaehler["n"] == _n:
                raise OSError("Strom weg")
            return _echt(a, b)

        setattr(os, wie, faellt_aus)
        try:
            fehler = wirft(lambda: fg.FlatGraphDB(w))
        finally:
            os.rename, os.replace = echt_rename, echt_replace
        check(f"Abbruch beim {n}. {wie}: der nächste Start führt den Umzug zu Ende",
              fehler == "OSError" and vollstaendig(w), f"Fehler: {fehler}")

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
