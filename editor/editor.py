"""
Graph-Editor: Objekte erfassen, per Ziehen verknüpfen, als Baum lesen.

    python editor/editor.py [--bestand PFAD] [--port 8765]

Läuft nur lokal (127.0.0.1), ein Prozess, nur Standardbibliothek. Der
Bestand ist ein normaler flatgraph-Bestand; hier steht kein Modell vorab —
Typen und Beziehungsarten entstehen beim ersten Gebrauch.

Die Logik steckt in `Editor` (ohne HTTP), damit sie sich testen lässt.
"""
import argparse
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


def _teile_typ(zeile, standardtyp):
    """„Raum: Reinraum C“ -> (Raum, Reinraum C). Ein Präfix gilt nur als Typ,
    wenn es kurz ist und keine Ziffer enthält — „Pumpe 12: Nord“ ist ein Name."""
    m = re.match(r"^([^\s:][^:\d]{0,29}):\s+(.+)$", zeile)
    if m and len(m.group(1).split()) <= 2:
        return m.group(1).strip(), m.group(2).strip()
    return standardtyp, zeile.strip()


class Editor:
    def __init__(self, pfad):
        self.db = FlatGraphDB(pfad, audit=True)

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
                knoten.append({
                    "ref": ref, "typ": _anzeige(typ),
                    "name": d.get("name") or kid,
                    "felder": {k: v for k, v in d.items()
                               if k != "name" and not k.startswith("_") and v != ""},
                })
        kanten = []
        for eid, e in db.list_edges().items():
            if e["source"] in refs and e["target"] in refs:
                kanten.append({"id": eid, "von": e["source"], "nach": e["target"],
                               "art": _anzeige(e["type"]),
                               "gerichtet": e.get("gerichtet", True)})
        return {"knoten": knoten, "kanten": kanten}

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
        for kid, d in self.db.list_nodes(typ, readonly=True).items():
            if str(d.get("name", "")).strip().casefold() == gesucht:
                return f"{typ}/{kid}"
        return None

    def knoten_anlegen(self, typ, name, felder=None):
        """Legt an — oder liefert den vorhandenen gleichen Namens im Typ.
        Rückgabe (ref, neu)."""
        name = (name or "").strip()
        if not name:
            raise Fehler("Name fehlt.")
        typ = _name_fuer_bestand(typ, "Typ")
        vorhanden = self._finde(typ, name)
        if vorhanden:
            return vorhanden, False
        basis = _kennung(name)
        kid, i = basis, 1
        while self.db.get_node(f"{typ}/{kid}", readonly=True) is not None:
            i += 1
            kid = f"{basis}_{i}"
        daten = {"name": name}
        for k, v in (felder or {}).items():
            if k and k != "name" and not k.startswith("_"):
                daten[k] = v
        self.db.create_node(typ, kid, daten)
        return f"{typ}/{kid}", True

    def knoten_aendern(self, ref, name=None, felder=None):
        typ, _, kid = ref.partition("/")
        daten = {}
        if name is not None:
            if not name.strip():
                raise Fehler("Name darf nicht leer sein.")
            daten["name"] = name.strip()
        for k, v in (felder or {}).items():
            k = k.strip()
            if k and k != "name" and not k.startswith("_"):
                daten[k] = v
        if daten:
            self.db.update_node(typ, kid, daten)

    def knoten_loeschen(self, ref):
        typ, _, kid = ref.partition("/")
        self.db.soft_delete(typ, kid)

    def knoten_zurueck(self, ref):
        typ, _, kid = ref.partition("/")
        self.db.restore_node(typ, kid)

    def kante_anlegen(self, von, nach, art, gerichtet=True):
        """Gleiche Kante zweimal ist kein Fehler, sondern nichts zu tun."""
        art = _name_fuer_bestand(art, "Beziehungsart")
        if von == nach:
            raise Fehler("Ein Objekt kann nicht mit sich selbst verknüpft werden.")
        # flatgraph weist nur ungerichtete Doppel ab; gerichtete wären zwei Kanten
        for _, k in self.db.get_connected_edges(von, "both", art):
            if {k["source"], k["target"]} == {von, nach} and (
                    not gerichtet or not k.get("gerichtet", True)
                    or (k["source"], k["target"]) == (von, nach)):
                return False
        try:
            self.db.create_edge(von, nach, art, gerichtet=gerichtet)
        except KanteExistiert:
            return False
        return True

    def kante_loeschen(self, kid):
        self.db.delete_edge(kid)

    def einhaengen(self, eltern, kind, art, alte_kante=None):
        """Kind unter Eltern hängen; mit `alte_kante` wird verschoben
        (alte Kante weg, neue hin — in einem Zug)."""
        art_b = _name_fuer_bestand(art, "Beziehungsart")
        if eltern == kind or self._erreichbar(kind, eltern, art_b):
            raise Fehler("Das wäre ein Kreis: das Ziel hängt schon unter diesem Objekt.")
        with self.db.transaction():
            if alte_kante:
                self.db.delete_edge(alte_kante)
            return self.kante_anlegen(eltern, kind, art)

    def loesen(self, kante):
        self.db.delete_edge(kante)

    def massen(self, text, standardtyp, art, unter=None):
        """Mehrere Zeilen auf einmal. Einrücken = „hängt unter der Zeile darüber“
        (mit `art`), ohne Einrücken unter `unter`, falls angegeben.
        Rückgabe (Anzahl neu, Anzahl vorhanden, Hinweise)."""
        art_b = _name_fuer_bestand(art, "Beziehungsart")
        standardtyp = _name_fuer_bestand(standardtyp, "Typ")
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
                        self.einhaengen(eltern, ref, art_b)
                    except Fehler as f:
                        hinweise.append(f"„{name}“ nicht eingehängt: {f}")
                stapel.append((einzug, ref))
        return neu, alt, hinweise


