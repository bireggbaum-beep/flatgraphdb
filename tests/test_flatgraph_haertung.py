"""
Härtung: der Speicher ändert sich nur, wenn die Platte die Änderung
aufnehmen kann.

Jeder Fehler, den diese Suite festnagelt, hatte dieselbe Form: erst wurde
der Speicher geändert, dann scheiterte das Schreiben — und danach
widersprachen sich Speicher und Platte. Nach dem Neustart gilt die Platte.
Nachgewiesen am 24.09.2026 an 3.0.0-entwurf:

  - ein Knoten mit einem `date` stand im Speicher, aber nicht auf der
    Platte, liess sich nicht neu anlegen und fehlte nach dem Neustart
  - eine Kante mit einem `date` machte ihre ganze Kantenart unschreibbar
  - eine Transaktion, die beim Schreiben scheiterte, stand zur Hälfte auf
    der Platte, während der Speicher „zurückgesetzt“ meldete
  - `NaN` wurde geschrieben — kein JSON, für jedes andere Werkzeug unlesbar
  - `create_node("../../x", ...)` legte ein Verzeichnis ausserhalb von
    `datenbank/` an
  - eine Zahl als Kennung war vor dem Neustart unter 5 abgelegt, danach
    unter "5"
  - eine zu lange Kennung scheiterte erst beim Schreiben, der Knoten blieb
    (Speicherform 2; seit den Fächern ist eine Kennung kein Dateiname mehr)
    im Speicher

Gegen `flatgraph/flatgraph.py` (3.0.0) laufen gelassen, fällt diese Suite —
das ist ihre Gegenprobe:

    python tests/test_flatgraph_haertung.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_haertung.py

Importiert `pdms` nicht.
"""
import datetime
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


def fehler(fn):
    """Die geworfene Ausnahme, oder None. Nicht ihr Name: geprüft wird
    auch, von welchem eingebauten Typ sie erbt."""
    try:
        fn()
    except Exception as e:
        return e
    return None


def ist(e, name):
    return type(e).__name__ == name


WURZEL = tempfile.mkdtemp(prefix="fg_haertung_")


def frisch():
    pfad = tempfile.mkdtemp(dir=WURZEL)
    return pfad, fg.FlatGraphDB(pfad)


def auf_platte(pfad, sammlung, kennung):
    """Steht der Knoten auf der Platte — in welcher Speicherform auch immer?"""
    return kennung in platte(pfad).get(sammlung, {})


def reste(pfad):
    """Arbeitsdateien, die ein gescheiterter Schreibvorgang liegen liess."""
    return [n for _, _, namen in os.walk(pfad) for n in namen if n.endswith(".tmp")]


HEUTE = datetime.date(2026, 9, 24)

# =========================================================================
print("--- Werte, die nicht als JSON zurückkämen ---")
# =========================================================================
pfad, db = frisch()
e = fehler(lambda: db.create_node("dinge", "d1", {"am": HEUTE}))
check("Ein date im neuen Knoten wird mit NichtSpeicherbar abgewiesen",
      ist(e, "NichtSpeicherbar"), repr(e))
check("NichtSpeicherbar ist weiterhin ein TypeError", isinstance(e, TypeError))
check("Der abgewiesene Knoten steht NICHT im Speicher", db.get_node("dinge/d1") is None)
check("… und nicht auf der Platte", not auf_platte(pfad, "dinge", "d1"))
check("… und hinterlässt keine Arbeitsdatei", reste(pfad) == [], str(reste(pfad)))
check("Die Kennung ist danach frei",
      fehler(lambda: db.create_node("dinge", "d1", {"am": HEUTE.isoformat()})) is None)

db.create_node("dinge", "d2", {"wert": 1})
e = fehler(lambda: db.update_node("dinge", "d2", {"wert": HEUTE}))
check("Ein date in einer Änderung wird abgewiesen", ist(e, "NichtSpeicherbar"), repr(e))
check("… und der alte Wert bleibt im Speicher", db.get_node("dinge/d2") == {"wert": 1})
db = neu_oeffnen(db)
check("… und auf der Platte", db.get_node("dinge/d2") == {"wert": 1})

for name, wert in [("NaN", float("nan")), ("Unendlich", float("inf"))]:
    e = fehler(lambda: db.create_node("dinge", f"z_{name}", {"x": wert}))
    check(f"{name} wird abgewiesen (kein gültiges JSON)", ist(e, "NichtSpeicherbar"), repr(e))

e = fehler(lambda: db.create_node("dinge", "tupel", {"punkt": (1, 2)}))
check("Ein Tupel wird abgewiesen — es käme als Liste zurück",
      ist(e, "NichtSpeicherbar"), repr(e))
e = fehler(lambda: db.create_node("dinge", "zahlschluessel", {"stufen": {1: "a"}}))
check("Ein Zahlenschlüssel wird abgewiesen — er käme als Text zurück",
      ist(e, "NichtSpeicherbar"), repr(e))

check("Verschachtelte, gültige Werte gehen weiter durch",
      fehler(lambda: db.create_node("dinge", "tief", {
          "liste": [1, 2.5, None, True, "ä"], "karte": {"a": {"b": []}}})) is None)

# =========================================================================
print("--- Kanten ---")
# =========================================================================
pfad, db = frisch()
db.create_node("dinge", "a", {})
db.create_node("dinge", "b", {})
e = fehler(lambda: db.create_edge("dinge/a", "dinge/b", "gehoert_zu", meta={"am": HEUTE}))
check("Eine Kante mit date in den Metadaten wird abgewiesen",
      ist(e, "NichtSpeicherbar"), repr(e))
