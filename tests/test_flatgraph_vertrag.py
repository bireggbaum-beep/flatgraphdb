"""
Tut flatgraph, was `flatgraph/VERTRAG.md` zusagt?

Der Vertrag ist am 17.09.2026 aus Messungen entstanden, nicht aus dem
Lesen des Codes. Diese Suite dreht das um: sie nimmt jede Zusage und
prüft sie nach. Eine Zusage ohne Prüfung ist eine Behauptung.

Sie prüft auch die **Fallen** aus Abschnitt 4 — also Verhalten, das
schlecht ist und im Vertrag als schlecht benannt wird. Das ist Absicht:
ändert sich eines davon, soll die Prüfung rot werden, damit Vertrag und
Code gemeinsam nachgezogen werden. Ein Vertrag, der stillschweigend
veraltet, ist schlimmer als keiner.

Importiert `pdms` nicht.

    python tests/test_flatgraph_vertrag.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_vertrag.py
"""
import json
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


def wirft(fn):
    """Was kommt heraus: der Ausnahmetyp, oder der Rückgabewert."""
    try:
        return fn()
    except Exception as e:
        return type(e).__name__


WURZEL = tempfile.mkdtemp(prefix="fg_vertrag_")


def frisch(**kw):
    return fg.MaintenanceEngine(tempfile.mkdtemp(dir=WURZEL), **kw)


# =========================================================================
# §4 — Verhalten im Fehlerfall
# =========================================================================
print("--- §4 Fehlerfall ---")
db = frisch(schemas={"d": {"titel": str}})
db.create_node("d", "a", {"titel": "A"})

erwartet = [
    ("create_node auf vorhandene Kennung", lambda: db.create_node("d", "a", {"titel": "X"}), "KnotenExistiert"),
    ("create_node mit falschem Feldtyp", lambda: db.create_node("d", "b", {"titel": 42}), "TypeError"),
    ("create_node ohne Pflichtfeld", lambda: db.create_node("d", "c", {"x": 1}), "ValueError"),
    ("create_edge auf fehlenden Knoten", lambda: db.create_edge("d/weg", "d/a", "l"), "ValueError"),
    ("get_node auf fehlenden Knoten", lambda: db.get_node("d/weg"), None),
    ("get_edge auf fehlende Kante", lambda: db.get_edge("gibtsnicht"), None),
    ("list_nodes auf fehlende Sammlung", lambda: db.list_nodes("zzz"), {}),
]
for name, fn, soll in erwartet:
    ist = wirft(fn)
    check(name + f" → {soll}", ist == soll, f"kam: {ist!r}")

# Was frueher still scheiterte. Bis 2.1 gaben diese vier `False` zurueck —
# ein Schreibvorgang, der ins Leere ging, meldete sich mit einem
# Rueckgabewert, den Aufrufer routinemaessig ignorieren. Nachgezaehlt in
# pDMS: 22 Aufrufstellen, KEINE davon liest ihn. Eine vertippte Kennung
# hiess damit: nichts passiert, niemand merkt es.
print("--- §4 was frueher still scheiterte ---")
lautlos = [
    ("update_node auf fehlenden Knoten", lambda: db.update_node("d", "weg", {"titel": "Y"}), "KnotenFehlt"),
    ("update_node auf fehlende Sammlung", lambda: db.update_node("zzz", "a", {"titel": "Y"}), "KnotenFehlt"),
    ("delete_edge auf fehlende Kante", lambda: db.delete_edge("gibtsnicht"), "KanteFehlt"),
    ("soft_delete auf fehlenden Knoten", lambda: db.soft_delete("d", "weg"), "KnotenFehlt"),
    ("restore_node auf fehlenden Knoten", lambda: db.restore_node("d", "weg"), "KnotenFehlt"),
]
for name, fn, soll in lautlos:
    ist = wirft(fn)
    check(f"{name} wirft {soll}", ist == soll, f"kam: {ist!r}")

check("get_node unterscheidet jetzt Tippfehler von 'gibt es nicht'",
      wirft(lambda: db.get_node("ohne-schraegstrich")) == "UngueltigeReferenz"
      and db.get_node("d/weg") is None)

# Der Sinn eigener Typen: unterscheiden koennen, ohne in Fehlertexten zu
# suchen. Und sie erben zusaetzlich von den alten, damit bestehender
# Aufrufcode weiter funktioniert — deshalb bricht 2.2 nichts.
check("Alle sind FlatGraphFehler",
      all(issubclass(t, fg.FlatGraphFehler) for t in
          (fg.KnotenFehlt, fg.KanteFehlt, fg.KnotenExistiert,
           fg.UngueltigeReferenz, fg.DateiKaputt, fg.SpeicherformZuNeu)))