# --- HTTP ------------------------------------------------------------------

def _aktionen(ed):
    def anlegen(d):
        ref, neu = ed.knoten_anlegen(d["typ"], d["name"])
        if d.get("unter") and ref != d["unter"]:
            ed.einhaengen(d["unter"], ref, d.get("art") or "enthält")
        return {"ref": ref, "hinweis": None if neu else "Gab es schon – nicht doppelt angelegt."}

    def massen(d):
        neu, alt, hinweise = ed.massen(d["text"], d["typ"], d.get("art") or "enthält",
                                       d.get("unter"))
        teile = [f"{neu} angelegt"] + ([f"{alt} vorhanden"] if alt else []) + hinweise
        return {"hinweis": ", ".join(teile)}

    def aendern(d):
        ed.knoten_aendern(d["ref"], d.get("name"), d.get("felder"))

    def loeschen(d):
        ed.knoten_loeschen(d["ref"])

    def zurueck(d):
        ed.knoten_zurueck(d["ref"])

    def kante(d):
        richtung = d.get("richtung", "aus")
        von, nach = (d["ref"], d["ziel"]) if richtung != "ein" else (d["ziel"], d["ref"])
        ed.kante_anlegen(von, nach, d["art"], gerichtet=richtung != "ungerichtet")

    def kante_weg(d):
        ed.kante_loeschen(d["id"])

    def einhaengen(d):
        ed.einhaengen(d["eltern"], d["kind"], d["art"], d.get("alte_kante"))

    return {"anlegen": anlegen, "massen": massen, "aendern": aendern,
            "loeschen": loeschen, "zurueck": zurueck, "kante": kante,
            "kante_weg": kante_weg, "einhaengen": einhaengen}


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
                return self._senden(200, {"zustand": ed.zustand()})
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HIER, "index.html"), "rb") as f:
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
    a = p.parse_args()
    bedienen(Editor(a.bestand), a.port)
