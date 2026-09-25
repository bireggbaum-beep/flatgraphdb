"""
Block 1 — Lesen: get_node und list_nodes, mit und ohne Kopie.

Gemessen wird, was VERTRAG.md Abschnitt 5 zusagt:
  - `get_node` wächst NICHT mit dem Bestand,
  - `list_nodes` wächst linear, weil jeder Eintrag tief kopiert wird,
  - `readonly=True` lässt die Kopie weg (dort: 3.37 ms → 0.35 ms bei 2000).

Die Vertragszahlen stammen vom 17.09.2026 und damit noch aus Speicherform 1.
Lesen geht in beiden Formen aus dem Arbeitsspeicher, sollte sich also nicht
verschoben haben. Genau das ist hier nachzuprüfen, nicht anzunehmen.
"""
import itertools
import random

from rahmen import SAMMLUNG, SAMEN, Bestand, knoten_id, messen

NAME = "lesen"


def lauf(fg, groessen):
    zeilen = []
    for anzahl in groessen:
        with Bestand(fg, anzahl) as db:
            # Zufällige Reihenfolge statt immer derselbe Knoten: ein einzelner
            # heisser Eintrag würde einen Cache-Vorteil messen, den ein echter
            # Zugriff über den ganzen Bestand nicht hat.
            rnd = random.Random(SAMEN)
            refs = [f"{SAMMLUNG}/{knoten_id(rnd.randrange(anzahl))}" for _ in range(1000)]
            naechste = itertools.cycle(refs).__next__

            ist_knoten = lambda k: isinstance(k, dict) and "name" in k
            ist_alles = lambda d: isinstance(d, dict) and len(d) == anzahl

            zeilen.append(messen("get_node", anzahl,
                                 lambda: db.get_node(naechste()), ist_knoten))
            zeilen.append(messen("get_node readonly", anzahl,
                                 lambda: db.get_node(naechste(), readonly=True), ist_knoten))
            zeilen.append(messen("list_nodes", anzahl,
                                 lambda: db.list_nodes(SAMMLUNG), ist_alles))
            zeilen.append(messen("list_nodes readonly", anzahl,
                                 lambda: db.list_nodes(SAMMLUNG, readonly=True), ist_alles))
    return zeilen
