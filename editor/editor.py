"""
Graph-Editor: Objekte erfassen, per Ziehen verknüpfen, als Baum lesen.

    python editor/editor.py [--bestand PFAD] [--port 8765] [--modell DATEI]

Läuft nur lokal (127.0.0.1), ein Prozess, nur Standardbibliothek. Der
Bestand ist ein normaler flatgraph-Bestand. Mit Modell (editor/modell.json) gelten dessen Typen, Felder und Beziehungsarten;
ohne (`--modell keins`) entstehen sie beim ersten Gebrauch.

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


_STAMM = ("source", "target", "type", "created_at", "gerichtet")


class Modell:
    """Das Datenmodell (JSON-Schema mit *Node-Definitionen und GraphEdge).

    Es wird nur gelesen: Typen mit Feldern, Auswahlwerte, erlaubte
    Beziehungsarten und Kanten-Attribute. Es erzwingt nichts beim Erfassen —
    Pflichtfelder erscheinen als Lücke, nicht als Sperre (erst fassen, dann
    festigen)."""

    def __init__(self, pfad):
        with open(pfad, encoding="utf-8") as f:
            schema = json.load(f)
        defs = schema.get("$defs", {})
        self.typen = {}
        for d in defs.values():
            typ = d.get("properties", {}).get("type", {}).get("const")
            if not typ:
                continue
            eig = d["properties"]["properties"]
            pflicht = set(eig.get("required", []))
            felder = []
            for name, spez in eig.get("properties", {}).items():
                felder.append({"name": name, "pflicht": name in pflicht,
                               "optionen": spez.get("enum"),
                               "hinweis": spez.get("description", "")})
            namefeld = next((f["name"] for f in felder if f["name"].endswith("Name")), "name")
            self.typen[typ] = {"namefeld": namefeld, "felder": felder}
        kante = defs.get("GraphEdge", {}).get("properties", {})
        # „Steht in“ und „steht in“ stehen beide im Modell: eine Art, die
        # kleingeschriebene gewinnt.
        gesehen = {}
        for a in kante.get("relation", {}).get("enum", []):
            k = a.casefold()
            if k not in gesehen or a == a.lower():
                gesehen[k] = a
        self.arten = list(gesehen.values())
        self.kantenfelder = {}  # Art -> [Feld]
        for name, spez in kante.get("properties", {}).get("properties", {}).items():
            m = re.search(r"'([^']+)'", spez.get("description", ""))
            art = self.kanon_art(m.group(1)) if m else None
            if art:
                self.kantenfelder.setdefault(art, []).append(
                    {"name": name, "optionen": spez.get("enum")})

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
                "kantenfelder": self.kantenfelder}


class Editor:
    def __init__(self, pfad, modell=None):
        self.db = FlatGraphDB(pfad, audit=True)
        self.modell = modell
        # Bestandsname einer Art -> Anzeigename (Klammern, „/“ und „:“ sind im
        # Bestand nicht erlaubt, die Anzeige soll trotzdem das Original zeigen)
        self._art_anzeige = {_name_fuer_bestand(a, "Art"): a
                             for a in (modell.arten if modell else [])}

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

    def _pruefe_werte(self, typ, felder):
        """Auswahlfelder nehmen nur Werte aus dem Modell; leer = nicht gesetzt."""
        if not (self.modell and typ in self.modell.typen):
            return
        bekannt = {f["name"]: f for f in self.modell.typen[typ]["felder"]}
        for k, v in (felder or {}).items():
            f = bekannt.get(k)
            if f and f["optionen"] and v not in ("", None) and v not in f["optionen"]:
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
                    "ref": ref, "typ": _anzeige(typ),
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
        vorhanden = self._finde(typ, name)
        if vorhanden:
            return vorhanden, False
        basis = _kennung(name)
        kid, i = basis, 1
        while self.db.get_node(f"{typ}/{kid}", readonly=True) is not None:
            i += 1
            kid = f"{basis}_{i}"
        nf = self._namefeld(typ)
        daten = {nf: name}
        self._pruefe_werte(typ, felder)
        for k, v in (felder or {}).items():
            if k and k != nf and not k.startswith("_"):
                daten[k] = v
        self.db.create_node(typ, kid, daten)
        return f"{typ}/{kid}", True

    def anlegen_im_kontext(self, typ, name, felder=None, verknuepfen=None):
        """Anlegen mit Werten und Verknüpfungen aus dem aktiven Filter (der
        Explorer schlägt sie vor): alles oder nichts.
        `verknuepfen`: [{ziel, art, richtung: aus|ein|ungerichtet}].
        Rückgabe (ref, neu); gab es das Objekt schon, bleiben seine Felder
        unberührt und nur die Verknüpfungen kommen hinzu."""
        with self.db.transaction():
            ref, neu = self.knoten_anlegen(typ, name, felder)
            for v in verknuepfen or []:
                richtung = v.get("richtung", "aus")
                von, nach = (v["ziel"], ref) if richtung == "ein" else (ref, v["ziel"])
                self.kante_anlegen(von, nach, v["art"], gerichtet=richtung != "ungerichtet")
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
                    richtung = v.get("richtung", "aus")
                    von, nach = (v["ziel"], ref) if richtung == "ein" else (ref, v["ziel"])
                    if von != nach:
                        self.kante_anlegen(von, nach, v["art"], gerichtet=richtung != "ungerichtet")
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

    def kante_anlegen(self, von, nach, art, gerichtet=True, felder=None):
        """Gleiche Kante zweimal ist kein Fehler, sondern nichts zu tun."""
        art = self._art_b(art)
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

    def massen(self, text, standardtyp, art, unter=None, umgekehrt=False):
        """Mehrere Zeilen auf einmal. Einrücken = „hängt unter der Zeile darüber“
        (mit `art`), ohne Einrücken unter `unter`, falls angegeben.
        Rückgabe (Anzahl neu, Anzahl vorhanden, Hinweise)."""
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
            ed.einhaengen(d["unter"], ref, d["art"], umgekehrt=d.get("umgekehrt", False))
        return {"ref": ref, "hinweis": None if neu else "Gab es schon – nicht doppelt angelegt."}

    def massen(d):
        neu, alt, hinweise = ed.massen(d["text"], d["typ"], d["art"], d.get("unter"),
                                       d.get("umgekehrt", False))
        teile = [f"{neu} angelegt"] + ([f"{alt} vorhanden"] if alt else []) + hinweise
        return {"hinweis": ", ".join(teile)}

    def aendern(d):
        ed.knoten_aendern(d["ref"], d.get("name"), d.get("felder"), d.get("notiz"))

    def loeschen(d):
        ed.knoten_loeschen(d["ref"])

    def zurueck(d):
        ed.knoten_zurueck(d["ref"])

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
        von, nach = (d["ref"], d["ziel"]) if richtung != "ein" else (d["ziel"], d["ref"])
        ed.kante_anlegen(von, nach, d["art"], gerichtet=richtung != "ungerichtet",
                         felder=d.get("felder"))

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
            "mehrere_zurueck": mehrere_zurueck,
            "einhaengen": einhaengen}


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
    p.add_argument("--modell", default=os.path.join(HIER, "modell.json"),
                   help="Datenmodell (JSON-Schema); 'keins' = ohne Modell, alles frei")
    a = p.parse_args()
    modell = None if a.modell == "keins" else Modell(a.modell)
    bedienen(Editor(a.bestand, modell), a.port)
