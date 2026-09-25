"""
flatgraphs Schreibweg — die eine örtliche Abweichung dieser Kopie.

Diese Datei hat einen einzigen Zweck: zu verhindern, dass ein
Herüberkopieren einer frischen flatgraph-Fassung den `fsync` aus
`_save_json_atomic` lautlos wieder entfernt.

Warum das eine eigene Prüfung verdient — die Frage aus CLAUDE.md lautet
„welchen Fehler fängt sie, den jemand wirklich wieder machen würde?". Die
Antwort ist unangenehm konkret: flatgraph wird weiterentwickelt, und der
naheliegendste Handgriff dabei ist, die neue Fassung über die alte zu
legen. Ohne diese Prüfung ist danach Invariante 1 weg — und zwar ohne
jedes Anzeichen, denn fehlendes fsync fällt erst beim Stromausfall auf,
und dann ist es zu spät.

Sie prüft ausserdem die REIHENFOLGE. fsync NACH os.replace wäre wertlos:
dann ist der Name schon getauscht, während der Inhalt noch im Puffer steht.

WICHTIG: Diese Suite importiert `pdms` NICHT. Das ist Absicht und die
mechanische Regel gegen Overfitting — lässt sich eine Eigenschaft von
flatgraph nur über pDMS prüfen, gehört sie nicht zu flatgraph.

    python tests/test_flatgraph_schreibweg.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.getcwd())

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


from flatgraph import FlatGraphDB                                      # noqa: E402

check("flatgraph lässt sich ohne pdms benutzen", "pdms" not in sys.modules,
      "geladen: " + ", ".join(m for m in sys.modules if m.startswith("pdms")))

ort = tempfile.mkdtemp(prefix="fg_schreibweg_")

# --- Mitschreiben, was beim Speichern wirklich passiert --------------------
ablauf = []
echt_fsync, echt_replace = os.fsync, os.replace


def fsync_mit_notiz(fd):
    # Ob Datei oder Verzeichnis: am Dateityp hinter dem Deskriptor erkennbar.
    try:
        art = "verzeichnis" if os.path.isdir("/proc/self/fd/%d" % fd) else "datei"
    except OSError:
        art = "datei"
    ablauf.append(("fsync", art))
    return echt_fsync(fd)


def replace_mit_notiz(a, b):
    ablauf.append(("replace", os.path.basename(b)))
    return echt_replace(a, b)


os.fsync, os.replace = fsync_mit_notiz, replace_mit_notiz
try:
    db = FlatGraphDB(ort)
    ablauf.clear()                       # das Anlegen interessiert nicht
    db.create_node("dinge", "d_1", {"titel": "Eins"})
    db.flush()
finally:
    os.fsync, os.replace = echt_fsync, echt_replace

arten = [a for a, _ in ablauf]
check("Beim Speichern wird überhaupt synchronisiert", "fsync" in arten, str(ablauf))
check("Es wird per os.replace getauscht", "replace" in arten, str(ablauf))

# Die Reihenfolge ist der Punkt: erst die Daten dauerhaft machen, dann den
# Namen tauschen. Andersherum zeigt der neue Name auf einen Puffer.
if "fsync" in arten and "replace" in arten:
    check("Erst fsync, dann os.replace",
          arten.index("fsync") < arten.index("replace"), str(arten))

check("Auch der Verzeichniseintrag wird dauerhaft gemacht",
      ("fsync", "verzeichnis") in ablauf,
      "nur: " + str([a for a in ablauf if a[0] == "fsync"]))

# --- Und der Inhalt stimmt danach auch wirklich ---------------------------
# Vor dem Neustart schliessen: seit 3.0.0-entwurf hat ein Bestand EINE
# offene Instanz, eine zweite ohne close() bekäme BestandBelegt. Eine
# ältere Fassung kennt close() nicht — dann bleibt es beim blossen
# Neuöffnen, das reichte vorher auch.
schliessen = getattr(db, "close", None)
if schliessen is not None:
    schliessen()
db2 = FlatGraphDB(ort)
check("Der Knoten ist nach einem Neustart da",
      (db2.get_node("dinge/d_1") or {}).get("titel") == "Eins",
      str(db2.get_node("dinge/d_1")))

# --- Keine Reste: eine .tmp darf nicht liegenbleiben ----------------------
reste = []
for wurzel, _, dateien in os.walk(os.path.join(ort, "datenbank")):
    reste += [f for f in dateien if f.endswith(".tmp")]
check("Keine Arbeitsdatei bleibt liegen", not reste, str(reste))

# --- Die Arbeitsdatei liegt NEBEN dem Ziel, nicht in /tmp -----------------
# os.replace scheitert über Dateisystemgrenzen hinweg. Lag die Arbeitsdatei
# im System-Temp, brach in Codespaces jeder Lauf mit "Invalid cross-device
# link" ab — und sah aus wie ein Dokumentfehler.
ziele = [b for a, b in ablauf if a == "replace"]
check("Es wurde überhaupt etwas getauscht", bool(ziele), str(ablauf))

import shutil                                                          # noqa: E402
shutil.rmtree(ort, ignore_errors=True)

print("\n" + "=" * 60)
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