check("Die abgewiesene Kante steht nicht im Speicher",
      db.get_connected("dinge/a", rel_type="gehoert_zu") == [])
e = fehler(lambda: db.create_edge("dinge/a", "dinge/b", "gehoert_zu"))
check("Die Kantenart bleibt danach schreibbar", e is None, repr(e))
db = neu_oeffnen(db)
check("… und die neue Kante überlebt den Neustart",
      db.get_connected("dinge/a", rel_type="gehoert_zu") == ["dinge/b"])

# =========================================================================
print("--- Transaktion ---")
# =========================================================================
# Zwei Sammlungen, nicht zwei Knoten einer: beim Abschluss werden die
# Sammlungen in der Reihenfolge ihrer ersten Änderung geschrieben, die
# Knoten innerhalb einer Sammlung aber in Mengenreihenfolge — und die
# wechselt von Lauf zu Lauf. Mit einer Sammlung bestünde die Gegenprobe
# gegen 3.0.0 mal und mal nicht.
pfad, db = frisch()
db.create_node("konto", "a", {"stand": 100})
db.create_node("buchung", "b", {"stand": 0})


def umbuchen():
    with db.transaction():
        db.update_node("konto", "a", {"stand": 0})
        db.update_node("buchung", "b", {"stand": 100, "am": HEUTE})


fehler(umbuchen)
im_speicher = (db.get_node("konto/a"), db.get_node("buchung/b"))
neu = neu_oeffnen(db)
check("Eine Transaktion mit einem unspeicherbaren Wert hinterlässt nichts auf der Platte",
      neu.get_node("konto/a") == {"stand": 100} and neu.get_node("buchung/b") == {"stand": 0},
      f"a={neu.get_node('konto/a')} b={neu.get_node('buchung/b')}")
check("… und Speicher und Platte stimmen überein",
      im_speicher == (neu.get_node("konto/a"), neu.get_node("buchung/b")))

# =========================================================================
print("--- Namen von Sammlungen und Kantenarten ---")
# =========================================================================
pfad, db = frisch()
e = fehler(lambda: db.create_node("../../ausserhalb", "x", {}))
check("Ein Sammlungsname mit ../ wird abgewiesen", ist(e, "UngueltigerName"), repr(e))
check("UngueltigerName ist weiterhin ein ValueError", isinstance(e, ValueError))
check("… und legt nichts ausserhalb von datenbank/ an",
      sorted(os.listdir(pfad)) == sorted(os.listdir(frisch()[0])),
      str(sorted(os.listdir(pfad))))
check("… und keine leere Sammlung im Speicher",
      "../../ausserhalb" not in db.list_collections())

for name in ["a/b", "", "_intern", ".versteckt", "-strich", "x" * 101]:
    check(f"Sammlungsname {name[:12]!r} wird abgewiesen",
          ist(fehler(lambda: db.create_node(name, "x", {})), "UngueltigerName"))
for name in ["anlagen", "Räume", "gehört-zu", "v1.2", "a_b", "7tage"]:
    check(f"Sammlungsname {name!r} ist erlaubt",
          fehler(lambda: db.create_node(name, "x", {})) is None)

db.create_node("dinge", "a", {})
db.create_node("dinge", "b", {})
for art in ["teil/von", "..", "_intern", "a\\b"]:
    check(f"Kantenart {art!r} wird abgewiesen",
          ist(fehler(lambda: db.create_edge("dinge/a", "dinge/b", art)), "UngueltigerName"))
check("Kantenart 'gehört-zu' ist erlaubt",
      fehler(lambda: db.create_edge("dinge/a", "dinge/b", "gehört-zu")) is None)

# =========================================================================
print("--- Kennungen ---")
# =========================================================================
pfad, db = frisch()
e = fehler(lambda: db.create_node("dinge", 5, {}))
check("Eine Zahl als Kennung wird abgewiesen", ist(e, "UngueltigerName"), repr(e))
check("Eine leere Kennung wird abgewiesen",
      ist(fehler(lambda: db.create_node("dinge", "", {})), "UngueltigerName"))
# Bis Speicherform 2 war die Kennung ein Dateiname, und eine zu lange
# scheiterte erst beim Schreiben — der Knoten blieb im Speicher. Seit den
# Fächern ist sie ein Schlüssel in einer Datei: lang ist erlaubt, und sie
# muss den Neustart überstehen.
if getattr(fg, "SPEICHERFORM", 1) >= 3:
    lang = "z" * 1000
    angelegt = fehler(lambda: db.create_node("dinge", lang, {"v": 2})) is None
    db = neu_oeffnen(db)
    check("Eine sehr lange Kennung (1000 Zeichen) geht und übersteht den Neustart",
          angelegt and db.get_node(f"dinge/{lang}") == {"v": 2})
else:
    e = fehler(lambda: db.create_node("dinge", "z" * 300, {}))
    check("Eine zu lange Kennung wird abgewiesen, bevor geschrieben wird",
          ist(e, "UngueltigerName"), repr(e))
    check("… und steht nicht im Speicher", db.get_node("dinge/" + "z" * 300) is None)
check("Schrägstrich und Leerzeichen in Kennungen gehen weiter (kodiert)",
      fehler(lambda: db.create_node("dinge", "a/b c", {"v": 1})) is None
      and neu_oeffnen(db).get_node("dinge/a/b c") == {"v": 1})

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
