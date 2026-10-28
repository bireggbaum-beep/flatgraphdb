"""
Graph-Editor: Objekte erfassen, per Ziehen verknüpfen, als Baum lesen.

    python editor/editor.py [--bestand PFAD] [--port 8765] [--modell DATEI]

Läuft nur lokal (127.0.0.1), ein Prozess, nur Standardbibliothek. Der
Bestand ist ein normaler flatgraph-Bestand. Mit Modell (editor/modell.json) gelten dessen Typen, Felder und Beziehungsarten;
ohne (`--modell keins`) entstehen sie beim ersten Gebrauch.

Die Logik steckt in `Editor` (ohne HTTP), damit sie sich testen lässt.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from flatgraph import FlatGraphDB, KanteExistiert  # noqa: E402

HIER = os.path.dirname(os.path.abspath(__file__))


class Fehler(Exception):
    """Etwas, das der Benutzer lesen soll — kein Programmfehler."""


def _anzeige(s):
    return s.replace("_", " ")


def _name_fuer_bestand(text, was):
    """Typ- und Kantennamen erlauben nur Buchstaben, Ziffern, _ - . —
    Leerzeichen werden zu _, alles andere entfällt."""
    text = re.sub(r"\s+", "_", (text or "").strip())
    text = re.sub(r"[^\w.\-]", "", text)
    text = text.strip("_.-")
    if not text:
        raise Fehler(f"{was} fehlt.")
    return text[:100]


def _kennung(name):
    text = (name or "").lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:40] or "objekt"


def _schreibe_json(pfad, daten):
    """Über eine Arbeitsdatei daneben, fsync, os.replace — nie an Ort und Stelle."""
    arbeit = pfad + ".arbeit"
    with open(arbeit, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(arbeit, pfad)


def _teile_typ(zeile, standardtyp):
    """„Raum: Reinraum C“ -> (Raum, Reinraum C). Ein Präfix gilt nur als Typ,
    wenn es kurz ist und keine Ziffer enthält — „Pumpe 12: Nord“ ist ein Name."""
    m = re.match(r"^([^\s:][^:\d]{0,29}):\s+(.+)$", zeile)
    if m and len(m.group(1).split()) <= 2:
        return m.group(1).strip(), m.group(2).strip()
    return standardtyp, zeile.strip()


_STAMM = ("source", "target", "type", "created_at", "gerichtet")


class Modell:
    """Das Datenmodell (JSON-Schema): *Node-Definitionen und Edge_*-Definitionen.

    Gelesen werden Typen mit Feldern und Auswahlwerten, die erlaubten
    Beziehungen als Paare (von-Typ, Art, nach-Typ) — das ist das Regelwerk —
    und die Kanten-Attribute (etwa ProcessstepType). Ältere Schemata mit einer
    einzigen GraphEdge-Definition (nur Arten, keine Paare) lesen sich weiter."""

    def __init__(self, pfad):
        with open(pfad, encoding="utf-8") as f:
            schema = json.load(f)
        defs = schema.get("$defs", {})
        self.typen = {}
        for d in defs.values():
            typ = d.get("properties", {}).get("type", {}).get("const")
            if not typ or "fromType" in d.get("properties", {}):
                continue
            eig = d["properties"]["properties"]
            pflicht = set(eig.get("required", []))
            felder = []
            for name, spez in eig.get("properties", {}).items():
                felder.append({"name": name, "pflicht": name in pflicht,
                               "titel": spez.get("title", ""),
                               "optionen": spez.get("enum"),
                               "freitext": bool(spez.get("x-freitext")),   # Auswahl, aber auch freier Text erlaubt
                               "hinweis": spez.get("description", "")})
            namefeld = next((f["name"] for f in felder if f["name"].endswith("Name")), "name")
            # Die ID (aus SAP) ist je Typ eindeutig; doppelte sind ein Fehler
            idfeld = next((f["name"] for f in felder if f["name"].endswith("ID")), None)
            self.typen[typ] = {"namefeld": namefeld, "idfeld": idfeld, "felder": felder}
        # „Steht in“ (Plant) und „steht in“ (Equipment) sind eine Art: die
        # kleingeschriebene gewinnt.
        gesehen, paare = {}, []

        def art_merken(a):
            a = a[:1].lower() + a[1:]            # „Steht in“ wie die übrigen Arten klein beginnen
            gesehen.setdefault(a.casefold(), a)
        for d in defs.values():
            pr = d.get("properties", {})
            von, art, nach = (pr.get(x, {}).get("const") for x in ("fromType", "relation", "toType"))
            if von and art and nach:
                art_merken(art)
                paare.append((von, art, nach, pr.get("properties")))
        kante = defs.get("GraphEdge", {}).get("properties", {})
        for a in kante.get("relation", {}).get("enum", []):
            art_merken(a)
        self.arten = list(gesehen.values())
        self.hubs, self.einzeln, self.grob, self.kontext, self.fluss = [], [], set(), [], None
        self.regeln, self.kantenfelder = [], {}      # Regel: von, art, nach; Art -> [Attribut]
        for von, art, nach, attr in paare:
            art = self.kanon_art(art)
            if {"von": von, "art": art, "nach": nach} not in self.regeln:
                self.regeln.append({"von": von, "art": art, "nach": nach})
            for name, spez in ((attr or {}).get("properties") or {}).items():
                liste = self.kantenfelder.setdefault(art, [])
                if not any(f["name"] == name for f in liste):
                    liste.append({"name": name, "titel": spez.get("title", ""), "optionen": spez.get("enum"),
                                  "pflicht": name in (attr.get("required") or [])})
        for name, spez in kante.get("properties", {}).get("properties", {}).items():
            m = re.search(r"'([^']+)'", spez.get("description", ""))
            art = self.kanon_art(m.group(1)) if m else None
            if art:
                self.kantenfelder.setdefault(art, []).append(
                    {"name": name, "optionen": spez.get("enum"), "pflicht": False})

    def lade_wegregeln(self, pfad):
        """Einstellungen für die Wege (`regeln.json`): Typen, durch die ein Weg
        nicht hindurchläuft (`nicht_durchlaufen`: Dokument, Medium, Software —
        Ziel ja, Zwischenschritt nein), und Beziehungsarten, die nicht mit
        weiteren Schritten verkettet werden (`nur_einzeln`: Nachfolger).
        Rückgabe: Einträge, die zum Modell nicht passen."""
        with open(pfad, encoding="utf-8") as f:
            daten = json.load(f)
        fehler = []
        self.hubs = [t for t in (self.kanon_typ(x) for x in daten.get("nicht_durchlaufen", [])) if t]
        self.einzeln = [a for a in (self.kanon_art(x) for x in daten.get("nur_einzeln", [])) if a]
        # „Plant führt aus Prozess“: grober Weg, gilt nur, wenn kein gleich kurzer feiner da ist
        paare = {f"{r['von']} {r['art']} {r['nach']}": (r["von"], r["art"], r["nach"]) for r in self.regeln}
        self.grob = {paare[x] for x in daten.get("grob", []) if x in paare}
        fehler += [x for x in daten.get("grob", []) if x not in paare]
        self.kontext = [t for t in (self.kanon_typ(x) for x in daten.get("kontext", [])) if t]
        f = daten.get("fluss")
        if f:
            arten = {k: self.kanon_art(f.get(k, "")) for k in ("input", "output", "folge")}
            fehler += [f.get(k) for k, a in arten.items() if not a]
            if all(arten.values()):
                self.fluss = {**arten, "folge_nicht": set(f.get("folge_nicht", [])),
                              "produkt": f.get("produkt") or {}, "blatt": f.get("blatt") or {}}
        fehler += [x for x in daten.get("kontext", []) if not self.kanon_typ(x)]
        fehler += [x for x in daten.get("nicht_durchlaufen", []) if not self.kanon_typ(x)]
        fehler += [x for x in daten.get("nur_einzeln", []) if not self.kanon_art(x)]
        return fehler

    def zwischen(self, typ_a, typ_b, art=None):
        """Erlaubte Beziehungen zwischen zwei Typen: [(art, richtung)], richtung
        „aus“ = von a nach b, „ein“ = von b nach a. Mit `art` nur diese Art."""
        gefunden = []
        for r in self.regeln:
            if art is not None and r["art"] != art:
                continue
            if r["von"] == typ_a and r["nach"] == typ_b:
                gefunden.append((r["art"], "aus"))
            if r["von"] == typ_b and r["nach"] == typ_a and typ_a != typ_b:
                gefunden.append((r["art"], "ein"))
        return gefunden

    def kanon_art(self, text):
        for a in self.arten:
            if a.casefold() == (text or "").strip().casefold():
                return a
        return None

    def kanon_typ(self, text):
        for t in self.typen:
            if t.casefold() == (text or "").strip().casefold():
                return t
        return None

    def als_json(self):
        return {"typen": self.typen, "arten": self.arten,
                "kantenfelder": self.kantenfelder, "regeln": self.regeln,
                "hubs": self.hubs, "einzeln": self.einzeln}


class _Probe(Exception):
    """Bricht die Transaktion eines Probelaufs ab: alles wird zurückgerollt."""


class Editor:
    def __init__(self, pfad, modell=None, strikt=True):
        self.db = FlatGraphDB(pfad, audit=True)
        self.modell = modell
        # Mit Regelwerk im Modell sind nur dessen Beziehungspaare erlaubt
        # („Erlaubt NUR die exakt definierten Beziehungspaare“); `strikt=False`
        # lässt alles zu.
        self.strikt = strikt
        # Bestandsname einer Art -> Anzeigename (Klammern, „/“ und „:“ sind im
        # Bestand nicht erlaubt, die Anzeige soll trotzdem das Original zeigen)
        self._art_anzeige = {_name_fuer_bestand(a, "Art"): a
                             for a in (modell.arten if modell else [])}
        # Tabellenlayouts liegen beim Bestand, damit alle dieselben sehen;
        # flatgraph liest im Wurzelverzeichnis nur seine eigenen Ordner.
        self._layoutdatei = os.path.join(pfad, "layouts.json")
        self._importdatei = os.path.join(pfad, "importe.json")

    def _art_b(self, art):
        """Art-Name für den Bestand; mit Modell nur, was das Modell kennt."""
        if self.modell:
            kanon = self.modell.kanon_art(art)
            if not kanon:
                raise Fehler(f"Die Beziehungsart „{art}“ gibt es im Modell nicht. "
                             f"Erlaubt: {', '.join(self.modell.arten)}.")
            art = kanon
        return _name_fuer_bestand(art, "Beziehungsart")

    def _art_zeigen(self, art_b):
        return self._art_anzeige.get(art_b) or _anzeige(art_b)

    def _typ_b(self, typ):
        if self.modell:
            kanon = self.modell.kanon_typ(typ)
            if not kanon:
                raise Fehler(f"Den Typ „{typ}“ gibt es im Modell nicht. "
                             f"Erlaubt: {', '.join(self.modell.typen)}.")
            return kanon
        return _name_fuer_bestand(typ, "Typ")

    def _namefeld(self, typ):
        return self.modell.typen[typ]["namefeld"] if self.modell and typ in self.modell.typen else "name"

    def _idfeld(self, typ):
        return self.modell.typen[typ].get("idfeld") if self.modell and typ in self.modell.typen else None

    def _mit_id(self, typ, wert, ausser=None):
        """Das Objekt des Typs mit dieser ID (ausser `ausser`) oder None."""
        idf, wert = self._idfeld(typ), str(wert).strip()
        for kid, d in self.db.list_nodes(typ, readonly=True).items():
            if f"{typ}/{kid}" != ausser and str(d.get(idf, "")).strip() == wert:
                return f"{typ}/{kid}"
        return None

    def _pruefe_id(self, typ, felder, ausser=None):
        idf = self._idfeld(typ)
        wert = str((felder or {}).get(idf, "") if idf else "").strip()
        if wert:
            doppelt = self._mit_id(typ, wert, ausser)
            if doppelt:
                nf = self._namefeld(typ)
                raise Fehler(f"Die ID „{wert}“ hat schon {typ} „{self.db.get_node(doppelt, readonly=True).get(nf)}“. "
                             f"Doppelte IDs sind nicht erlaubt.")

    def _pruefe_werte(self, typ, felder):
        """Auswahlfelder nehmen nur Werte aus dem Modell; leer = nicht gesetzt."""
        if not (self.modell and typ in self.modell.typen):
            return
        bekannt = {f["name"]: f for f in self.modell.typen[typ]["felder"]}
        for k, v in (felder or {}).items():
            f = bekannt.get(k)
            if f and f["optionen"] and not f["freitext"] and v not in ("", None) and v not in f["optionen"]:
                raise Fehler(f"„{v}“ ist für {k} nicht vorgesehen. "
                             f"Erlaubt: {', '.join(f['optionen'])}.")

    def schliessen(self):
        self.db.close()

    # --- Lesen -------------------------------------------------------------

    def zustand(self):
        db = self.db
        knoten, refs = [], set()
        for typ in sorted(db.list_collections()):
            for kid, d in db.list_nodes(typ, readonly=True).items():
                ref = f"{typ}/{kid}"
                refs.add(ref)
                nf = self._namefeld(typ)
                fehlt = []
                if self.modell and typ in self.modell.typen:
                    fehlt = [f["name"] for f in self.modell.typen[typ]["felder"]
                             if f["pflicht"] and f["name"] != nf
                             and d.get(f["name"]) in (None, "")]
                knoten.append({
                    "ref": ref, "typ": _anzeige(typ), "luecken": self._fluss_luecken(ref, d),
                    "name": d.get(nf) or kid,
                    "felder": {k: v for k, v in d.items()
                               if k not in (nf, "notiz") and not k.startswith("_") and v != ""},
                    "notiz": d.get("notiz") or "",
                    "fehlt": fehlt,
                })
        kanten = []
        for eid, e in db.list_edges().items():
            if e["source"] in refs and e["target"] in refs:
                kanten.append({"id": eid, "von": e["source"], "nach": e["target"],
                               "art": self._art_zeigen(e["type"]),
                               "gerichtet": e.get("gerichtet", True),
                               "felder": {k: v for k, v in e.items()
                                          if k not in _STAMM and not k.startswith("_")}})
        return {"knoten": knoten, "kanten": kanten,
                "modell": self.modell.als_json() if self.modell else None}

    def metapfade(self, von, nach=None, max_hops=2):
        """Typisierte Wege zwischen Typen nach dem Regelwerk (`regeln.json`).

        Jeder Weg ist eine Liste von Schritten {art, richtung, typ}: „Prozess <-
        ist Teil von <- Prozessschritt -> benötigt Input -> Material“. Ein Typ
        kommt je Weg nur einmal vor (kein Kreisen); Typen aus `nicht_durchlaufen`
        (Dokument, Medium, Software) enden einen Weg, statt ihn fortzusetzen; nur ein Selbstbezug wie
        „Prozess ist Teil von Prozess“ gilt als erster Schritt. Ohne `nach`
        alle Wege bis `max_hops` Schritte."""
        if not (self.modell and self.modell.regeln):
            return []
        nachbarn = {}
        for r in self.modell.regeln:
            nachbarn.setdefault(r["von"], []).append((r["art"], "aus", r["nach"]))
            nachbarn.setdefault(r["nach"], []).append((r["art"], "ein", r["von"]))
        ergebnis = []

        def geh(typ, pfad, besucht):
            if len(pfad) >= max_hops:
                return
            for art, richt, t2 in nachbarn.get(typ, []):
                einzeln = art in self.modell.einzeln
                if t2 in besucht and not (not pfad and t2 == typ):
                    continue
                neu = pfad + [{"art": art, "richtung": richt, "typ": t2}]
                if nach is None or t2 == nach:
                    ergebnis.append(neu)
                if t2 not in self.modell.hubs and not einzeln:   # Hubs und Reihenfolge enden den Weg
                    geh(t2, neu, besucht | {t2})
        geh(von, [], {von})
        return ergebnis

    def _gehen(self, start, pfad):
        """Ein Metapfad ab `start`: je Schritt ein flatgraph-`get_connected`
        mit Art, Richtung und Zieltyp. Rückgabe: die erreichten Objekte."""
        aktuell = {start}
        for schritt in pfad:
            naechste = set()
            for n in aktuell:
                naechste.update(self.db.get_connected(
                    n, direction="out" if schritt["richtung"] == "aus" else "in",
                    rel_type=self._art_b(schritt["art"]),
                    target_collection=self._typ_b(schritt["typ"])))
            aktuell = naechste
            if not aktuell:
                break
        aktuell.discard(start)
        return aktuell

    def _ist_grob(self, von, pfad):
        typ = von
        for s in pfad:
            paar = (typ, s["art"], s["typ"]) if s["richtung"] == "aus" else (s["typ"], s["art"], typ)
            if paar in self.modell.grob:
                return True
            typ = s["typ"]
        return False

    def kurze_wege(self, von, max_hops):
        """Je Zieltyp die kürzesten Wege laut Modell — der innere Kreis.
        Gibt es neben einem groben Weg (`grob` in regeln.json, „Plant führt aus
        Prozess“) einen gleich kurzen feinen, gilt nur der feine: am Prozess
        beteiligt ist, wer einen seiner Schritte ausführt."""
        je_typ = {}
        for p in self.metapfade(von, None, max_hops):
            je_typ.setdefault(p[-1]["typ"], []).append(p)
        ergebnis = {}
        for typ, wege in je_typ.items():
            kurz = [p for p in wege if len(p) == min(len(q) for q in wege)]
            ergebnis[typ] = [p for p in kurz if not self._ist_grob(von, p)] or kurz
        return ergebnis

    # --- Materialfluss: Herkunft und Verwendung -----------------------------

    def _fl(self, art):
        return self._art_b(self.modell.fluss[art])

    def _ist(self, d, regel):
        return bool(regel) and str((d or {}).get(regel.get("feld"), "")) in regel.get("werte", [])

    def _fluss_luecken(self, ref, d):
        """Ein Material, das kein Schritt erzeugt und das weder Rohstoff noch
        Zukaufteil ist: hier bricht die Herkunft ab."""
        f = self.modell.fluss if self.modell else None
        if not f or ref.partition("/")[0] != "Material" or self._ist(d, f["blatt"]):
            return []
        if self.db.get_connected(ref, "in", self._fl("output")):
            return []
        return ["wird von keinem Schritt erzeugt (weder Rohstoff noch Zukaufteil)"]

    def _folge(self, schritt, richtung):
        """Nachbarschritte im Ablauf ohne Schleifen (eine Schleife führt zurück)."""
        nicht = self.modell.fluss["folge_nicht"]
        ergebnis = []
        for _eid, e in self.db.get_connected_edges(schritt, "in" if richtung == "zurueck" else "out", self._fl("folge")):
            if nicht & {str(v) for v in e.values()}:
                continue
            gegen = e["source"] if richtung == "zurueck" else e["target"]
            if self.db.get_node(gegen, readonly=True) is not None:
                ergebnis.append(gegen)
        return ergebnis

    def fluss(self, start, richtung="herkunft"):
        """Alles, was entlang des Materialflusses vor (Herkunft) oder nach
        (Verwendung) `start` liegt: {ref: stufe} für Schritte und Materialien.

        Herkunft: Material <- erzeugender Schritt <- seine Inputs (eine Stufe
        tiefer) und seine Vorgänger im Ablauf (gleiche Stufe). Erzeugt ein
        Vorgänger selbst ein Material, zählt dieses als nächste Stufe — so
        landen lose Teststreifen unter der Dose, nicht daneben. Verwendung
        spiegelbildlich. Gemeinsame Zwischenprodukte (ein Pool für mehrere
        Lösungen) erscheinen in jeder Kette, in die sie eingehen."""
        import heapq
        if not (self.modell and self.modell.fluss):
            raise Fehler("Für Herkunft und Verwendung fehlt der Abschnitt „fluss“ in regeln.json.")
        ein, aus = self._fl("input"), self._fl("output")
        typ = start.partition("/")[0]
        stufe, offen = {}, []          # kleinste Stufe zuerst, damit jedes Objekt seine kleinste bekommt

        def besuche(ref, s):
            if s < stufe.get(ref, 1 << 30):
                stufe[ref] = s
                heapq.heappush(offen, (s, ref))
        if typ == "Prozess":
            for s_ in self.db.get_connected(start, "in", target_collection="Prozessschritt"):
                besuche(s_, 0)
        else:
            besuche(start, 0)
        while offen:
            s, ref = heapq.heappop(offen)
            if s > stufe.get(ref, s):
                continue
            if ref.startswith("Material/"):
                nach = self.db.get_connected(ref, "in", aus if richtung == "herkunft" else ein)
                for schritt in nach:
                    besuche(schritt, s)
                continue
            if richtung == "herkunft":
                for m in self.db.get_connected(ref, "out", ein):
                    besuche(m, s + 1)
                for v in self._folge(ref, "zurueck"):
                    erzeugt = self.db.get_connected(v, "out", aus)
                    for m in erzeugt:
                        besuche(m, s + 1)
                    if not erzeugt:
                        besuche(v, s)
            else:
                for m in self.db.get_connected(ref, "out", aus):
                    besuche(m, s + 1)
                for n in self._folge(ref, "vor"):
                    besuche(n, s)
        if richtung == "herkunft" and typ == "Material":
            for s_ in self._nachbehandlung(start):
                besuche(s_, 0)
        return stufe

    def _nachbehandlung(self, material):
        """Was nach dem erzeugenden Schritt mit dem Material geschieht, ohne
        dass es eine neue Materialnummer bekommt: Nachfolger, solange kein
        anderes Material eingeht und keins entsteht (Kalibrierung, Lagerung) —
        die Montage, in die weitere Teile eingehen, gehört nicht mehr dazu."""
        ein, aus = self._fl("input"), self._fl("output")
        gesehen, offen = set(), list(self.db.get_connected(material, "in", aus))
        while offen:
            for n in self._folge(offen.pop(), "vor"):
                if n in gesehen:
                    continue
                if self.db.get_connected(n, "out", aus) or set(self.db.get_connected(n, "out", ein)) - {material}:
                    continue
                gesehen.add(n)
                offen.append(n)
        return gesehen

    def fluss_bereich(self, start, richtung="herkunft"):
        """Der Bereich entlang des Flusses, je Typ: Schritte und Materialien aus
        `fluss`, alles andere von diesen Schritten aus wie im inneren Kreis
        (Prozess, Equipment, Parameter, Raum …)."""
        st = self.fluss(start, richtung)
        schritte = [r for r in st if r.startswith("Prozessschritt/")]
        ergebnis = {"Prozessschritt": set(schritte), "Material": {r for r in st if r.startswith("Material/")}}
        for typ, wege in self.kurze_wege("Prozessschritt", 3).items():
            if typ in ("Prozessschritt", "Material"):
                continue
            ziel = ergebnis.setdefault(typ, set())
            for s_ in schritte:
                for p in wege:
                    ziel |= self._gehen(s_, p)
        ergebnis["Material"].discard(start)
        return {t: sorted(v) for t, v in ergebnis.items() if v}

    def produkte(self):
        """Endprodukte: Materialien vom Produkttyp, die nirgends mehr eingehen."""
        f = self.modell.fluss if self.modell else None
        if not f:
            return []
        return sorted(f"Material/{k}" for k, d in self.db.list_nodes("Material", readonly=True).items()
                      if self._ist(d, f["produkt"]) and not self.db.get_connected(f"Material/{k}", "in", self._fl("input")))

    def produktbaum(self):
        """Seitenleiste: Endprodukt ▸ Komponenten (Stufe 1 der Herkunft)."""
        kinder = {}
        for p in self.produkte():
            kinder[p] = sorted(r for r, s in self.fluss(p).items() if s == 1 and r.startswith("Material/"))
        return {"kinder": kinder, "ohne": []}

    def _produktspalten(self, typ, refs):
        """Zusatzspalten für Material: in welche Endprodukte es eingeht und auf
        welcher Stufe (0 = das Produkt, 1 = direkte Komponente …)."""
        if typ != "Material" or not (self.modell and self.modell.fluss):
            return [], {}
        fuer, stufe = {}, {}
        for p in self.produkte():
            for r, s in self.fluss(p).items():
                if r.startswith("Material/"):
                    fuer.setdefault(r, set()).add(p)
                    stufe[r] = min(stufe.get(r, s), s)
        spalten = [{"key": "x:produkt", "titel": "für Produkt", "typ": "Material", "pfad": [], "standard": True,
                    "weg": "Endprodukte, in die das Material über den Fluss eingeht"},
                   {"key": "x:stufe", "titel": "Stufe", "typ": "", "pfad": [], "standard": True, "text": True,
                    "weg": "0 = Endprodukt, 1 = geht direkt ein, 2 = in eine Komponente …"}]
        werte = {r: {"x:produkt": sorted(fuer.get(r, [])), "x:stufe": [str(stufe[r])] if r in stufe else []} for r in refs}
        return spalten, werte

    def bereich(self, anker=None, max_hops=3):
        """Was zum Anker gehört, je Typ {typ: [refs]} — Equipments im Raum (über
        die Plant), Schritte am Prozess. Ohne Anker: alle Objekte."""
        if anker is None:
            return {_anzeige(t): sorted(f"{t}/{k}" for k in self.db.list_nodes(t, readonly=True))
                    for t in self.db.list_collections()}
        if self.db.get_node(anker, readonly=True) is None:
            raise Fehler("Dieses Objekt gibt es nicht (mehr).")
        ergebnis = {}
        for typ, wege in self.kurze_wege(_anzeige(anker.partition("/")[0]), max_hops).items():
            refs = set()
            for p in wege:
                refs |= self._gehen(anker, p)
            if refs:
                ergebnis[typ] = sorted(refs)
        return ergebnis

    def baum(self, oben, unten):
        """Für die Seitenleiste: je Objekt des oberen Typs die des unteren, die
        zu ihm gehören (Raum ▸ Plant über die Equipments). `ohne`: untere ohne
        oberes."""
        oben, unten = _anzeige(self._typ_b(oben)), _anzeige(self._typ_b(unten))
        wege = self.kurze_wege(oben, 3).get(unten, [])
        kinder, mit = {}, set()
        for kid in self.db.list_nodes(self._typ_b(oben), readonly=True):
            ref = f"{self._typ_b(oben)}/{kid}"
            k = set()
            for p in wege:
                k |= self._gehen(ref, p)
            kinder[ref] = sorted(k)
            mit |= k
        ohne = sorted(f"{self._typ_b(unten)}/{k}" for k in self.db.list_nodes(self._typ_b(unten), readonly=True)
                      if f"{self._typ_b(unten)}/{k}" not in mit)
        return {"kinder": kinder, "ohne": ohne}

    def spalten(self, typ, max_hops=2):
        """Abgeleitete Spalten eines Typs aus dem Modell: je Zieltyp der kürzeste
        Weg; mehrere gleich kurze (Material als Input und als Output) werden je
        eine Spalte. Sichtbar ab Werk: direkte Nachbarn und die Kontexttypen
        (`kontext` in regeln.json) — der Raum eines Equipments über die Plant."""
        def art_kurz(s):
            return ("← " if s["richtung"] == "ein" else "") + re.sub(r"\s*\(\d+:\w+\)", "", s["art"])
        ergebnis = []
        for ziel, wege in sorted(self.kurze_wege(typ, max_hops).items()):
            zusatz = [""] * len(wege)
            if len(wege) > 1:
                for kandidat in ([art_kurz(p[-1]) for p in wege],
                                 [art_kurz(p[0]) for p in wege],
                                 [f"über {p[-2]['typ']}" if len(p) > 1 else art_kurz(p[-1]) for p in wege],
                                 [" ".join(art_kurz(s) for s in p) for p in wege]):
                    if len(set(kandidat)) == len(wege):
                        zusatz = kandidat
                        break
            for p, z in zip(wege, zusatz):
                ergebnis.append({
                    "key": "|".join(f"{s['richtung']}:{s['art']}:{s['typ']}" for s in p),
                    "titel": f"{ziel} ({z})" if z else ziel, "typ": ziel, "pfad": p,
                    "weg": " ".join(f"{'←' if s['richtung'] == 'ein' else '→'} {s['art']} {'←' if s['richtung'] == 'ein' else '→'} {s['typ']}" for s in p),
                    "standard": len(p) == 1 or ziel in self.modell.kontext})
        return ergebnis

    def tabelle(self, anker, typ, fluss=None):
        """Primär (Anker oder alle) → Sekundär (Typ): die Zeilen, die Zahl je Typ
        für die Auswahl darüber, die abgeleiteten Spalten und ihre Werte.
        `fluss` (herkunft/verwendung): Bereich entlang des Materialflusses."""
        typ = _anzeige(self._typ_b(typ))
        bereich = self.fluss_bereich(anker, fluss) if anker and fluss else self.bereich(anker)
        zeilen = bereich.get(typ, [])
        spalten = self.spalten(typ) if self.modell else []
        werte = {r: {s["key"]: sorted(self._gehen(r, s["pfad"])) for s in spalten} for r in zeilen}
        zs, zw = self._produktspalten(typ, zeilen)
        for r in zeilen:
            werte[r].update(zw.get(r, {}))
        return {"zahlen": {t: len(r) for t, r in bereich.items()}, "zeilen": zeilen,
                "spalten": spalten + zs, "werte": werte}

    def umfeld(self, ref):
        """Die abgeleiteten Spalten eines einzelnen Objekts (für den Inspektor)."""
        if self.db.get_node(ref, readonly=True) is None:
            raise Fehler("Dieses Objekt gibt es nicht (mehr).")
        typ = _anzeige(ref.partition("/")[0])
        spalten = self.spalten(typ) if self.modell else []
        werte = {s["key"]: sorted(self._gehen(ref, s["pfad"])) for s in spalten}
        zs, zw = self._produktspalten(typ, [ref])
        return {"spalten": spalten + zs, "werte": {**werte, **zw.get(ref, {})}}

    # --- Tabellenlayouts (wie Layout-Varianten in SAP) ---------------------

    def layouts(self):
        try:
            with open(self._layoutdatei, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def layout_speichern(self, typ, name, layout=None, vorgabe=False):
        """Variante speichern (`layout` None = löschen). Geschrieben wird über
        eine Arbeitsdatei daneben, fsync, os.replace — nie an Ort und Stelle."""
        name = (name or "").strip()
        if not name:
            raise Fehler("Der Name der Variante fehlt.")
        alle = self.layouts()
        eintrag = alle.setdefault(typ, {"varianten": {}, "vorgabe": None})
        if layout is None:
            eintrag["varianten"].pop(name, None)
            if eintrag["vorgabe"] == name:
                eintrag["vorgabe"] = None
        else:
            eintrag["varianten"][name] = {k: layout.get(k) for k in ("spalten", "sort", "absteigend", "gruppe")}
            if vorgabe:
                eintrag["vorgabe"] = name
        _schreibe_json(self._layoutdatei, alle)
        return alle

    # --- Import aus Sheets/Excel (Kopieren und Einfügen) --------------------

    def importe(self):
        try:
            with open(self._importdatei, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    @staticmethod
    def _tabelle_lesen(text):
        """Eingefügter Text -> Zeilen. Aus Sheets und Excel kommt Tabulator,
        aus einer CSV-Datei Semikolon oder Komma. Leere Zeilen fallen weg."""
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        kopf = text.split("\n", 1)[0]
        trenner = "\t" if "\t" in kopf else ";" if ";" in kopf else ","
        return [z for z in csv.reader(io.StringIO(text), delimiter=trenner) if any(x.strip() for x in z)]

    def importieren(self, typ, text, ziele, probe=True):
        """Eine eingefügte Tabelle übernehmen: erste Zeile Spaltennamen, je Spalte
        ein Ziel aus `ziele` — "" (übergehen), "name", "feld:<Feld>" oder
        "bez:<Art>|<aus/ein>|<Typ>". Schlüssel ist die ID: vorhanden -> ändern,
        sonst anlegen. Leere Zellen ändern nichts; eine Beziehungsspalte ersetzt
        die bisherigen Ziele dieser Beziehung (SAP ist die Quelle). Fehlende Räume
        und Plants werden mit angelegt.

        Alles oder nichts: mit `probe` oder bei einem Fehler wird zurückgerollt.
        Rückgabe: Zahlen, Fehler je Zeile und die Zeilen mit ihrem Ergebnis."""
        typ = self._typ_b(typ)
        zeilen = self._tabelle_lesen(text)
        if len(zeilen) < 2:
            raise Fehler("Bitte die Tabelle mit Kopfzeile einfügen (mindestens eine Datenzeile).")
        kopf, daten = zeilen[0], zeilen[1:]
        ziele = list(ziele) + [""] * (len(kopf) - len(ziele))
        nf, idf = self._namefeld(typ), self._idfeld(typ)
        schluessel = f"feld:{idf}" if idf else "name"     # mit ID im Modell ist sie der Schlüssel, nicht der Name
        if schluessel not in ziele:
            raise Fehler("Ordne eine Spalte der ID zu" + (f" ({idf})." if idf else " oder dem Namen."))
        ergebnis = {"neu": 0, "geaendert": 0, "gleich": 0, "neue_ziele": {}, "fehler": [], "zeilen": [],
                    "uebernommen": False}
        try:
            with self.db.transaction():
                self._import_lauf(typ, kopf, daten, ziele, schluessel, nf, idf, ergebnis)
                if probe or ergebnis["fehler"]:
                    raise _Probe
                ergebnis["uebernommen"] = True
        except _Probe:
            pass
        if ergebnis["uebernommen"]:
            alle = self.importe()
            alle[typ] = {**alle.get(typ, {}), **{k: z for k, z in zip(kopf, ziele) if k}}
            _schreibe_json(self._importdatei, alle)
        return ergebnis

    def _import_lauf(self, typ, kopf, daten, ziele, schluessel, nf, idf, erg):
        spalte = {z: i for i, z in enumerate(ziele) if z}
        zelle = lambda z, ziel: (z[spalte[ziel]] if spalte.get(ziel, 99999) < len(z) else "").strip()
        # Doppelte Schlüssel im eingefügten Block: der ganze Import ist abgelehnt
        gesehen = {}
        for nr, z in enumerate(daten, start=2):
            k = zelle(z, schluessel)
            if k:
                gesehen.setdefault(k if schluessel != "name" else k.casefold(), []).append(nr)
        for k, nrn in gesehen.items():
            if len(nrn) > 1:
                erg["fehler"].append({"zeile": nrn[0], "text": f"„{k}“ steht mehrfach in der Tabelle (Zeilen {', '.join(map(str, nrn))})."})
        if erg["fehler"]:
            return
        # Vorhandene Objekte je Typ nach ID und Name, einmal statt je Zeile
        index = {}

        def finde(t, wert, nur_id=False):
            if t not in index:
                i_f, n_f = self._idfeld(t), self._namefeld(t)
                ids, namen = {}, {}
                for kid, d in self.db.list_nodes(t, readonly=True).items():
                    if i_f and str(d.get(i_f, "")).strip():
                        ids[str(d[i_f]).strip()] = f"{t}/{kid}"
                    namen.setdefault(str(d.get(n_f, "")).strip().casefold(), f"{t}/{kid}")
                index[t] = (ids, namen)
            ids, namen = index[t]
            return ids.get(wert) if nur_id else ids.get(wert) or namen.get(wert.casefold())

        def merke(t, ref, wert, als_id):
            finde(t, "")
            index[t][0 if als_id else 1][wert if als_id else wert.casefold()] = ref

        felder_spalten = [z for z in spalte if z.startswith("feld:")]
        bez_spalten = [z for z in spalte if z.startswith("bez:")]
        for nr, z in enumerate(daten, start=2):
            k = zelle(z, schluessel)
            if not k:
                erg["fehler"].append({"zeile": nr, "text": "Die ID fehlt." if schluessel != "name" else "Der Name fehlt."})
                continue
            felder = {f[5:]: zelle(z, f) for f in felder_spalten if zelle(z, f)}
            felder.pop(nf, None)
            name = zelle(z, "name") if "name" in spalte else ""
            try:
                ref = finde(typ, k, nur_id=schluessel != "name")
                aenderungen = []
                if ref:
                    alt = self.db.get_node(ref, readonly=True)
                    neu_f = {f: v for f, v in felder.items() if str(alt.get(f, "")) != v}
                    aenderungen = [f for f in neu_f]
                    if name and name != alt.get(nf):
                        aenderungen.insert(0, nf)
                    if neu_f or (name and name != alt.get(nf)):
                        self.knoten_aendern(ref, name=name if name and name != alt.get(nf) else None, felder=neu_f)
                    status = "geändert" if aenderungen else "gleich"
                else:
                    ref = self._erzeuge(typ, name or k, felder)
                    merke(typ, ref, k, schluessel != "name")
                    status = "neu"
                for b in bez_spalten:
                    wert = zelle(z, b)
                    if not wert:
                        continue
                    art, richt, zt = b[4:].split("|")
                    zt = self._typ_b(zt)
                    soll = set()
                    for w in (x.strip() for x in wert.split(";")):
                        if not w:
                            continue
                        ziel = finde(zt, w)
                        if not ziel:
                            ziel = self._erzeuge(zt, w, None)
                            merke(zt, ziel, w, False)
                            erg["neue_ziele"].setdefault(zt, []).append(w)
                        soll.add(ziel)
                    rel = self._art_b(art)
                    ist = {}
                    for eid, e in self.db.get_connected_edges(ref, "out" if richt == "aus" else "in", rel):
                        gegen = e["target"] if richt == "aus" else e["source"]
                        if gegen.startswith(zt + "/"):
                            ist[gegen] = eid
                    for gegen, eid in ist.items():
                        if gegen not in soll:
                            self.db.delete_edge(eid)
                    for ziel in soll - set(ist):
                        self.verknuepfe(ref, ziel, art, richt)
                    if set(ist) != soll:
                        aenderungen.append(art)
                        if status == "gleich":
                            status = "geändert"
            except Fehler as f:
                erg["fehler"].append({"zeile": nr, "text": str(f)})
                continue
            erg[{"neu": "neu", "geändert": "geaendert", "gleich": "gleich"}[status]] += 1
            if len(erg["zeilen"]) < 500:
                erg["zeilen"].append({"zeile": nr, "ref": ref, "name": name or k, "status": status, "aenderungen": aenderungen})

    def _regel_reichweite(self, quellen, tiefe, typen):
        """Reichweite entlang der Metapfade: jeder Schritt ist ein flatgraph-
        `get_connected` mit Kantenart, Richtung und Zieltyp — es wird nur
        gegangen, was das Regelwerk vorsieht."""
        tiefe = max(1, min(int(tiefe), 4))
        ziele = {self._typ_b(t) for t in typen} if typen else None
        pfade_je_typ, ergebnis = {}, {}
        for start in quellen:
            if self.db.get_node(start, readonly=True) is None:
                continue
            typ0 = start.partition("/")[0]
            if typ0 not in pfade_je_typ:
                pfade_je_typ[typ0] = [p for p in self.metapfade(_anzeige(typ0), None, tiefe)
                                      if ziele is None or self._typ_b(p[-1]["typ"]) in ziele]
            tiefen = {}
            for pfad in pfade_je_typ[typ0]:
                for z in self._gehen(start, pfad):
                    tiefen[z] = min(tiefen.get(z, 99), len(pfad))
            ergebnis[start] = tiefen
        return ergebnis

    def reichweite(self, quellen, richtung="beide", tiefe=2, arten=None, halt=False, modus="frei", typen=None):
        """Wer ist von jeder Quelle aus erreichbar — mit flatgraphs `traverse`.

        Rückgabe {quelle: {ziel: kürzeste Tiefe}}. Richtung aus | ein | beide;
        `arten` (Anzeigenamen) beschränkt die Wege auf diese Beziehungsarten.
        `halt`: der Weg läuft nicht durch Objekte vom Typ der Quelle — ein
        gleichartiges Objekt ist nur als direkter Nachbar erreichbar. So
        hängt eine Nachbar-Parameter nicht „an“ dieser, nur weil beide im
        selben Prozessschritt liegen.
        Die Tiefe je Ziel ergibt sich aus Läufen mit wachsender Tiefe.
        `modus="regel"`: nur entlang der Wege des Regelwerks (Tiefe = Zahl der
        Typ-Schritte, höchstens 4), optional nur zu den Zieltypen `typen`."""
        if modus == "regel":
            return self._regel_reichweite(quellen, tiefe, typen)
        richt = {"aus": "out", "ein": "in", "beide": "both"}.get(richtung)
        if richt is None:
            raise Fehler(f"Unbekannte Richtung „{richtung}“.")
        tiefe = max(1, min(int(tiefe), 6))
        arten_b = {self._art_b(a) for a in arten} if arten else None
        ergebnis = {}
        for start in quellen:
            if self.db.get_node(start, readonly=True) is None:
                continue
            gleich = start.partition("/")[0] + "/"

            def kante_ok(k, start=start, gleich=gleich):
                if arten_b is not None and k["type"] not in arten_b:
                    return False
                if halt and start != k["source"] and start != k["target"]:
                    return not (k["source"].startswith(gleich) or k["target"].startswith(gleich))
                return True
            tiefen = {}
            for d in range(1, tiefe + 1):
                for ziel in self.db.traverse(start, direction=richt, max_depth=d, kantenfilter=kante_ok):
                    tiefen.setdefault(ziel, d)
            ergebnis[start] = tiefen
        return ergebnis

    def _erreichbar(self, start, ziel, art):
        """Führt von `start` über gerichtete Kanten dieser Art ein Weg zu `ziel`?"""
        gesehen, offen = set(), [start]
        while offen:
            r = offen.pop()
            if r == ziel:
                return True
            if r in gesehen:
                continue
            gesehen.add(r)
            for eid, k in self.db.get_connected_edges(r, "out", art):
                if k.get("gerichtet", True):
                    offen.append(k["target"])
        return False

    # --- Schreiben ---------------------------------------------------------

    def _finde(self, typ, name):
        gesucht = name.strip().casefold()
        nf = self._namefeld(typ)
        for kid, d in self.db.list_nodes(typ, readonly=True).items():
            if str(d.get(nf, "")).strip().casefold() == gesucht:
                return f"{typ}/{kid}"
        return None

    def knoten_anlegen(self, typ, name, felder=None):
        """Legt an — oder liefert den vorhandenen gleichen Namens im Typ.
        Rückgabe (ref, neu)."""
        name = (name or "").strip()
        if not name:
            raise Fehler("Name fehlt.")
        typ = self._typ_b(typ)
        idf = self._idfeld(typ)
        neue_id = str((felder or {}).get(idf, "") if idf else "").strip()
        vorhanden = self._finde(typ, name)
        alte_id = str((self.db.get_node(vorhanden, readonly=True) or {}).get(idf, "")).strip() if vorhanden and idf else ""
        # Gleicher Name heisst dasselbe Objekt — ausser beide tragen verschiedene
        # IDs: in SAP darf es zwei „Waage“ geben
        if vorhanden and not (neue_id and alte_id and neue_id != alte_id):
            return vorhanden, False
        return self._erzeuge(typ, name, felder), True

    def _erzeuge(self, typ, name, felder):
        self._pruefe_werte(typ, felder)
        self._pruefe_id(typ, felder)
        basis = _kennung(name)
        kid, i = basis, 1
        while self.db.get_node(f"{typ}/{kid}", readonly=True) is not None:
            i += 1
            kid = f"{basis}_{i}"
        nf = self._namefeld(typ)
        daten = {nf: name}
        for k, v in (felder or {}).items():
            if k and k != nf and not k.startswith("_"):
                daten[k] = v
        self.db.create_node(typ, kid, daten)
        return f"{typ}/{kid}"

    def anlegen_im_kontext(self, typ, name, felder=None, verknuepfen=None):
        """Anlegen mit Werten und Verknüpfungen aus dem aktiven Filter (der
        Explorer schlägt sie vor): alles oder nichts.
        `verknuepfen`: [{ziel, art, richtung: aus|ein|ungerichtet}].
        Rückgabe (ref, neu); gab es das Objekt schon, bleiben seine Felder
        unberührt und nur die Verknüpfungen kommen hinzu."""
        with self.db.transaction():
            ref, neu = self.knoten_anlegen(typ, name, felder)
            for v in verknuepfen or []:
                self.verknuepfe(ref, v["ziel"], v.get("art"), v.get("richtung", "aus"), v.get("felder"))
        return ref, neu

    def _fremde_felder(self, typ):
        """Feldnamen, die das Modell anderen Typen zuordnet, diesem aber nicht."""
        eigene = {f["name"] for f in self.modell.typen[typ]["felder"]} if self.modell and typ in self.modell.typen else set()
        alle = {f["name"] for t in (self.modell.typen.values() if self.modell else []) for f in t["felder"]}
        return alle - eigene

    def mehrere_aendern(self, refs, felder=None, verknuepfen=None):
        """Felder setzen und/oder verknüpfen für viele Objekte: alles oder nichts.
        Ein Feld, das das Modell einem anderen Typ zuordnet (EquipmentGXP an einer
        Plant), wird dort übersprungen statt angelegt. Den Namen gibt es nur einzeln.
        Rückgabe (gesetzt, übersprungen)."""
        felder = {k.strip(): v for k, v in (felder or {}).items() if k and k.strip() and not k.startswith("_")}
        gesetzt = uebersprungen = 0
        with self.db.transaction():
            for ref in refs:
                typ = ref.partition("/")[0]
                if self._namefeld(typ) in felder:
                    raise Fehler("Der Name lässt sich nicht für mehrere Objekte auf einmal setzen.")
                ok = {k: v for k, v in felder.items() if k not in self._fremde_felder(typ)}
                if felder:
                    if ok:
                        self.knoten_aendern(ref, felder=ok)
                        gesetzt += 1
                    else:
                        uebersprungen += 1
                for v in verknuepfen or []:
                    if v["ziel"] != ref:
                        self.verknuepfe(ref, v["ziel"], v.get("art"), v.get("richtung"))
        return gesetzt, uebersprungen

    def mehrere_loeschen(self, refs):
        with self.db.transaction():
            for ref in refs:
                self.knoten_loeschen(ref)

    def mehrere_zurueck(self, refs):
        with self.db.transaction():
            for ref in refs:
                self.knoten_zurueck(ref)

    def knoten_aendern(self, ref, name=None, felder=None, notiz=None):
        typ, _, kid = ref.partition("/")
        nf = self._namefeld(typ)
        daten = {}
        if name is not None:
            if not name.strip():
                raise Fehler("Name darf nicht leer sein.")
            daten[nf] = name.strip()
        self._pruefe_werte(typ, felder)
        self._pruefe_id(typ, felder, ausser=ref)
        for k, v in (felder or {}).items():
            k = k.strip()
            if k and k != nf and not k.startswith("_"):
                daten[k] = v
        if notiz is not None:
            daten["notiz"] = notiz
        if not daten:
            return
        with self.db.transaction():
            alt = (self.db.get_node(ref, readonly=True) or {}).get(nf)
            self.db.update_node(typ, kid, daten)
            if name is not None and alt and alt != daten[nf]:
                self._erwaehnungen_umbenennen(alt, daten[nf])

    def _erwaehnungen_umbenennen(self, alt, neu):
        """[[Alt]] in allen Notizen -> [[Neu]], damit Verweise eine Umbenennung überleben."""
        muster = re.compile(r"\[\[\s*" + re.escape(alt) + r"\s*\]\]", re.IGNORECASE)
        for typ in self.db.list_collections():
            for kid, d in self.db.list_nodes(typ, readonly=True).items():
                n = d.get("notiz")
                if isinstance(n, str) and muster.search(n):
                    self.db.update_node(typ, kid, {"notiz": muster.sub(lambda _: f"[[{neu}]]", n)})

    def knoten_loeschen(self, ref):
        typ, _, kid = ref.partition("/")
        self.db.soft_delete(typ, kid)

    def knoten_zurueck(self, ref):
        typ, _, kid = ref.partition("/")
        self.db.restore_node(typ, kid)

    def _pruefe_kantenfelder(self, art_b, felder):
        if not felder:
            return {}
        erlaubt = {}
        if self.modell:
            kanon = self._art_zeigen(art_b)
            erlaubt = {f["name"]: f for f in self.modell.kantenfelder.get(kanon, [])}
            for k, v in felder.items():
                if k not in erlaubt:
                    raise Fehler(f"„{kanon}“ hat kein Attribut „{k}“.")
                o = erlaubt[k]["optionen"]
                if v not in ("", None) and o and v not in o:
                    raise Fehler(f"„{v}“ ist für {k} nicht vorgesehen. Erlaubt: {', '.join(o)}.")
        return {k: v for k, v in felder.items() if v not in ("", None)}

    def _regeln_gelten(self):
        return bool(self.strikt and self.modell and self.modell.regeln)

    def _regelfehler(self, tv, art, tn):
        zw = self.modell.zwischen(tv, tn)
        if not zw:
            return f"Zwischen {tv} und {tn} gibt es im Modell keine Beziehung."
        erlaubt = "; ".join(f"{tv if r == 'aus' else tn} {a} {tn if r == 'aus' else tv}" for a, r in zw)
        return (f"„{tv} {art} {tn}“ gibt es im Modell nicht. "
                f"Zwischen diesen Typen erlaubt: {erlaubt}.")

    def verknuepfe(self, ref, ziel, art=None, richtung=None, felder=None):
        """Verknüpft `ref` mit `ziel`. Mit Regelwerk ergeben sich Art und
        Richtung aus den beiden Typen, wenn es genau eine Möglichkeit gibt;
        sind es mehrere, müssen Art und Richtung kommen. Rückgabe: neu angelegt?"""
        if art and self.modell:
            self._art_b(art)                    # eine unbekannte Art wird nicht stillschweigend ersetzt
        if self._regeln_gelten():
            tv, tn = ref.partition("/")[0], ziel.partition("/")[0]
            art_k = self.modell.kanon_art(art) if art else None
            moegl = self.modell.zwischen(tv, tn, art_k)
            if len(moegl) == 1:
                # Bei einer Selbstbeziehung (Schritt führt zu Schritt) gibt es nur
                # eine Regel, aber zwei Richtungen: die verlangte bleibt.
                art, r0 = moegl[0]
                richtung = richtung if tv == tn and richtung in ("aus", "ein") else r0
            elif not moegl:
                raise Fehler(self._regelfehler(tv, art_k or art or "…", tn))
            elif not (art and richtung):
                raise Fehler(f"Zwischen {tv} und {tn} gibt es mehrere Beziehungen "
                             f"({', '.join(sorted({a for a, _ in moegl}))}) — bitte die Art wählen.")
        if not art:
            raise Fehler("Die Beziehungsart fehlt.")
        von, nach = (ziel, ref) if richtung == "ein" else (ref, ziel)
        return self.kante_anlegen(von, nach, art, gerichtet=richtung != "ungerichtet", felder=felder)

    def kante_anlegen(self, von, nach, art, gerichtet=True, felder=None):
        """Gleiche Kante zweimal ist kein Fehler, sondern nichts zu tun."""
        art_k = self.modell.kanon_art(art) if self.modell else None
        art = self._art_b(art)
        if self._regeln_gelten():
            tv, tn = von.partition("/")[0], nach.partition("/")[0]
            if not any(r == "aus" for _, r in self.modell.zwischen(tv, tn, art_k)):
                raise Fehler(self._regelfehler(tv, art_k, tn))
        meta = self._pruefe_kantenfelder(art, felder)
        if von == nach:
            raise Fehler("Ein Objekt kann nicht mit sich selbst verknüpft werden.")
        # flatgraph weist nur ungerichtete Doppel ab; gerichtete wären zwei Kanten
        for _, k in self.db.get_connected_edges(von, "both", art):
            if {k["source"], k["target"]} == {von, nach} and (
                    not gerichtet or not k.get("gerichtet", True)
                    or (k["source"], k["target"]) == (von, nach)):
                return False
        try:
            self.db.create_edge(von, nach, art, meta=meta or None, gerichtet=gerichtet)
        except KanteExistiert:
            return False
        return True

    def kante_loeschen(self, kid):
        self.db.delete_edge(kid)

    def kante_aendern(self, kid, felder):
        """Kanten-Attribute setzen. flatgraph kann eine Kante nicht ändern:
        neu anlegen mit denselben Enden (neue Kennung), alte weg."""
        alt = self.db.get_edge(kid)
        if alt is None:
            raise Fehler("Die Beziehung gibt es nicht mehr.")
        meta = {k: v for k, v in alt.items() if k not in _STAMM and not k.startswith("_")}
        meta.update(felder or {})
        meta = self._pruefe_kantenfelder(alt["type"], meta)
        with self.db.transaction():
            self.db.delete_edge(kid)
            self.db.create_edge(alt["source"], alt["target"], alt["type"], meta=meta or None,
                                gerichtet=alt.get("gerichtet", True))

    def einhaengen(self, eltern, kind, art, alte_kante=None, umgekehrt=False):
        """Kind unter Eltern hängen; mit `alte_kante` wird verschoben
        (alte Kante weg, neue hin — in einem Zug). `umgekehrt`: die Art liest
        sich vom Kind zum Elternteil („ist Teil von“, „gehört zu“), die Kante
        läuft dann Kind -> Eltern."""
        if art and self.modell:
            self._art_b(art)
        if self._regeln_gelten():
            tv, tk = eltern.partition("/")[0], kind.partition("/")[0]
            art_k = self.modell.kanon_art(art) if art else None
            moegl = self.modell.zwischen(tv, tk, art_k)
            if len(moegl) == 1:
                art, umgekehrt = moegl[0][0], moegl[0][1] == "ein"   # Richtung folgt dem Regelwerk
            elif not moegl:
                raise Fehler(self._regelfehler(tv, art_k or art or "…", tk))
            elif not art:
                raise Fehler(f"Zwischen {tv} und {tk} gibt es mehrere Beziehungen — bitte die Art wählen.")
        if not art:
            raise Fehler("Die Beziehungsart fehlt.")
        art_b = self._art_b(art)
        von, nach = (kind, eltern) if umgekehrt else (eltern, kind)
        if von == nach or self._erreichbar(nach, von, art_b):
            raise Fehler("Das wäre ein Kreis: das Ziel hängt schon unter diesem Objekt.")
        with self.db.transaction():
            if alte_kante:
                self.db.delete_edge(alte_kante)
            return self.kante_anlegen(von, nach, art)

    def loesen(self, kante):
        self.db.delete_edge(kante)

    def massen(self, text, standardtyp, art=None, unter=None, umgekehrt=False):
        """Mehrere Zeilen auf einmal. Einrücken = „hängt unter der Zeile darüber“
        (mit `art`), ohne Einrücken unter `unter`, falls angegeben.
        Rückgabe (Anzahl neu, Anzahl vorhanden, Hinweise)."""
        if art:
            self._art_b(art)  # früh ablehnen, bevor etwas angelegt wird
        standardtyp = self._typ_b(standardtyp)
        neu = alt = 0
        hinweise = []
        stapel = []  # (Einzug, ref)
        with self.db.transaction():
            for roh in text.splitlines():
                if not roh.strip():
                    continue
                einzug = len(roh) - len(roh.lstrip())
                einzug += roh[:einzug].count("\t") * 3  # Tab zählt wie 4 Stellen
                typ, name = _teile_typ(roh.strip(), standardtyp)
                ref, ist_neu = self.knoten_anlegen(typ, name)
                neu, alt = (neu + 1, alt) if ist_neu else (neu, alt + 1)
                while stapel and stapel[-1][0] >= einzug:
                    stapel.pop()
                eltern = stapel[-1][1] if stapel else unter
                if eltern:
                    try:
                        self.einhaengen(eltern, ref, art, umgekehrt=umgekehrt)
                    except Fehler as f:
                        hinweise.append(f"„{name}“ nicht eingehängt: {f}")
                stapel.append((einzug, ref))
        return neu, alt, hinweise


# --- HTTP ------------------------------------------------------------------

def _aktionen(ed):
    def anlegen(d):
        ref, neu = ed.anlegen_im_kontext(d["typ"], d["name"], d.get("felder"),
                                         d.get("verknuepfen"))
        if d.get("unter") and ref != d["unter"]:
            ed.einhaengen(d["unter"], ref, d.get("art"), umgekehrt=d.get("umgekehrt", False))
        return {"ref": ref, "hinweis": None if neu else "Gab es schon – nicht doppelt angelegt."}

    def massen(d):
        neu, alt, hinweise = ed.massen(d["text"], d["typ"], d.get("art"), d.get("unter"),
                                       d.get("umgekehrt", False))
        teile = [f"{neu} angelegt"] + ([f"{alt} vorhanden"] if alt else []) + hinweise
        return {"hinweis": ", ".join(teile)}

    def aendern(d):
        ed.knoten_aendern(d["ref"], d.get("name"), d.get("felder"), d.get("notiz"))

    def loeschen(d):
        ed.knoten_loeschen(d["ref"])

    def zurueck(d):
        ed.knoten_zurueck(d["ref"])

    def reichweite(d):
        sp = d.get("spec") or {}
        return {"reichweite": ed.reichweite(d["quellen"], sp.get("richtung", "beide"), sp.get("tiefe", 2),
                                            sp.get("arten") or None, bool(sp.get("halt")),
                                            sp.get("modus", "frei"), sp.get("typen") or None), "_nur": True}

    def metapfade(d):
        return {"metapfade": ed.metapfade(d["von"], d.get("nach"), int(d.get("tiefe", 2))), "_nur": True}

    def tabelle(d):
        return {**ed.tabelle(d.get("anker"), d["typ"], d.get("fluss")), "_nur": True}

    def baum(d):
        return {"baeume": [ed.produktbaum() if b.get("art") == "produkte" else ed.baum(b["oben"], b["unten"])
                           for b in d["baeume"]], "_nur": True}

    def tabelle_lesen(d):
        zeilen = ed._tabelle_lesen(d["text"])
        return {"kopf": zeilen[0] if zeilen else [], "beispiele": zeilen[1:4], "anzahl": max(0, len(zeilen) - 1), "_nur": True}

    def importieren(d):
        return {"import": ed.importieren(d["typ"], d["text"], d["ziele"], bool(d.get("probe", True)))}

    def umfeld(d):
        return {**ed.umfeld(d["ref"]), "_nur": True}

    def layout(d):
        return {"layouts": ed.layout_speichern(d["typ"], d["name"], d.get("layout"), bool(d.get("vorgabe"))),
                "_nur": True}

    def mehrere_aendern(d):
        g, u = ed.mehrere_aendern(d["refs"], d.get("felder"), d.get("verknuepfen"))
        teile = ([f"{g} Objekte geändert"] if d.get("felder") else []) + ([f"{u} übersprungen (Feld gehört zu anderem Typ)"] if u else [])
        if d.get("verknuepfen"):
            teile.append(f"{len(d['refs'])} verknüpft")
        return {"hinweis": ", ".join(teile)}

    def mehrere_loeschen(d):
        ed.mehrere_loeschen(d["refs"])

    def mehrere_zurueck(d):
        ed.mehrere_zurueck(d["refs"])

    def kante(d):
        richtung = d.get("richtung", "aus")
        ed.verknuepfe(d["ref"], d["ziel"], d.get("art"), richtung, felder=d.get("felder"))

    def kante_felder(d):
        ed.kante_aendern(d["id"], d["felder"])

    def kante_weg(d):
        ed.kante_loeschen(d["id"])

    def einhaengen(d):
        ed.einhaengen(d["eltern"], d["kind"], d["art"], d.get("alte_kante"),
                      d.get("umgekehrt", False))

    return {"anlegen": anlegen, "massen": massen, "aendern": aendern,
            "loeschen": loeschen, "zurueck": zurueck, "kante": kante,
            "kante_weg": kante_weg, "kante_felder": kante_felder,
            "mehrere_aendern": mehrere_aendern, "mehrere_loeschen": mehrere_loeschen,
            "mehrere_zurueck": mehrere_zurueck, "reichweite": reichweite, "metapfade": metapfade,
            "einhaengen": einhaengen, "tabelle": tabelle, "umfeld": umfeld, "layout": layout,
            "importieren": importieren, "baum": baum, "tabelle_lesen": tabelle_lesen}


def bedienen(ed, port):
    aktionen = _aktionen(ed)

    class H(BaseHTTPRequestHandler):
        def _senden(self, code, inhalt, art="application/json; charset=utf-8"):
            if not isinstance(inhalt, bytes):
                inhalt = json.dumps(inhalt, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", art)
            self.send_header("Content-Length", str(len(inhalt)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(inhalt)

        def do_GET(self):
            if self.path == "/api/zustand":
                return self._senden(200, {"zustand": ed.zustand(), "layouts": ed.layouts(), "importe": ed.importe()})
            seiten = {"/": "atlas.html", "/index.html": "atlas.html", "/alt": "index.html"}
            if self.path in seiten:
                with open(os.path.join(HIER, seiten[self.path]), "rb") as f:
                    return self._senden(200, f.read(), "text/html; charset=utf-8")
            self._senden(404, {"fehler": "nicht gefunden"})

        def do_POST(self):
            name = self.path.removeprefix("/api/")
            if name not in aktionen:
                return self._senden(404, {"fehler": "unbekannte Aktion"})
            try:
                laenge = int(self.headers.get("Content-Length") or 0)
                daten = json.loads(self.rfile.read(laenge) or b"{}")
                antwort = aktionen[name](daten) or {}
            except Fehler as f:
                return self._senden(400, {"fehler": str(f)})
            except Exception as f:  # der Benutzer soll den Grund sehen, nicht nur "500"
                return self._senden(400, {"fehler": f"{type(f).__name__}: {f}"})
            if antwort.pop("_nur", False):      # reine Abfrage: kein Zustand mitschicken
                return self._senden(200, antwort)
            antwort["zustand"] = ed.zustand()
            self._senden(200, antwort)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"Graph-Editor: http://127.0.0.1:{port}/   (Strg+C beendet)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        ed.schliessen()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--bestand", default=os.path.join(HIER, "bestand"))
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--modell", default=os.path.join(HIER, "modell.json"),
                   help="Datenmodell (JSON-Schema); 'keins' = ohne Modell, alles frei")
    p.add_argument("--regeln", default=os.path.join(HIER, "regeln.json"),
                   help="Regelwerk Typ-Art-Typ (Annahmen, warnt nur); 'keine' = ohne")
    p.add_argument("--locker", action="store_true",
                   help="Beziehungspaare des Modells nicht erzwingen (nur Arten prüfen)")
    a = p.parse_args()
    modell = None if a.modell == "keins" else Modell(a.modell)
    if modell and a.regeln != "keine" and os.path.exists(a.regeln):
        for r in modell.lade_wegregeln(a.regeln):
            print("Eintrag in regeln.json passt nicht zum Modell, ignoriert:", r)
    bedienen(Editor(a.bestand, modell, strikt=not a.locker), a.port)
