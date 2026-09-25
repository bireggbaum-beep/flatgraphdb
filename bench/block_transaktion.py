"""
Block 4 — Transaktion: was kostet `transaction()`, und was spart sie?

`transaction()` macht beim Betreten eine tiefe Kopie des GANZEN Bestands
(Knoten, Kanten, Kantenverzeichnis) für den Rollback, puffert die
Schreibvorgänge und schreibt sie beim Verlassen gebündelt. Bei einer
Ausnahme setzt sie den Speicher auf die Kopie zurück und baut den
Nachbarschaftsindex komplett neu.

Daraus vier Fragen, je eine Zeilengruppe:

  feste Kosten   Transaktion mit EINER Änderung. Wächst sie mit dem
                 Bestand, obwohl sie nur einen Knoten berührt?
  Knoten         100 Änderungen einzeln gegen 100 in einer Transaktion.
                 Jeder Knoten ist eine eigene Datei mit eigenem fsync —
                 bündelt die Transaktion hier überhaupt etwas?
  Kanten         20 neue Kanten einzeln gegen 20 in einer Transaktion.
                 Einzeln schreibt jede die ganze Kantendatei (Block 3);
                 in der Transaktion geschieht das einmal.
  Rollback       Transaktion mit einer Änderung, die mit einer Ausnahme
                 endet.

Als Untergrenze steht „Bestand tief kopieren“ daneben: `copy.deepcopy`
auf denselben Inhalt, gewonnen über die öffentliche Schnittstelle. So
lässt sich ablesen, welcher Teil der festen Kosten die Kopie ist, ohne
dass die Messung in flatgraphs Innereien greift — sie soll auch eine
Fassung messen können, die anders gebaut ist.

Gemessen mit vier Kanten je Knoten, weil Kopie und Rollback die Kanten
mit anfassen.
"""
import copy
import itertools
import json
import os
import random

from rahmen import KANTENART, SAMMLUNG, SAMEN, Bestand, auf_platte, knoten_id, messen

NAME = "transaktion"

KANTEN_JE_KNOTEN = 4
KNOTEN_STAPEL = 100
KANTEN_STAPEL = 20
STICHPROBEN = 7


class _Abbruch(Exception):
    """Absichtlich geworfen, um den Rollback auszulösen."""


def _knoten_pfad(wurzel, node_id):
    return os.path.join(wurzel, "datenbank", "nodes", SAMMLUNG, f"{node_id}.json")


def _kanten_pfad(wurzel):
    return os.path.join(wurzel, "datenbank", "edges", f"{KANTENART}.json")


def _auf_platte(wurzel, node_id):
    return auf_platte(wurzel, "nodes", SAMMLUNG, node_id)


def lauf(fg, groessen):
    zeilen = []
    for anzahl in groessen:
        b = Bestand(fg, anzahl, kanten_je_knoten=KANTEN_JE_KNOTEN)
        with b as db:
            wurzel = b.wurzel
            rnd = random.Random(SAMEN)
            zaehler = itertools.count()
            ids = [knoten_id(i) for i in rnd.sample(range(anzahl), min(anzahl, 1000))]
            naechste = itertools.cycle(ids).__next__
            zuletzt = {}

            def aendern():
                nid, wert = naechste(), f"Wert {next(zaehler)}"
                zuletzt["id"], zuletzt["wert"] = nid, wert
                db.update_node(SAMMLUNG, nid, {"ort": wert})

            def zuletzt_auf_platte(_):
                return _auf_platte(wurzel, zuletzt["id"]).get("ort") == zuletzt["wert"]

            # --- Untergrenze: den Bestand einmal tief kopieren -----------
            inhalt = {"knoten": db.list_nodes(SAMMLUNG),
                      "kanten": db.list_edges()}
            zeilen.append(messen(
                "Bestand tief kopieren", anzahl, lambda: copy.deepcopy(inhalt),
                lambda k: len(k["knoten"]) == anzahl
                and len(k["kanten"]) == anzahl * KANTEN_JE_KNOTEN,
                STICHPROBEN))

            # --- feste Kosten ---------------------------------------------
            zeilen.append(messen("1 Änderung ohne Transaktion", anzahl,
                                 aendern, zuletzt_auf_platte, STICHPROBEN))

            def eine_in_transaktion():
                with db.transaction():
                    aendern()

            zeilen.append(messen("1 Änderung in Transaktion", anzahl,
                                 eine_in_transaktion, zuletzt_auf_platte, STICHPROBEN))

            # --- Knoten bündeln -------------------------------------------
            def stapel_einzeln():
                for _ in range(KNOTEN_STAPEL):
                    aendern()

            def stapel_transaktion():
                with db.transaction():
                    for _ in range(KNOTEN_STAPEL):
                        aendern()

            zeilen.append(messen(f"{KNOTEN_STAPEL} Änderungen einzeln", anzahl,
                                 stapel_einzeln, zuletzt_auf_platte, STICHPROBEN))
            zeilen.append(messen(f"{KNOTEN_STAPEL} Änderungen in Transaktion", anzahl,
                                 stapel_transaktion, zuletzt_auf_platte, STICHPROBEN))

            # --- Kanten bündeln -------------------------------------------
            refs = [f"{SAMMLUNG}/{i}" for i in ids]
            q = itertools.cycle(refs).__next__
            z = itertools.cycle(reversed(refs)).__next__
            neue_kanten = []

            def kanten_einzeln():
                for _ in range(KANTEN_STAPEL):
                    neue_kanten.append(db.create_edge(q(), z(), KANTENART))

            def kanten_transaktion():
                with db.transaction():
                    for _ in range(KANTEN_STAPEL):
                        neue_kanten.append(db.create_edge(q(), z(), KANTENART))

            def letzte_kante_auf_platte(_):
                return auf_platte(wurzel, "edges", KANTENART, neue_kanten[-1]) is not None

            # Einzeln kostet jede Kante bei 10 000 Knoten ~0.3 s (Block 3):
            # 20 davon sind 6 s je Stichprobe. Deshalb hier weniger
            # Stichproben — die Streuung ist bei dieser Dauer ohnehin klein.
            zeilen.append(messen(f"{KANTEN_STAPEL} Kanten einzeln", anzahl,
                                 kanten_einzeln, letzte_kante_auf_platte, 3))
            zeilen.append(messen(f"{KANTEN_STAPEL} Kanten in Transaktion", anzahl,
                                 kanten_transaktion, letzte_kante_auf_platte, STICHPROBEN))

            # --- Rollback -------------------------------------------------
            opfer = ids[0]
            vorher = db.get_node(f"{SAMMLUNG}/{opfer}")

            def rollback():
                try:
                    with db.transaction():
                        db.update_node(SAMMLUNG, opfer, {"ort": "verworfen"})
                        raise _Abbruch()
                except _Abbruch:
                    pass

            # Gegenprobe: nach dem Rollback steht der alte Wert im Speicher
            # UND auf der Platte. Ein Rollback, der nur den Speicher
            # zurücksetzt, wäre schnell und falsch.
            def zurueckgesetzt(_):
                return (db.get_node(f"{SAMMLUNG}/{opfer}") == vorher
                        and _auf_platte(wurzel, opfer) == vorher)

            zeilen.append(messen("Rollback nach 1 Änderung", anzahl,
                                 rollback, zurueckgesetzt, STICHPROBEN))
    return zeilen
