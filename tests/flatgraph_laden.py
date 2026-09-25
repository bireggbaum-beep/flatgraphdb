"""
Welche flatgraph-Fassung geprüft wird — eine Stelle für alle Suiten.

Vorgabe ist `flatgraph.py` im Repo-Wurzelverzeichnis. Auf eine andere
Kopie zeigen (z.B. eine lokale Arbeitskopie vergleichen):

    FLATGRAPH_DATEI=/pfad/zu/anderer/flatgraph.py python tests/test_flatgraph_graph.py

Geladen wird über importlib und nicht mit `import flatgraph`, damit
`FLATGRAPH_DATEI` zuverlässig zieht statt vom normalen Modul-Cache
überdeckt zu werden.
"""
import importlib.util
import os
import sys

VORGABE = "flatgraph.py"


def lade():
    pfad = os.environ.get("FLATGRAPH_DATEI", VORGABE)
    if not os.path.isfile(pfad):
        raise SystemExit(f"Keine flatgraph-Fassung unter {pfad}")
    spec = importlib.util.spec_from_file_location("flatgraph_pruefling", pfad)
    modul = importlib.util.module_from_spec(spec)
    sys.modules["flatgraph_pruefling"] = modul
    spec.loader.exec_module(modul)
    modul.__pfad__ = pfad
    return modul


def neu_oeffnen(db, klasse=None, **kw):
    """Ein Neustart: die alte Instanz schliessen, dann denselben Bestand neu
    öffnen.

    Seit 3.0.0-entwurf hat ein Bestand EINE offene Instanz; eine zweite
    bekommt `BestandBelegt`, solange die erste lebt. Vorher liessen die
    Suiten die alte Instanz einfach liegen. Eine ältere Fassung kennt
    `close()` nicht — dann wird nur neu geöffnet, damit dieselbe Suite sie
    weiter charakterisieren kann.

    Die alte Instanz ist danach geschlossen: wer weiterarbeiten will, nimmt
    die zurückgegebene.
    """
    schliessen = getattr(db, "close", None)
    if schliessen is not None:
        schliessen()
    return (klasse or type(db))(db.root, **kw)


def platte(wurzel, art="nodes"):
    """Was auf der Platte steht, unabhängig von der Speicherform:
    {sammlung bzw. kantenart: {kennung: daten}}.

    Die Suiten prüfen, was WIRKLICH geschrieben wurde, nicht was der
    Prüfling im Speicher hält. Seit Speicherform 3 liegt ein Knoten aber
    nicht mehr in `<kennung>.json`, sondern in einem Fach. Diese Hilfe
    liest jede Form, damit dieselbe Suite gegen alte und neue Fassungen
    läuft:

        Form 2  nodes/<sammlung>/<kennung>.json    edges/<art>.json
        Form 3  nodes/<sammlung>/fach_000001.json  edges/<art>/fach_000001.json
    """
    import json
    import re
    import urllib.parse

    basis = os.path.join(wurzel, "datenbank", art)
    ergebnis = {}
    if not os.path.isdir(basis):
        return ergebnis
    for eintrag in sorted(os.listdir(basis)):
        pfad = os.path.join(basis, eintrag)
        if os.path.isdir(pfad):
            name, inhalt = urllib.parse.unquote(eintrag), {}
            for datei in sorted(os.listdir(pfad)):
                if not datei.endswith(".json"):
                    continue
                with open(os.path.join(pfad, datei), encoding="utf-8") as f:
                    daten = json.load(f)
                if re.fullmatch(r"fach_\d+\.json", datei):
                    inhalt.update(daten)
                else:
                    inhalt[urllib.parse.unquote(datei[:-5])] = daten
            ergebnis[name] = inhalt
        elif eintrag.endswith(".json") and art == "edges":
            with open(pfad, encoding="utf-8") as f:
                ergebnis[eintrag[:-5]] = json.load(f)
    return ergebnis
