"""
Block 5 — Nachbarschaft: was kosten die Graph-Zugriffe, und wie hängen sie
von der Dichte ab?

Gemessen wird, worauf die geplanten Graphfunktionen (Mustersuche, Verbindung
zeigen, mitgedachte Beziehungen, Regeln — Issue #33) aufbauen:

  get_connected           Nachbarn eines Knotens, ausgehend
  get_connected_edges     dieselben, mit den Kanten
  traverse, Tiefe 2 / 3   Breitensuche über mehrere Stufen
  traverse, unbegrenzt    bis alles Erreichbare gefunden ist
  collect_related         eine Kette aus zwei Schritten
  list_edges              alle Kanten einer Art

Dichte heisst Kanten je Knoten (1, 4, 16), zu zufälligen Zielen. Bei
Nachbarschaft zählt vor allem sie: ein Knoten mit 16 Kanten hat in Tiefe 2
schon bis zu 256 Nachbarn.

Gegenprobe: jedes Ergebnis wird einmal gegen eine eigene Rechnung aus
`list_edges` geprüft — ohne flatgraphs Nachbarschaftsindex. Ein Index, der
Kanten verschwiege, wäre verdächtig schnell.
"""
import random

from rahmen import KANTENART, SAMMLUNG, SAMEN, Bestand, knoten_id, messen

NAME = "nachbarschaft"

DICHTEN = (1, 4, 16)
STICHPROBEN = 7


def _nachbarn_aus_kanten(db):
    """Die Wahrheit: ausgehende Nachbarn, direkt aus den Kanten gerechnet."""
    aus = {}
    for kante in db.list_edges(KANTENART).values():
        aus.setdefault(kante["source"], []).append(kante["target"])
    return aus


def _bfs(aus, start, tiefe):
    gesehen, stufe, ergebnis = {start}, [start], set()
    schritt = 0
    while stufe and (tiefe is None or schritt < tiefe):
        naechste = []
        for k in stufe:
            for z in aus.get(k, ()):
                if z not in gesehen:
                    gesehen.add(z)
                    naechste.append(z)
                    ergebnis.add(z)
        stufe, schritt = naechste, schritt + 1
    return ergebnis


def lauf(fg, groessen):
    zeilen = []
    for anzahl in groessen:
        for dichte in DICHTEN:
            with Bestand(fg, anzahl, kanten_je_knoten=dichte) as db:
                aus = _nachbarn_aus_kanten(db)
                rnd = random.Random(SAMEN)
                start = f"{SAMMLUNG}/{knoten_id(rnd.randrange(anzahl))}"
                name = lambda text: f"{text} ({dichte}K/Kn)"

                soll_1 = sorted(aus.get(start, []))
                zeilen.append(messen(
                    name("get_connected"), anzahl,
                    lambda: db.get_connected(start, "out", KANTENART),
                    lambda r: sorted(r) == soll_1, STICHPROBEN))
                zeilen.append(messen(
                    name("get_connected_edges"), anzahl,
                    lambda: db.get_connected_edges(start, "out", KANTENART),
                    lambda r: sorted(e["target"] for _, e in r) == soll_1, STICHPROBEN))

                for tiefe in (2, 3, None):
                    soll = _bfs(aus, start, tiefe)
                    zeilen.append(messen(
                        name(f"traverse, Tiefe {tiefe or 'offen'}"), anzahl,
                        lambda t=tiefe: db.traverse(start, KANTENART, "out", max_depth=t),
                        lambda r, s=soll: set(r) == s, STICHPROBEN))

                soll_kette = set()
                for z in aus.get(start, []):
                    soll_kette.update(aus.get(z, []))
                zeilen.append(messen(
                    name("collect_related 2 Stufen"), anzahl,
                    lambda: db.collect_related(start, [KANTENART, KANTENART]),
                    # collect_related nimmt die Startstufe in die letzte
                    # Stufe mit auf (siehe Docstring dort) — deshalb die
                    # direkten Nachbarn dazu.
                    lambda r: set(r) == soll_kette | set(aus.get(start, [])),
                    STICHPROBEN))

                zeilen.append(messen(
                    name("list_edges"), anzahl,
                    lambda: db.list_edges(KANTENART),
                    lambda r: len(r) == anzahl * dichte, STICHPROBEN))
    return zeilen