check("KnotenFehlt wird von altem 'except KeyError' noch gefangen",
      issubclass(fg.KnotenFehlt, KeyError))
check("DateiKaputt von 'except RuntimeError'",
      issubclass(fg.DateiKaputt, RuntimeError))
check("UngueltigeReferenz von 'except ValueError'",
      issubclass(fg.UngueltigeReferenz, ValueError))

# Die Schemaluecke besteht weiter und steht so im Vertrag: ein Unterstrich
# hebelt die Pruefung aus. Wird sie geschlossen, wird diese Pruefung rot.
check("Ein Feld mit Unterstrich umgeht die Schemaprüfung",
      db.update_node("d", "a", {"_titel": 42}) is True)
check("Ohne Unterstrich greift sie",
      wirft(lambda: db.update_node("d", "a", {"titel": 42})) == "TypeError")

# =========================================================================
# §2.1/2.2 — ganz oder gar nicht, und dauerhaft
# =========================================================================
print("--- §2.1 Schreibvorgang ---")
db2 = frisch()
db2.create_node("k", "k_1", {"wert": "urspruenglich"})
db2.flush()
# Der Pfad steht hier ausgeschrieben und wird nicht bei der Bibliothek
# erfragt: geprueft wird, was AUF DER PLATTE passiert, und eine Pruefung,
# die den Ort vom Prüfling erfährt, würde eine Verlegung stillschweigend
# mitmachen. Abgefragt wird nur die FORM — dieselbe Suite soll gegen beide
# Fassungen laufen können, und genau dieser Unterschied ist der Grund,
# warum es SPEICHERFORM überhaupt gibt.
#
#   Form 1  nodes/k_temp.json            eine Deltadatei für die ganze Collection
#   Form 2  nodes/k/k_1.json             eine Datei je Knoten
#   Form 3  nodes/k/fach_000001.json     Fächer zu bis zu 25 Knoten
if getattr(fg, "SPEICHERFORM", 1) >= 3:
    datei = os.path.join(db2.root, "datenbank", "nodes", "k", "fach_000001.json")
elif getattr(fg, "SPEICHERFORM", 1) == 2:
    datei = os.path.join(db2.root, "datenbank", "nodes", "k", "k_1.json")
else:
    datei = os.path.join(db2.root, "datenbank", "nodes", "k_temp.json")
vorher = open(datei, encoding="utf-8").read()

# Absturz genau zwischen Arbeitsdatei und Austausch nachstellen.
echt_replace = os.replace


def replace_faellt_aus(a, b):
    raise OSError("Strom weg")


os.replace = replace_faellt_aus
try:
    db2.update_node("k", "k_1", {"wert": "haette-nicht-ankommen-sollen"})
    db2.flush()
except OSError:
    pass
finally:
    os.replace = echt_replace

check("Nach einem Absturz beim Austausch ist die alte Datei unversehrt",
      open(datei, encoding="utf-8").read() == vorher)
check("Und sie ist gültiges JSON",
      wirft(lambda: json.load(open(datei, encoding="utf-8"))) is not None)

db3 = neu_oeffnen(db2, fg.MaintenanceEngine)
check("Der Bestand von der Platte trägt noch den alten Wert",
      (db3.get_node("k/k_1") or {}).get("wert") == "urspruenglich",
      str(db3.get_node("k/k_1")))

# =========================================================================
# §2.4 — Löschen ist zweistufig und wiederholbar
# =========================================================================
print("--- §2.4 Löschen ---")
db4 = frisch()
for i in range(3):
    db4.create_node("k", f"k_{i}", {"n": i})
db4.create_edge("k/k_0", "k/k_1", "l")
db4.soft_delete("k", "k_1")

check("Weich gelöscht ist aus list_nodes weg", "k_1" not in db4.list_nodes("k"))
check("Mit include_deleted noch da", "k_1" in db4.list_nodes("k", include_deleted=True))
check("Und wiederherstellbar, solange nicht aufgeräumt",
      db4.restore_node("k", "k_1") is True and db4.get_node("k/k_1") is not None)

db4.soft_delete("k", "k_1")
s1 = db4.run_garbage_collection()
check("Aufräumen entfernt endgültig", db4.get_node_raw("k/k_1") is None)
check("Und nimmt die Kante mit", db4.list_edges() == {}, str(db4.list_edges()))
check("Es meldet, was es tat", s1.get("nodes_purged") == 1, str(s1))

s2 = db4.run_garbage_collection()
check("Ein zweiter Lauf richtet keinen Schaden an",
      s2.get("nodes_purged") == 0 and len(db4.list_nodes("k")) == 2, str(s2))

