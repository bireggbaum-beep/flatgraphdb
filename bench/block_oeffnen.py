"""
Block 2 — Öffnen: wie lange dauert `FlatGraphDB(wurzel)`?

flatgraph hält den ganzen Bestand im Arbeitsspeicher (VERTRAG.md 1). Beim
Öffnen wird deshalb alles gelesen: in Speicherform 2 eine Datei je Knoten,
dazu eine Datei je Kantenart, und der Nachbarschaftsindex wird aus den
Kanten gebaut. Diese Kosten zahlt jeder Start der Anwendung.

Neben dem Öffnen stehen zwei Untergrenzen, gemessen am selben Bestand:

  Dateien lesen        nur listdir + read, ohne JSON — das kostet allein
                       das Dateisystem
  Dateien + json       zusätzlich json.loads je Datei — das muss jede
                       Bibliothek mindestens tun, die dieses Format liest

Der Abstand vom Öffnen zur zweiten Untergrenze ist, was flatgraph selbst
dazutut. Ohne diese Vergleichszeilen wäre „Öffnen dauert X“ nicht zu
bewerten: X kann gut oder schlecht sein, je nachdem, was die Platte hergibt.

Warm heisst: die Dateien liegen im Seitencache, wie direkt nach dem Bauen
oder einem kurz vorher beendeten Lauf. Kalt heisst: der Cache ist vor jeder
Stichprobe geleert, wie nach einem Neustart des Rechners. Kalt geht nur
unter Linux mit root; sonst fehlen diese Zeilen, und die Ausgabe sagt es.
"""
import json
import os

from rahmen import (KANTENART, SAMMLUNG, Bestand, dateicache_leerbar,
                    dateicache_leeren, messen, schliessen)

NAME = "oeffnen"

# Wie im Vertrag (500 Knoten / 2000 Kanten): vier je Knoten.
KANTEN_JE_KNOTEN = 4

# Öffnen dauert Millisekunden bis Sekunden. 15 Stichproben wie beim Lesen
# brächten nichts Genaueres, nur einen längeren Lauf.
STICHPROBEN = 7


def _alle_dateien(wurzel):
    """Jede Datei, die das Öffnen liest: Knoten, Kanten, Formatmarke."""
    datenbank = os.path.join(wurzel, "datenbank")
    dateien = []
    for ordner, _, namen in os.walk(datenbank):
        if os.path.basename(ordner) == "index":
            continue          # abgeleitet, wird beim Öffnen nicht gelesen
        dateien.extend(os.path.join(ordner, n) for n in namen if n.endswith(".json"))
    return dateien


def _nur_lesen(wurzel):
    n = 0
    for pfad in _alle_dateien(wurzel):
        with open(pfad, "rb") as f:
            f.read()
        n += 1
    return n


def _lesen_und_json(wurzel):
    n = 0
    for pfad in _alle_dateien(wurzel):
        with open(pfad, "rb") as f:
            json.loads(f.read())
        n += 1
    return n


def lauf(fg, groessen):
    kalt = dateicache_leerbar()
    if not kalt:
        print("   (kalt nicht messbar: Dateicache lässt sich hier nicht leeren)")
    zeilen = []
    for anzahl in groessen:
        for kanten in (0, KANTEN_JE_KNOTEN):
            b = Bestand(fg, anzahl, kanten_je_knoten=kanten)
            with b:
                wurzel = b.wurzel
                dateien = len(_alle_dateien(wurzel))
                zusatz = f" +{kanten}K/Kn" if kanten else ""

                def ist_bestand(db, kanten=kanten):
                    return (len(db.list_nodes(SAMMLUNG, readonly=True)) == anzahl and
                            len(db.list_edges(KANTENART)) == anzahl * kanten)

                ist_vollstaendig = lambda n: n == dateien

                # Ein Bestand, eine offene Instanz: die bauende zuerst zu,
                # und vor jedem Öffnen die vorige. Das Schliessen liegt
                # damit in der Zeitnahme, kostet aber nur das Lösen der
                # Sperre — Mikrosekunden gegen Millisekunden fürs Öffnen.
                schliessen(b.db)
                halter = {}

                def oeffnen():
                    schliessen(halter.pop("db", None))
                    halter["db"] = fg.FlatGraphDB(wurzel)
                    return halter["db"]

                faelle = [("warm", None)] + ([("kalt", dateicache_leeren)] if kalt else [])
                for zustand, vorher in faelle:
                    if not kanten:
                        # Die Untergrenzen nur ohne Kanten: eine Kantenart ist
                        # EINE Datei, die Zeile sähe fast gleich aus.
                        zeilen.append(messen(f"Dateien lesen {zustand}", anzahl,
                                             lambda: _nur_lesen(wurzel), ist_vollstaendig,
                                             STICHPROBEN, vorher=vorher))
                        zeilen.append(messen(f"Dateien + json {zustand}", anzahl,
                                             lambda: _lesen_und_json(wurzel), ist_vollstaendig,
                                             STICHPROBEN, vorher=vorher))
                    zeilen.append(messen(f"öffnen {zustand}{zusatz}", anzahl,
                                         oeffnen, ist_bestand, STICHPROBEN, vorher=vorher))
                schliessen(halter.pop("db", None))
    return zeilen
