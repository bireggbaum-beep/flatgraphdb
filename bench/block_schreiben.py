"""
Block 3 — Schreiben: ein einzelner Aufruf ohne Transaktion.

Die offene Frage aus VERTRAG.md Abschnitt 5: dort wuchs ein Schreibvorgang
mit der Sammlung (Form 1, 6.6 ms bei 200 → 89 ms bei 2000 Knoten), weil bei
jeder Änderung die ganze Deltadatei neu geschrieben wurde. Form 2 schreibt
je Knoten eine Datei. Ob das Wachstum damit weg ist, wird hier gemessen.

Kanten sind anders gebaut: alle Kanten einer Art liegen in EINER Datei,
und `create_edge` schreibt sie ganz. Bis 3.0.0 las es sie vorher auch
ganz und baute danach den Nachbarschaftsindex der Art neu; seit
3.0.0-entwurf (Prozesssperre) nicht mehr. Deshalb wird `create_edge` bei
zwei Kantendichten gemessen.

Für Kanten steht eine eigene Untergrenze daneben: die Kantendatei lesen,
parsen und dauerhaft zurückschreiben, ohne flatgraph — der Weg bis
3.0.0. Eine Fassung, die nicht mehr nachliest, kann darunter liegen; was
dann bleibt, ist das Schreiben der ganzen Datei, also das FORMAT (eine
Datei je Kantenart).

Als Untergrenze für Knoten steht daneben, was ein dauerhafter Schreibvorgang auf
dieser Platte mindestens kostet: eine Datei in Knotengrösse schreiben,
fsync, os.replace, fsync aufs Verzeichnis. Das ist genau der Weg von
`_save_json_atomic`. Was flatgraph darüber hinaus braucht, ist sein Anteil.

Jede Messung verändert den Bestand: create_node legt je Aufruf einen
Knoten an. Das sind einige hundert zusätzliche Knoten, bei 500 also
spürbar mehr, als der Name der Zeile sagt. Die Spalte „Bestand“ nennt die
Grösse VOR der Messung.
"""
import itertools
import json
import os
import random

from rahmen import (KANTENART, SAMMLUNG, SAMEN, TEXT_BYTES, Bestand, _text,
                    auf_platte, knoten_id, messen)

NAME = "schreiben"

KANTEN_JE_KNOTEN = 4


def _knoten_pfad(wurzel, node_id):
    # Speicherform 2: nodes/<sammlung>/<id>.json. Die Ids hier enthalten
    # nichts, was prozentkodiert würde.
    return os.path.join(wurzel, "datenbank", "nodes", SAMMLUNG, f"{node_id}.json")


def _kanten_pfad(wurzel):
    return os.path.join(wurzel, "datenbank", "edges", f"{KANTENART}.json")


def _dauerhaft_schreiben(pfad, daten):
    """Die Untergrenze: derselbe Weg wie _save_json_atomic, ohne flatgraph."""
    tmp = pfad + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, pfad)
    fd = os.open(os.path.dirname(pfad), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return pfad


def lauf(fg, groessen):
    zeilen = []
    for anzahl in groessen:
        for kanten in (0, KANTEN_JE_KNOTEN):
            b = Bestand(fg, anzahl, kanten_je_knoten=kanten)
            with b as db:
                wurzel = b.wurzel
                rnd = random.Random(SAMEN)
                zusatz = f" +{kanten}K/Kn" if kanten else ""

                if not kanten:
                    # Knoten und Untergrenze nur einmal je Grösse: Kanten
                    # liegen in eigenen Dateien und berühren das
                    # Knotenschreiben nicht.
                    probe = {"name": "Probe", "ort": "Keller",
                             "text": _text(rnd, TEXT_BYTES)}
                    ziel = os.path.join(wurzel, "untergrenze.json")
                    zeilen.append(messen(
                        "Datei dauerhaft schreiben", anzahl,
                        lambda: _dauerhaft_schreiben(ziel, probe),
                        os.path.isfile))

                    neue = (f"neu_{i:07d}" for i in itertools.count())
                    letzte = []

                    def anlegen():
                        nid = next(neue)
                        letzte.append(nid)
                        return db.create_node(SAMMLUNG, nid, dict(probe))

                    # Gegenprobe: der Knoten muss auf der Platte stehen,
                    # nicht nur im Speicher. Ein gepufferter Schreibvorgang
                    # wäre verdächtig schnell.
                    zeilen.append(messen(
                        "create_node", anzahl, anlegen,
                        lambda ref: auf_platte(wurzel, "nodes", SAMMLUNG, letzte[-1]) is not None))

                    ids = [knoten_id(rnd.randrange(anzahl)) for _ in range(1000)]
                    naechste = itertools.cycle(ids).__next__
                    zaehler = itertools.count()
                    zuletzt = {}

                    def aendern():
                        nid, wert = naechste(), f"Anlage geändert {next(zaehler)}"
                        zuletzt["id"], zuletzt["wert"] = nid, wert
                        return db.update_node(SAMMLUNG, nid, {"name": wert})

                    def steht_auf_platte(_):
                        knoten = auf_platte(wurzel, "nodes", SAMMLUNG, zuletzt["id"]) or {}
                        return knoten.get("name") == zuletzt["wert"]

                    zeilen.append(messen("update_node", anzahl, aendern, steht_auf_platte))

                quellen = [f"{SAMMLUNG}/{knoten_id(rnd.randrange(anzahl))}" for _ in range(1000)]
                q = itertools.cycle(quellen).__next__
                z = itertools.cycle(reversed(quellen)).__next__

                # Die Untergrenze „ganze Kantendatei umschreiben“ gibt es nur,
                # solange es die ganze Kantendatei gibt (Speicherform 2).
                if kanten and os.path.isfile(_kanten_pfad(wurzel)):
                    kopie = os.path.join(wurzel, "kanten_untergrenze.json")
                    with open(_kanten_pfad(wurzel), encoding="utf-8") as f:
                        _dauerhaft_schreiben(kopie, json.load(f))

                    def kantendatei_umschreiben():
                        with open(kopie, encoding="utf-8") as f:
                            daten = json.load(f)
                        _dauerhaft_schreiben(kopie, daten)
                        return len(daten)

                    zeilen.append(messen(
                        f"Kantendatei umschreiben{zusatz}", anzahl, kantendatei_umschreiben,
                        lambda n: n == anzahl * kanten))

                def kante_auf_platte(edge_id):
                    return auf_platte(wurzel, "edges", KANTENART, edge_id) is not None

                zeilen.append(messen(f"create_edge{zusatz}", anzahl,
                                     lambda: db.create_edge(q(), z(), KANTENART),
                                     kante_auf_platte))
    return zeilen
