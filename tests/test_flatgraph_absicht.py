"""
Eine Transaktion steht nach einem Absturz ganz oder gar nicht auf der Platte.

Bis zur Absichtsdatei schrieb der Abschluss einer Transaktion Fach für
Fach. Brach er dazwischen ab (Strom, Absturz, volle Platte), stand nach dem
Neustart die Hälfte da: bei einer Umbuchung das Geld auf dem einen Konto
abgebucht, auf dem anderen nie angekommen. VERTRAG.md 2.3 nannte das als
Lücke.

Geprüft wird mit einem simulierten Absturz: ab dem n-ten Aufruf von
`os.replace` bzw. `os.remove` bricht alles ab, mit einer Ausnahme, die
flatgraph nicht abfängt — wie ein Prozess, der mitten im Schreiben stirbt.
Danach wird der Bestand neu geöffnet. Für JEDES n muss er ganz alt oder ganz
neu sein, und ab dem Festschreiben der Absicht ganz neu.

Gegenprobe: gegen die Fassung vor der Absichtsdatei fällt „ganz alt oder
ganz neu“ beim zweiten Umbenennen.

Importiert `pdms` nicht.
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


class Absturz(BaseException):
    """Kein Exception: flatgraph soll ihn nicht abfangen und aufräumen
    können, genau wie bei einem echten Absturz."""


WURZEL = tempfile.mkdtemp(prefix="fg_absicht_")
ECHT = {"replace": os.replace, "remove": os.remove}


class Stolperdraht:
    """Ab dem n-ten Aufruf von os.replace/os.remove: `fehler` werfen."""

    def __init__(self, ab, fehler=Absturz, nur_einmal=False):
        self.ab, self.fehler, self.nur_einmal = ab, fehler, nur_einmal
        self.aufrufe = 0

    def _zaehlen(self, name):
        def aufruf(*a, **kw):
            self.aufrufe += 1
            if self.aufrufe >= self.ab and not (self.nur_einmal and self.aufrufe > self.ab):
                raise self.fehler(f"{name} Nr. {self.aufrufe}")
            return ECHT[name](*a, **kw)
        return aufruf

    def __enter__(self):
        os.replace, os.remove = self._zaehlen("replace"), self._zaehlen("remove")
        return self

    def __exit__(self, *_):
        os.replace, os.remove = ECHT["replace"], ECHT["remove"]


def bestand():
    """Zwei Konten in zwei Sammlungen und eine Kante: eine Umbuchung
    berührt damit mindestens drei Fächer."""
    pfad = tempfile.mkdtemp(dir=WURZEL)
    db = fg.FlatGraphDB(pfad)
    db.create_node("konto", "a", {"stand": 100})
    db.create_node("buchung", "b", {"stand": 0})
    return db


def umbuchen(db):
    with db.transaction():
        db.update_node("konto", "a", {"stand": 0})
        db.update_node("buchung", "b", {"stand": 100})
        db.create_edge("konto/a", "buchung/b", "gebucht")


def zustand(db):
    return (db.get_node("konto/a")["stand"], db.get_node("buchung/b")["stand"],
            len(db.get_connected("konto/a", rel_type="gebucht")))


ALT, NEU = (100, 0, 0), (0, 100, 1)

# =========================================================================
print("--- Absturz an jeder Stelle des Abschlusses ---")
# =========================================================================
# Wie viele Aufrufe braucht ein ungestörter Abschluss?
db = bestand()
with Stolperdraht(ab=10 ** 9) as zaehler:
    umbuchen(db)
schritte = zaehler.aufrufe
db.close()

ergebnisse = []
for n in range(1, schritte + 1):
    db = bestand()
    try:
        with Stolperdraht(ab=n):
            umbuchen(db)
    except Absturz:
        pass
    db = neu_oeffnen(db)
    ergebnisse.append(zustand(db))
    db.close()

check(f"Nach einem Absturz an jeder der {schritte} Stellen: ganz alt oder ganz neu",
      all(z in (ALT, NEU) for z in ergebnisse), str(ergebnisse))
check("… und nicht immer alt: ab dem Festschreiben gilt die Transaktion",
      ergebnisse and ergebnisse[-1] == NEU, str(ergebnisse))
check("… und nicht immer neu: vor dem Festschreiben gilt sie nicht",
      ergebnisse and ergebnisse[0] == ALT, str(ergebnisse))

# Nach dem Nachholen darf nichts herumliegen.
db = bestand()
try:
    with Stolperdraht(ab=schritte):
        umbuchen(db)
except Absturz:
    pass
db = neu_oeffnen(db)
reste = [n for _, _, namen in os.walk(db.root) for n in namen
         if n.endswith((".neu", ".tmp")) or n.startswith("_absicht")]
check("Nach dem Nachholen liegen weder Absicht noch .neu-Dateien herum", reste == [], str(reste))
db.close()

# =========================================================================
print("--- Ein Fach allein braucht keine Absicht ---")
# =========================================================================
db = bestand()
with Stolperdraht(ab=10 ** 9) as zaehler:
    db.update_node("konto", "a", {"stand": 1})
check("Ein Schreibvorgang auf ein Fach kostet ein Umbenennen, wie vorher",
      zaehler.aufrufe == 1, str(zaehler.aufrufe))
db.close()

# =========================================================================
print("--- Fehler statt Absturz ---")
# =========================================================================
# Vor dem Festschreiben: der erste Aufruf ist das Schreiben der Absicht.
db = bestand()
try:
    with Stolperdraht(ab=1, fehler=OSError):
        umbuchen(db)
    e = None
except OSError as fehler:
    e = fehler
check("Scheitert das Festschreiben, wirft die Transaktion", isinstance(e, OSError), repr(e))
check("… und der Speicher zeigt den alten Stand", zustand(db) == ALT, str(zustand(db)))
umbuchen(db)
db = neu_oeffnen(db)
check("… danach geht dieselbe Transaktion durch und übersteht den Neustart",
      zustand(db) == NEU, str(zustand(db)))

# Die Zuordnung Kennung -> Fach muss mit zurückgehen. Das Planen nimmt eine
# gelöschte Kante schon aus ihrem Fach; bleibt das nach dem Fehlschlag
# stehen, weiss die Instanz nicht mehr, wo die zurückgeholte Kante liegt —
# das nächste Löschen schreibt ihr Fach nicht, und sie ersteht beim
# Neustart wieder auf.
kante = db.create_edge("konto/a", "buchung/b", "verweis")


def loeschen():
    with db.transaction():
        db.update_node("konto", "a", {"stand": 50})
        db.delete_edge(kante)


try:
    with Stolperdraht(ab=1, fehler=OSError):
        loeschen()
except OSError:
    pass
loeschen()
db = neu_oeffnen(db)
check("Eine Kante, deren Löschen scheiterte, bleibt nach dem zweiten Versuch gelöscht",
      db.get_connected("konto/a", rel_type="verweis") == [],
      str(db.get_connected("konto/a", rel_type="verweis")))
db.close()

# Nach dem Festschreiben: das erste Umbenennen eines Fachs scheitert einmal.
db = bestand()
try:
    with Stolperdraht(ab=2, fehler=OSError, nur_einmal=True):
        umbuchen(db)
    e = None
except Exception as fehler:
    e = fehler
check("Scheitert das Umbenennen nach dem Festschreiben: AbschlussHaengt",
      type(e).__name__ == "AbschlussHaengt", repr(e))
check("… ein OSError, wie jeder Plattenfehler", isinstance(e, OSError))
check("… und der Speicher zeigt den NEUEN Stand — er gilt", zustand(db) == NEU, str(zustand(db)))
db.create_node("konto", "c", {"stand": 5})
check("Der nächste Schreibvorgang holt den Rest nach",
      not os.path.exists(os.path.join(db.root, "datenbank", "_absicht.json")))
db = neu_oeffnen(db)
check("… und nach dem Neustart steht alles da",
      zustand(db) == NEU and db.get_node("konto/c") == {"stand": 5})
db.close()

# =========================================================================
print("--- Was beim Öffnen herumliegt ---")
# =========================================================================
db = bestand()
wurzel = db.root
db.close()
fach = next(os.path.join(v, n) for v, _, namen in os.walk(os.path.join(wurzel, "datenbank", "nodes"))
            for n in namen if n.startswith("fach_") and "konto" in v)
with open(fach + ".neu", "w", encoding="utf-8") as f:
    json.dump({"a": {"stand": 999}}, f)
db = fg.FlatGraphDB(wurzel)
check("Eine .neu ohne Absicht gilt nicht", db.get_node("konto/a") == {"stand": 100},
      str(db.get_node("konto/a")))
check("… und wird weggeräumt", not os.path.exists(fach + ".neu"))
db.close()

absicht = os.path.join(wurzel, "datenbank", "_absicht.json")
fremd = os.path.join(wurzel, "wichtig.txt")
with open(fremd, "w") as f:
    f.write("nicht anfassen")
with open(absicht, "w", encoding="utf-8") as f:
    json.dump({"absicht": 1, "ersetzen": [], "entfernen": ["wichtig.txt"]}, f)
try:
    fg.FlatGraphDB(wurzel)
    e = None
except Exception as fehler:
    e = fehler
check("Eine Absicht, die ausserhalb der Fächer löschen will, wird abgewiesen",
      type(e).__name__ == "DateiKaputt", repr(e))
check("… und die Datei bleibt", os.path.exists(fremd))

with open(absicht, "w", encoding="utf-8") as f:
    f.write('{"absicht": 1, "ersetzen": [')
try:
    fg.FlatGraphDB(wurzel)
    e = None
except Exception as fehler:
    e = fehler
check("Eine kaputte Absichtsdatei wird gemeldet, nicht übergangen",
      type(e).__name__ == "DateiKaputt", repr(e))
os.remove(absicht)
check("Ohne sie öffnet der Bestand wieder, im alten Stand",
      fg.FlatGraphDB(wurzel).get_node("konto/a") == {"stand": 100})

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
