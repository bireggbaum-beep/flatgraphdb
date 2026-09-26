"""
Was 2.1 mitbringt — und was nie jemand geprüft hat.

Der Stand vom 07.05.2026 brachte 13 neue Funktionen und 19 geänderte mit.
Im flatgraph-Repo gibt es dazu **keine einzige Prüfung**. Diese Suite ist
die erste, die diese Fläche anfasst.

Sie prüft, was pDMS nicht braucht und deshalb auch nicht abdeckt:
Aufzählungen und Referenzen im Schema, Kantenbedingungen, Import und
Export, ausgelagerte Langtexte, und die kopierfreien Lesewege.

Importiert `pdms` nicht.

    python tests/test_flatgraph_neuerungen.py
"""
import csv
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
    try:
        return fn()
    except Exception as e:
        return type(e).__name__


WURZEL = tempfile.mkdtemp(prefix="fg_neu_")


def frisch(**kw):
    return fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), **kw)


fehlt = [n for n in ("export_json", "import_json", "export_csv", "import_csv",
                     "get_node_full") if not hasattr(fg.FlatGraphDB, n)]
if fehlt:
    print(f"Diese Fassung kennt {', '.join(fehlt)} nicht — "
          f"{fg.__pfad__} ({fg.__version__})")
    print("0/0 Checks bestanden")
    sys.exit(0)

# =========================================================================
# Schema: Aufzählungen und Referenzen
# =========================================================================
print("--- Schema ---")
db = frisch(schemas={
    "auftraege": {
        "status": {"type": str, "options": ["offen", "laeuft", "fertig"]},
        "marken": {"type": list, "options": ["eilig", "extern"]},
        "anlage": {"type": "link", "target": "anlagen"},
    },
})
db.create_node("anlagen", "A1", {"name": "Presse"})

check("Ein erlaubter Wert geht durch",
      bool(db.create_node("auftraege", "a1",
                          {"status": "offen", "marken": ["eilig"], "anlage": "anlagen/A1"})))
check("Ein unerlaubter Wert wird abgewiesen",
      wirft(lambda: db.create_node("auftraege", "a2",
                                   {"status": "erfunden", "marken": []})) == "ValueError")
check("Auch in einer Liste wird jeder Eintrag geprüft",
      wirft(lambda: db.create_node("auftraege", "a3",
                                   {"status": "offen", "marken": ["eilig", "quatsch"]}))
      == "ValueError")
check("Der falsche Typ wird als Typfehler gemeldet",
      wirft(lambda: db.create_node("auftraege", "a4",
                                   {"status": 42, "marken": []})) == "TypeError")
check("Ein Pflichtfeld fehlt",
      wirft(lambda: db.create_node("auftraege", "a5", {"marken": []})) == "ValueError")

check("Eine Referenz auf einen vorhandenen Knoten geht",
      bool(db.create_node("auftraege", "a6",
                          {"status": "offen", "marken": [], "anlage": "anlagen/A1"})))
check("Eine Referenz ins Leere wird abgewiesen",
      wirft(lambda: db.create_node("auftraege", "a7",
                                   {"status": "offen", "marken": [],
                                    "anlage": "anlagen/gibtsnicht"})) == "ValueError")
check("Eine Referenz in die falsche Sammlung ebenso",
      wirft(lambda: db.create_node("auftraege", "a8",
                                   {"status": "offen", "marken": [],
                                    "anlage": "auftraege/a1"})) == "ValueError")
check("Eine Referenz darf fehlen — sie ist freiwillig",
      bool(db.create_node("auftraege", "a9", {"status": "offen", "marken": []})))

# Die Prüfung muss auch beim ÄNDERN greifen, nicht nur beim Anlegen —
# sonst ist sie nach dem ersten Speichern wirkungslos.
check("Beim Ändern greift die Aufzählung ebenfalls",
      wirft(lambda: db.update_node("auftraege", "a1", {"status": "erfunden"})) == "ValueError")