# =========================================================================
# §2.5 — eine Kante zeigt beim Anlegen auf vorhandene Knoten
# =========================================================================
print("--- §2.5 Kanten ---")
# Ein Knoten OHNE Felder ist ein leeres Dict — und damit falsy. Dieselbe
# Frage darf nicht drei Antworten haben: list_nodes fuehrte ihn,
# get_node_raw lieferte ihn, get_node sagte None. create_edge weigerte sich
# deshalb, ihn zu verknuepfen. Das readme nennt diesen Fall EMPTY-Infosatz:
# ein Datensatz, dessen Nutzlast noch fehlt.
#
# Eigene Datenbank, damit eine aeltere Fassung hier nur DIESE Pruefungen
# verliert und die uebrigen noch durchlaufen.
db_leer = frisch()
db_leer.create_node("k", "a", {})
check("Ein Knoten ohne Felder ist über list_nodes da", "a" in db_leer.list_nodes("k"))
check("Über get_node_raw auch", db_leer.get_node_raw("k/a") == {})
check("Und über get_node ebenfalls", db_leer.get_node("k/a") == {},
      repr(db_leer.get_node("k/a")))
db_leer.create_node("k", "b", {})
check("Und er lässt sich verknüpfen",
      wirft(lambda: db_leer.create_edge("k/a", "k/b", "l")) != "ValueError",
      str(wirft(lambda: db_leer.create_edge("k/a", "k/b", "l")))[:50])

db5 = frisch()
db5.create_node("k", "a", {"n": 1})
db5.create_node("k", "b", {"n": 2})
check("Auf vorhandene Knoten geht es", bool(db5.create_edge("k/a", "k/b", "l")))
check("Auf einen fehlenden nicht",
      wirft(lambda: db5.create_edge("k/a", "k/weg", "l")) == "ValueError")
db5.soft_delete("k", "b")
check("Auf einen weich gelöschten auch nicht",
      wirft(lambda: db5.create_edge("k/a", "k/b", "l")) == "ValueError")
# Aber eine BESTEHENDE Kante überlebt das Weichlöschen ihres Ziels — der
# Vertrag sagt das ausdrücklich, und gefiltert wird erst beim Lesen.
check("Eine bestehende Kante bleibt trotzdem liegen",
      len(db5.list_edges()) == 1, str(db5.list_edges()))
check("Wird beim Lesen aber ausgeblendet",
      db5.get_connected("k/a") == [], str(db5.get_connected("k/a")))

# =========================================================================
# §6/§7 — Form auf der Platte und Kennungen
# =========================================================================
print("--- §6 Platte, §7 Kennungen ---")
db6 = frisch()
db6.create_node("k", "k_1", {"n": 1})
db6.create_edge("k/k_1", "k/k_1", "selbst")
db6.flush()
wurzel = db6.root
for teil in ("datenbank/nodes", "datenbank/edges", "vault", "vault_archive"):
    check(f"Es gibt {teil}", os.path.isdir(os.path.join(wurzel, teil)))
# Seit 3.0.0-entwurf liegt der Feldindex nur im Speicher (VERTRAG.md 6).
check("Es gibt kein datenbank/index mehr",
      not os.path.exists(os.path.join(wurzel, "datenbank", "index")))
if getattr(fg, "SPEICHERFORM", 1) >= 3:
    check("Kanten liegen je Art in Fächern",
          os.path.isfile(os.path.join(wurzel, "datenbank", "edges", "selbst", "fach_000001.json")))
else:
    check("Kanten liegen je Art in einer Datei",
          os.path.isfile(os.path.join(wurzel, "datenbank", "edges", "selbst.json")))

db7 = frisch()
check("next_id auf leerer Sammlung fängt bei 1 an",
      db7.next_id("k", prefix="n_", padding=4) == "n_0001")
db7.create_node("k", "n_0001", {"n": 1})
check("Und zählt hoch", db7.next_id("k", prefix="n_", padding=4) == "n_0002")
db7.create_node("k", "n_0009", {"n": 9})
check("Es ist das Maximum plus eins, nicht die Anzahl",
      db7.next_id("k", prefix="n_", padding=4) == "n_0010")
check("Ein anderes Präfix zählt eigenständig",
      db7.next_id("k", prefix="x_", padding=4) == "x_0001")

# =========================================================================
# §3.3 — Speicherform ist beziffert
# =========================================================================
print("--- §3.3 Speicherform ---")
check("Die Bibliothek nennt ihre Fassung", bool(getattr(fg, "__version__", "")),
      getattr(fg, "__version__", "—"))
check("Und die Speicherform getrennt davon",
      isinstance(getattr(fg, "SPEICHERFORM", None), int),
      str(getattr(fg, "SPEICHERFORM", None)))

