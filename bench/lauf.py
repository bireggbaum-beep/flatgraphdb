"""
Leistungsmessung für flatgraph — ohne pDMS, nur Standardbibliothek.

    python bench/lauf.py                    alle Blöcke
    python bench/lauf.py lesen              nur einen Block
    python bench/lauf.py --groessen 500,2000,10000
    python bench/lauf.py --json ergebnis.json

    FLATGRAPH_DATEI=/pfad/zu/anderer/flatgraph.py python bench/lauf.py

Aus dem Wurzelverzeichnis des Repos aufrufen (wie die Prüfsuiten), sonst
findet `tests/flatgraph_laden.py` die Fassung nicht.

Welche Blöcke es gibt und welche noch fehlen: bench/README.md.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import block_lesen                                                     # noqa: E402
import block_oeffnen                                                   # noqa: E402
import block_schreiben                                                 # noqa: E402
import block_transaktion                                               # noqa: E402
import block_nachbarschaft                                             # noqa: E402
import block_suche                                                     # noqa: E402
from rahmen import lade_fassung, tabelle, umgebung                     # noqa: E402

# Neue Blöcke werden hier eingetragen, in der Reihenfolge des README.
BLOECKE = {m.NAME: m for m in [block_lesen, block_oeffnen, block_schreiben,
                                     block_transaktion, block_nachbarschaft, block_suche]}


def main():
    p = argparse.ArgumentParser(description="flatgraph-Leistungsmessung")
    p.add_argument("bloecke", nargs="*",
                   help=f"welche Blöcke: {', '.join(BLOECKE)} (Vorgabe: alle)")
    p.add_argument("--groessen", default="500,2000",
                   help="Bestandsgrössen, kommagetrennt (Vorgabe: 500,2000 wie VERTRAG.md)")
    p.add_argument("--json", metavar="DATEI", help="Ergebnis zusätzlich als JSON ablegen")
    a = p.parse_args()

    unbekannt = [b for b in a.bloecke if b not in BLOECKE]
    if unbekannt:
        p.error(f"unbekannter Block: {', '.join(unbekannt)} — vorhanden: {', '.join(BLOECKE)}")
    groessen = [int(g) for g in a.groessen.split(",")]
    gewaehlt = a.bloecke or list(BLOECKE)

    fg = lade_fassung()
    umg = umgebung(fg)
    for k, v in umg.items():
        print(f"{k:<13} {v}")

    alle = []
    for name in gewaehlt:
        print(f"\n== {name}")
        zeilen = BLOECKE[name].lauf(fg, groessen)
        for z in zeilen:
            z["block"] = name
        tabelle(zeilen)
        alle.extend(zeilen)

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"umgebung": umg, "ergebnisse": alle}, f, indent=2, ensure_ascii=False)
        print(f"\nabgelegt: {a.json}")


if __name__ == "__main__":
    main()