check("Und ein erlaubter Wert geht durch",
      db.update_node("auftraege", "a1", {"status": "fertig"}) is True)

# =========================================================================
# Kantenbedingungen
# =========================================================================
print("--- Kantenbedingungen ---")
db2 = frisch(edge_constraints={"steht_in": [("anlagen", "raeume")]})
db2.create_node("anlagen", "A1", {"n": 1})
db2.create_node("raeume", "R1", {"n": 1})
db2.create_node("personen", "P1", {"n": 1})

check("Eine erlaubte Paarung geht", bool(db2.create_edge("anlagen/A1", "raeume/R1", "steht_in")))
check("Eine unerlaubte Paarung wird abgewiesen",
      wirft(lambda: db2.create_edge("personen/P1", "raeume/R1", "steht_in")) == "ValueError")
check("Eine ungeregelte Kantenart bleibt frei",
      bool(db2.create_edge("personen/P1", "raeume/R1", "arbeitet_in")))

# =========================================================================
# Kopierfreies Lesen
# =========================================================================
print("--- readonly ---")
db3 = frisch()
db3.create_node("k", "k_1", {"liste": [1, 2, 3]})

kopie = db3.list_nodes("k")
kopie["k_1"]["liste"].append(99)
check("Die Vorgabe gibt eine Kopie — Ändern trifft den Bestand nicht",
      db3.get_node("k/k_1")["liste"] == [1, 2, 3],
      str(db3.get_node("k/k_1")["liste"]))

# Auch in die andere Richtung: was der Aufrufer nach dem Anlegen an seinen
# eigenen Daten ändert, darf den Bestand nicht treffen — auch nicht über
# eine Unterklasse von dict, die eine auf JSON-Typen verkürzte Kopie sonst
# durchreichen würde.
from collections import OrderedDict                                    # noqa: E402
eigene = {"liste": [1], "karte": OrderedDict(tief=[1])}
db3.create_node("k", "k_eigen", eigene)
eigene["liste"].append(99)
eigene["karte"]["tief"].append(99)
check("Was der Aufrufer danach an seinen Daten ändert, trifft den Bestand nicht",
      db3.get_node("k/k_eigen") == {"liste": [1], "karte": {"tief": [1]}},
      str(db3.get_node("k/k_eigen")))

roh = db3.list_nodes("k", readonly=True)
check("readonly gibt dieselben Werte", roh["k_1"]["liste"] == [1, 2, 3])
check("Aber als Verweis in den Zwischenspeicher, nicht als Kopie",
      roh["k_1"] is db3._cache["nodes"]["k"]["k_1"])
check("find_nodes kennt readonly auch",
      db3.find_nodes("k", {}, readonly=True)["k_1"]
      is db3._cache["nodes"]["k"]["k_1"])

# =========================================================================
# Import und Export
# =========================================================================
print("--- Import/Export ---")
db4 = frisch()
for i in range(3):
    db4.create_node("k", f"k_{i}", {"name": f"Nummer {i}", "zahl": i})

pfad_json = os.path.join(WURZEL, "aus.json")
db4.export_json("k", pfad_json)
check("Export nach JSON legt eine Datei an", os.path.isfile(pfad_json))
inhalt = json.load(open(pfad_json, encoding="utf-8"))
check("Und sie enthält alle Knoten", len(inhalt) == 3, str(len(inhalt)))

check("Die Kennung ist der Schlüssel, nicht ein Feld",
      set(inhalt) == {"k_0", "k_1", "k_2"}, str(sorted(inhalt)))

db5 = frisch()
n = db5.import_json("k", pfad_json, id_field=None)
check("Import aus JSON legt alle Knoten an", len(db5.list_nodes("k")) == 3,
      f"{n!r}, {sorted(db5.list_nodes('k'))}")
check("Und die Werte stimmen",
      db5.get_node("k/k_2") == {"name": "Nummer 2", "zahl": 2},
      str(db5.get_node("k/k_2")))