# §3.3 sagt jetzt: eine neuere Speicherform wird VERWEIGERT. Vorher stand
# hier die Warnung, dass es diese Pruefung noch nicht gibt — sie ist am
# 17.09. gebaut worden, und damit wird aus der Warnung eine Pruefung.
db8 = frisch()
db8.create_node("k", "a", {"n": 1})
db8.flush()
marke = os.path.join(db8.root, "datenbank", "_meta.json")
check("Beim Öffnen entsteht eine Formatmarke", os.path.isfile(marke))
inhalt = json.load(open(marke, encoding="utf-8"))
check("Sie nennt die Speicherform", inhalt.get("speicherform") == fg.SPEICHERFORM,
      str(inhalt))
check("Und wer sie geschrieben hat", bool(inhalt.get("geschrieben_von")), str(inhalt))

# Die Instanz schliessen, bevor neu geoeffnet wird: seit 3.0.0-entwurf
# hat ein Bestand eine offene Instanz, und die Probe soll an der
# Speicherform scheitern, nicht an der Sperre. Dass ein GESCHEITERTES
# Oeffnen die Sperre wieder freigibt, prueft der Rest dieses Abschnitts
# nebenbei: jedes weitere Oeffnen unten setzt es voraus.
getattr(db8, "close", lambda: None)()
json.dump({"speicherform": fg.SPEICHERFORM + 1}, open(marke, "w"))
check("Ein neuerer Bestand wird verweigert",
      wirft(lambda: fg.FlatGraphDB(db8.root)) == "SpeicherformZuNeu",
      str(wirft(lambda: fg.FlatGraphDB(db8.root))))

# Der eigene Fehlertyp ist der Punkt: der Aufrufer soll "zu alt" von
# "kaputt" unterscheiden koennen, ohne in Fehlertexten zu suchen.
try:
    fg.FlatGraphDB(db8.root)
    gefangen = False
except fg.SpeicherformZuNeu as e:
    gefangen = e.gefunden == fg.SPEICHERFORM + 1 and e.unterstuetzt == fg.SPEICHERFORM
check("Der Fehler trägt die Zahlen mit sich", gefangen)
check("Und er ist ein FlatGraphFehler",
      issubclass(fg.SpeicherformZuNeu, fg.FlatGraphFehler))

json.dump({"speicherform": fg.SPEICHERFORM}, open(marke, "w"))
db_normal = fg.FlatGraphDB(db8.root)
check("Dieselbe Form öffnet normal", len(db_normal.list_nodes("k")) == 1)
getattr(db_normal, "close", lambda: None)()

# Ein Bestand von VOR dieser Pruefung hat keine Marke. Das ist kein Fehler
# — sonst liesse sich kein einziger bestehender Bestand mehr oeffnen.
os.remove(marke)
db9 = fg.FlatGraphDB(db8.root)
check("Ein Bestand ohne Marke öffnet trotzdem", len(db9.list_nodes("k")) == 1)
check("Und die Marke wird nachgetragen", os.path.isfile(marke))

# =========================================================================
# §4 — Der Unterstrich gehoert flatgraph, nicht dem Aufrufer
# =========================================================================
print("--- §4 Unterstrich-Felder ---")
# Frueher war JEDES Feld mit fuehrendem Unterstrich von der Schemapruefung
# ausgenommen. Wer ein solches Feld ins Schema schrieb, bekam zweierlei:
# es wurde nie geprueft, und jede gewoehnliche Aenderung am Knoten schlug
# fehl, weil das Feld vor der Pruefung herausfiel und danach als fehlend
# gemeldet wurde. Beide Faelle stehen hier.
db10 = frisch(schemas={"d": {"titel": str, "_intern": str}})
db10.create_node("d", "a", {"titel": "A", "_intern": "x"})

check("Ein Schemafeld mit Unterstrich wird geprüft",
      wirft(lambda: db10.update_node("d", "a", {"_intern": 42})) == "TypeError")
check("Und es blockiert keine gewöhnliche Änderung",
      wirft(lambda: db10.update_node("d", "a", {"titel": "B"})) is True)
check("Löschen geht auch bei einem Knoten, der nicht zum Schema passt",
      wirft(lambda: db10.soft_delete("d", "a")) is True)

# Die Gegenrichtung: ohne Schemaeintrag ist ein Unterstrichfeld so
# unbekannt wie jedes andere — flatgraphs Schemata sind bewusst
# unvollstaendig, sie verbieten keine zusaetzlichen Felder.
db11 = frisch(schemas={"d": {"titel": str}})
db11.create_node("d", "b", {"titel": "A"})
check("Ein unbekanntes Feld bleibt erlaubt, mit und ohne Unterstrich",
      db11.update_node("d", "b", {"quatsch": 42}) is True
      and db11.update_node("d", "b", {"_quatsch": 42}) is True)

shutil.rmtree(WURZEL, ignore_errors=True)

print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
