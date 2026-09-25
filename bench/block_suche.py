"""
Block 6 — Suche: `find_nodes` in seinen drei Arten und in vier Lagen des
Feldindex.

Die drei Arten (siehe Docstring von find_nodes):
  Teilstring    {"ort": "keller"}     über den Feldindex
  Platzhalter   {"ort": "ha*1"}       über den Feldindex, mit Muster
  Prädikat      {"ort": lambda v: …}  Durchgang durch alle Knoten

Der Feldindex ist in vier Lagen zu haben, und die Kosten der Suche hängen
vor allem davon ab, in welcher er gerade ist:
  warm            gebaut und gültig — die Suche selbst
  nach Änderung   JEDE Änderung an der Sammlung verwirft ihn; gemessen
                  wird Ändern + Suchen, denn so tritt es auf
  frisch, Datei   gleich nach dem Öffnen, Indexdatei liegt auf der Platte
  frisch, keine   gleich nach dem Öffnen, ohne Indexdatei

Zwei Untergrenzen ohne flatgraph, am selben Bestand über die öffentliche
Schnittstelle gewonnen:
  Index bauen         ein Durchgang, der {wert: {kennungen}} aufbaut —
                      so viel kostet es, den Index aus dem Speicher zu
                      gewinnen
  Inhalt serialisieren + MD5
                      jeden Knoten als JSON serialisieren und hashen.
                      Das tut flatgraph heute, um eine Indexdatei auf
                      Gültigkeit zu prüfen (_collection_checksum)
"""
import hashlib
import itertools
import json
import os
import shutil

from rahmen import ORTE, SAMMLUNG, Bestand, knoten_id, messen, schliessen

NAME = "suche"

STICHPROBEN = 7


def lauf(fg, groessen):
    zeilen = []
    for anzahl in groessen:
        b = Bestand(fg, anzahl)
        with b as db:
            wurzel = b.wurzel
            alle = db.list_nodes(SAMMLUNG, readonly=True)
            im_keller = {k for k, v in alle.items() if v["ort"] == "Keller"}
            in_halle_1 = {k for k, v in alle.items() if v["ort"] == "Halle 1"}

            genau = lambda erwartet: (lambda r: set(r) == erwartet)

            # --- Untergrenzen ---------------------------------------------
            def index_bauen():
                idx = {}
                for k, v in db.list_nodes(SAMMLUNG, readonly=True).items():
                    idx.setdefault(str(v["ort"]).lower(), set()).add(k)
                return idx

            zeilen.append(messen("Index bauen (Untergrenze)", anzahl, index_bauen,
                                 lambda idx: idx.get("keller") == im_keller, STICHPROBEN))

            def serialisieren_md5():
                h = hashlib.md5()
                knoten = db.list_nodes(SAMMLUNG, readonly=True)
                for k in sorted(knoten):
                    h.update(k.encode())
                    h.update(json.dumps(knoten[k], sort_keys=True,
                                        ensure_ascii=False).encode())
                return h.hexdigest()

            zeilen.append(messen("Inhalt serialisieren + MD5", anzahl, serialisieren_md5,
                                 lambda h: len(h) == 32, STICHPROBEN))

            # --- warm -----------------------------------------------------
            db.find_nodes(SAMMLUNG, {"ort": "keller"})
            zeilen.append(messen("Teilstring, warm", anzahl,
                                 lambda: db.find_nodes(SAMMLUNG, {"ort": "keller"}, readonly=True),
                                 genau(im_keller), STICHPROBEN))
            zeilen.append(messen("Platzhalter, warm", anzahl,
                                 lambda: db.find_nodes(SAMMLUNG, {"ort": "ha*1"}, readonly=True),
                                 genau(in_halle_1), STICHPROBEN))
            zeilen.append(messen("Prädikat", anzahl,
                                 lambda: db.find_nodes(SAMMLUNG, {"ort": lambda v: v == "Keller"},
                                                       readonly=True),
                                 genau(im_keller), STICHPROBEN))

            # --- nach einer Änderung --------------------------------------
            # Gesucht wird nach dem NEUEN Wert, und er muss genau den
            # geänderten Knoten liefern. Das ist die Gegenprobe: ein Index,
            # der nach der Änderung nicht nachgezogen würde, fände nichts —
            # und wäre verdächtig schnell.
            zaehler = itertools.count()
            ziel = knoten_id(0)
            zuletzt = {}

            def aendern_und_suchen():
                wert = f"umzug-{next(zaehler):07d}-"
                zuletzt["wert"] = wert
                db.update_node(SAMMLUNG, ziel, {"ort": wert})
                return db.find_nodes(SAMMLUNG, {"ort": wert}, readonly=True)

            zeilen.append(messen("1 Änderung, dann Teilstring", anzahl,
                                 aendern_und_suchen, genau({ziel}), STICHPROBEN))

            def nur_aendern():
                db.update_node(SAMMLUNG, ziel, {"ort": f"umzug-{next(zaehler):07d}-"})

            zeilen.append(messen("1 Änderung allein", anzahl, nur_aendern,
                                 lambda r: r is None, STICHPROBEN))
            # Den Knoten zurück an einen gewöhnlichen Ort, damit die
            # folgenden Zeilen denselben Bestand sehen wie die ersten.
            db.update_node(SAMMLUNG, ziel, {"ort": alle[ziel]["ort"]})

            # --- frisch geöffnet ------------------------------------------
            halter = {}
            index_ort = os.path.join(wurzel, "datenbank", "index")

            # Ein Bestand, eine offene Instanz: vor jedem Öffnen die vorige
            # schliessen, ausserhalb der Zeitnahme (im vorher-Haken).
            schliessen(db)

            def oeffnen():
                schliessen(halter.pop("db", None))
                halter["db"] = fg.FlatGraphDB(wurzel)

            def oeffnen_ohne_indexdatei():
                shutil.rmtree(index_ort, ignore_errors=True)
                oeffnen()

            erste_suche = lambda: halter["db"].find_nodes(SAMMLUNG, {"ort": "keller"},
                                                          readonly=True)
            # Erst einmal suchen, damit die Indexdatei zum aktuellen Stand
            # passt — sonst misst „mit Datei“ in Wahrheit „Datei veraltet“.
            oeffnen()
            erste_suche()
            zeilen.append(messen("erste Suche, Indexdatei da", anzahl,
                                 erste_suche, genau(im_keller), STICHPROBEN, vorher=oeffnen))
            zeilen.append(messen("erste Suche, ohne Indexdatei", anzahl,
                                 erste_suche, genau(im_keller), STICHPROBEN,
                                 vorher=oeffnen_ohne_indexdatei))
            schliessen(halter.pop("db", None))
    return zeilen


# ORTE wird importiert, damit ein geänderter Ortskatalog im Rahmen hier
# auffällt: die Suchmuster oben ("keller", "ha*1") setzen ihn voraus.
assert "Keller" in ORTE and "Halle 1" in ORTE