# Zweimal dasselbe importieren darf nicht stillschweigend ueberschreiben.
check("Ein zweiter Import meldet den Zusammenstoss",
      wirft(lambda: db5.import_json("k", pfad_json, id_field=None)) == "KeyError")
check("'skip' laesst das Vorhandene stehen",
      db5.import_json("k", pfad_json, id_field=None, on_conflict="skip") is not None
      and len(db5.list_nodes("k")) == 3)
db5.update_node("k", "k_0", {"name": "geaendert"})
db5.import_json("k", pfad_json, id_field=None, on_conflict="overwrite")
check("'overwrite' setzt zurueck",
      db5.get_node("k/k_0")["name"] == "Nummer 0",
      str(db5.get_node("k/k_0")))

pfad_csv = os.path.join(WURZEL, "aus.csv")
db4.export_csv("k", pfad_csv)
check("Export nach CSV legt eine Datei an", os.path.isfile(pfad_csv))
with open(pfad_csv, encoding="utf-8") as f:
    zeilen = list(csv.DictReader(f))
check("Die CSV hat eine Zeile je Knoten", len(zeilen) == 3, str(len(zeilen)))
check("Und die Felder als Spalten",
      "name" in zeilen[0] and "zahl" in zeilen[0], str(list(zeilen[0].keys())))

pfad_teil = os.path.join(WURZEL, "teil.csv")
db4.export_csv("k", pfad_teil, fields=["name"])
with open(pfad_teil, encoding="utf-8") as f:
    kopf = next(csv.reader(f))
check("Eine Feldauswahl wird eingehalten", "zahl" not in kopf, str(kopf))

# =========================================================================
# Ausgelagerte Langtexte
# =========================================================================
print("--- vault_text ---")
db6 = frisch(longtext_threshold=100)
langer = "x" * 500
db6.create_node("k", "k_1", {"kurz": "ok", "lang": langer})

vt = os.path.join(db6.root, "vault_text")
dateien = os.listdir(vt) if os.path.isdir(vt) else []
check("Ein langes Feld landet in einer eigenen Datei", len(dateien) == 1, str(dateien))
check("Ein kurzes nicht", all("kurz" not in d for d in dateien), str(dateien))

check("Im Knoten selbst steht nur ein Verweis",
      db6.get_node_raw("k/k_1")["lang"].startswith("@vault_text/"),
      str(db6.get_node_raw("k/k_1")["lang"])[:50])

# Die scharfe Kante: get_node loest NICHT auf, get_node_full schon. Das ist
# Absicht — Aufloesen heisst ein Dateizugriff je Feld, und den waehlt der
# Aufrufer. Aber wer es nicht weiss, bekommt eine Zeichenkette mit einem
# Pfad darin und haelt sie fuer den Inhalt.
check("get_node liefert den Verweis, nicht den Text",
      db6.get_node("k/k_1")["lang"].startswith("@vault_text/"))
check("get_node_full liefert den Text",
      db6.get_node_full("k/k_1")["lang"] == langer,
      str(db6.get_node_full("k/k_1")["lang"])[:40])
check("Der kurze Wert bleibt unberührt",
      db6.get_node_full("k/k_1")["kurz"] == "ok")

db7 = neu_oeffnen(db6, longtext_threshold=100)
check("Auch nach einem Neustart löst get_node_full auf",
      db7.get_node_full("k/k_1")["lang"] == langer)
check("Auf einen fehlenden Knoten bleibt get_node_full None",
      db7.get_node_full("k/weg") is None)

# Ohne Schwelle passiert nichts — das ist die Vorgabe, und deshalb ist
# die Speicherform mit und ohne 2.1 dieselbe.
db8 = frisch()
db8.create_node("k", "k_1", {"lang": langer})
vt8 = os.path.join(db8.root, "vault_text")
check("Ohne eingeschaltete Schwelle wird nichts ausgelagert",
      not os.path.isdir(vt8) or not os.listdir(vt8),
      str(os.listdir(vt8)) if os.path.isdir(vt8) else "kein Verzeichnis")

shutil.rmtree(WURZEL, ignore_errors=True)

print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
