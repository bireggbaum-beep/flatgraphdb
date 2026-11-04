"""
Absturz und Plattenfehler an JEDER Stelle der Operationen, die mehr als eine
Datei berühren.

Kein Zufall: ein Zähler zählt jede Aktion, die das Dateisystem ändert
(schreibend öffnen, umbenennen, löschen, Verzeichnis anlegen/entfernen,
fsync), und an der n-ten passiert etwas — für jedes n, bis die Operation
durchläuft:

  Absturz         ein Kindprozess stirbt mit os._exit; danach öffnet ein
                  frischer Prozess den Bestand.
  Doppelabsturz   lag danach eine Absichtsdatei, stirbt auch das Öffnen
                  (das Nachholen) an jeder Stelle, dann wird sauber geöffnet.
  Plattenfehler   im selben Prozess wirft die n-te Aktion OSError(EIO);
                  danach muss der Speicher der Platte gleichen.

Zwei Arten von Operationen, nach dem Vertrag:

  unteilbar   Transaktion, soft_delete und restore_node mit Kaskade, lange
              Texte in einer Transaktion: danach genau „vorher“ oder
              „nachher“ (VERTRAG 2.3). Hing eine Absicht, „nachher“.
  aufräumend  purge (2.9), run_garbage_collection: dürfen halb fertig sein.
              Zugesagt ist: kein Knoten ausserhalb der Löschmenge ändert
              sich, ein Knoten der Löschmenge ist ganz da oder ganz weg,
              keine Kante zeigt auf einen fehlenden Knoten, kein Knoten auf
              einen fehlenden langen Text, next_id vergibt keine entfernte
              Nummer neu (7) — und ein zweiter Lauf bringt es zu Ende.

Grenze: ein Prozessende, kein Stromausfall; die Schritte sind Aufrufe, nicht
Zustände des Kernels. Ein Fehler wird nur als EIO eingespielt — ENOSPC, EROFS
nehmen in flatgraph denselben Weg (OSError).

    python tests/test_flatgraph_absturz.py
"""
import builtins
import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                       # noqa: E402

fg = lade()
results = []
WURZEL = tempfile.mkdtemp(prefix="fg_absturz_")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- Zähler
ZAEHLER = r"""
import os, shutil, builtins
_zz = {"n": 0, "an": False, "bei": 0, "wie": "exit", "gefeuert": False}
def _tick():
    if not _zz["an"]:
        return
    _zz["n"] += 1
    if _zz["n"] == _zz["bei"]:
        _zz["gefeuert"] = True
        if _zz["wie"] == "exit":
            os._exit(137)
        import errno as _e
        raise OSError(_e.EIO, "eingespielter Plattenfehler")
def _mk(echt):
    def f(*a, **k):
        _tick()
        return echt(*a, **k)
    return f
_ECHT = {}
for _name in ("replace", "rename", "remove", "unlink", "rmdir", "makedirs", "mkdir", "fsync"):
    _ECHT[_name] = getattr(os, _name)
    setattr(os, _name, _mk(_ECHT[_name]))
_ECHT["rmtree"] = shutil.rmtree
shutil.rmtree = _mk(_ECHT["rmtree"])
_ECHT["open"] = builtins.open
def _offen(datei, modus="r", *a, **k):
    if any(c in str(modus) for c in "wax+"):
        _tick()
    return _ECHT["open"](datei, modus, *a, **k)
builtins.open = _offen
"""

KIND = ZAEHLER + r"""
import sys, json, importlib.util
pfad, wurzel, bei, modus, opts, op = sys.argv[1:7]
_zz["bei"] = int(bei)
spec = importlib.util.spec_from_file_location("fgx", pfad)
m = importlib.util.module_from_spec(spec); sys.modules["fgx"] = m
spec.loader.exec_module(m)
if modus == "oeffnen":
    _zz["an"] = True
db = m.FlatGraphDB(wurzel, **json.loads(opts))
_zz["an"] = True
if modus == "op":
    exec(op, {"db": db, "fg": m})
_zz["an"] = False
db.close()
print("fertig")
"""

# Derselbe Zähler im eigenen Prozess, für den Plattenfehler. Ein eigener
# Namensraum, damit er nur während der Operation zählt.
_zaehler_ns = {}
exec(ZAEHLER, _zaehler_ns)
_zz = _zaehler_ns["_zz"]


# ---------------------------------------------------------------- Zustand
PRAEFIX = getattr(fg.FlatGraphDB, "_VAULT_TEXT_PREFIX", "@vault_text/")


def zustand(db):
    """Was der Bestand zeigt, unabhängig von Zufall (Kanten-Ids, Zeitstempel).

    Papierkorb eingeschlossen; lange Texte aufgelöst, ein fehlender heisst
    "<FEHLT>". Kanten als (Quelle, Ziel, Art, Metadaten ohne created_at).
    """
    knoten = {}
    for sam in sorted(db.list_collections()):
        if sam.startswith("_"):
            continue
        for nid, daten in db.list_nodes(sam, include_deleted=True).items():
            d = {}
            for k, v in daten.items():
                if k == "_deletion_flag":
                    v = "gesetzt"
                elif isinstance(v, str) and v.startswith(PRAEFIX):
                    p = os.path.join(db.root, v[len(PRAEFIX):])
                    v = open(p, encoding="utf-8").read() if os.path.exists(p) else "<FEHLT>"
                d[k] = v
            knoten[f"{sam}/{nid}"] = d
    kanten = sorted(
        (e["source"], e["target"], e["type"],
         tuple(sorted((k, repr(v)) for k, v in e.items()
                      if k not in ("source", "target", "type", "created_at"))))
        for e in db.list_edges().values())
    return {"knoten": knoten, "kanten": kanten}


def oeffnen(wurzel, opts):
    return fg.FlatGraphDB(wurzel, **opts)


def zustand_von(wurzel, opts):
    db = oeffnen(wurzel, opts)
    try:
        return zustand(db)
    finally:
        db.close()


def kopie(quelle):
    ziel = tempfile.mkdtemp(dir=WURZEL)
    shutil.rmtree(ziel)
    shutil.copytree(quelle, ziel)
    return ziel


# ---------------------------------------------------------------- Szenarien
def basis(db):
    for sam in ("a", "b"):
        for i in range(1, 7):
            db.create_node(sam, f"{sam}{i}", {"v": i})
    for i in range(1, 7):
        db.create_edge(f"a/a{i}", f"b/b{i}", "t")


def mit_kaskade(db):
    basis(db)
    db.create_edge("a/a1", "a/a2", "teil", cascade_delete=True)
    db.create_edge("a/a2", "b/b3", "teil", cascade_delete=True)


def kaskade_im_papierkorb(db):
    mit_kaskade(db)
    db.soft_delete("a", "a1")       # nimmt a2 und b3 mit
    db.soft_delete("a", "a5")       # unabhängig im Papierkorb


def viel_papierkorb(db):
    kaskade_im_papierkorb(db)
    with db.transaction():
        for i in range(1, 61):
            db.create_node("c", f"c{i}", {"v": i})
    for i in range(1, 51):          # dünne Fächer: der Müllsammler verdichtet
        db.soft_delete("c", f"c{i}")


def mit_langtext(db):
    basis(db)
    db.update_node("a", "a1", {"text": "alt " * 40})


SZENARIEN = [
    {"name": "Transaktion über drei Fächer", "art": "unteilbar", "bau": basis, "opts": {},
     "doppelt": True,
     "op": "with db.transaction():\n"
           "    db.update_node('a', 'a1', {'v': 100})\n"
           "    db.create_node('b', 'neu', {'v': 1})\n"
           "    db.create_edge('a/a2', 'b/neu', 't')\n"
           "    k = sorted(i for i, e in db.list_edges('t').items() if e['source'] == 'a/a3')[0]\n"
           "    db.delete_edge(k)\n"},
    {"name": "soft_delete mit Kaskade über zwei Sammlungen", "art": "unteilbar",
     "bau": mit_kaskade, "opts": {}, "op": "db.soft_delete('a', 'a1')\n"},
    {"name": "restore_node mit Kaskade", "art": "unteilbar",
     "bau": kaskade_im_papierkorb, "opts": {}, "op": "db.restore_node('a', 'a1')\n"},
    {"name": "Lange Texte in einer Transaktion", "art": "unteilbar", "bau": mit_langtext,
     "opts": {"longtext_threshold": 50},
     "op": "with db.transaction():\n"
           "    db.update_node('a', 'a1', {'text': 'neu ' * 40})\n"
           "    db.create_node('b', 'lang', {'text': 'lang ' * 40})\n"},
    {"name": "purge mit Kaskade", "art": "aufraeumend", "bau": kaskade_im_papierkorb,
     "opts": {}, "loeschmenge": {"a/a1", "a/a2", "b/b3"},
     "op": "db.purge('a', 'a1')\n",
     "nochmal": "try:\n    db.purge('a', 'a1')\nexcept (fg.KnotenFehlt, KeyError):\n    pass\n"},
    {"name": "Müllsammler mit Verdichten", "art": "aufraeumend", "bau": viel_papierkorb,
     "opts": {}, "loeschmenge": None,      # alles, was vorher im Papierkorb lag
     "op": "db.run_garbage_collection()\n", "nochmal": "db.run_garbage_collection()\n"},
]


def vorbereiten(sz):
    quelle = tempfile.mkdtemp(dir=WURZEL)
    db = oeffnen(quelle, sz["opts"])
    sz["bau"](db)
    db.close()
    vorher = zustand_von(quelle, sz["opts"])
    golden = kopie(quelle)
    db = oeffnen(golden, sz["opts"])
    exec(sz["op"], {"db": db, "fg": fg})
    im_speicher = zustand(db)
    db.close()
    nachher = zustand_von(golden, sz["opts"])
    # „nachher“ rechnet der Prüfling selbst aus. Schriebe er schon ungestört
    # falsch, gälte das Falsche als Soll — deshalb muss die Platte nach dem
    # ungestörten Lauf dem Speicher gleichen.
    sz["ungestoert_ok"] = im_speicher == nachher
    shutil.rmtree(golden)
    if sz["loeschmenge"] if "loeschmenge" in sz else False:
        menge = sz["loeschmenge"]
    elif sz["art"] == "aufraeumend":
        menge = {r for r, d in vorher["knoten"].items() if d.get("_deletion_flag")}
    else:
        menge = set()
    return quelle, vorher, nachher, menge


def bewerten(sz, ist, vorher, nachher, menge):
    """None, wenn der Zustand zugesagt ist; sonst der Grund."""
    if sz["art"] == "unteilbar":
        if ist == vorher or ist == nachher:
            return None
        return "weder vorher noch nachher"
    for ref, daten in ist["knoten"].items():
        if ref not in menge and daten != vorher["knoten"].get(ref):
            return f"{ref} ausserhalb der Löschmenge verändert"
        if ref in menge and daten != vorher["knoten"][ref]:
            return f"{ref} halb entfernt"
        if any(v == "<FEHLT>" for v in daten.values()):
            return f"{ref} zeigt auf einen fehlenden langen Text"
    fehlend = [r for r in vorher["knoten"] if r not in menge and r not in ist["knoten"]]
    if fehlend:
        return f"{fehlend[:2]} fehlen, gehörten nicht zur Löschmenge"
    for q, z, art, _ in ist["kanten"]:
        if q not in ist["knoten"] or z not in ist["knoten"]:
            return f"Kante {q} -> {z} zeigt auf einen fehlenden Knoten"
    erwartet = [k for k in vorher["kanten"] if k[0] not in menge and k[1] not in menge]
    if any(k not in ist["kanten"] for k in erwartet):
        return "eine Kante ausserhalb der Löschmenge fehlt"
    return None


def nummern_ok(wurzel, opts, vorher):
    """next_id darf keine Nummer vergeben, die es schon gab (VERTRAG 7)."""
    db = oeffnen(wurzel, opts)
    try:
        for sam in ("a", "b", "c"):
            hoechste = max((int(r.split("/", 1)[1][1:]) for r in vorher["knoten"]
                            if r.startswith(sam + "/") and r.split("/", 1)[1][1:].isdigit()),
                           default=0)
            if hoechste and int(db.next_id(sam, sam)[1:]) <= hoechste:
                return f"next_id('{sam}') vergibt {db.next_id(sam, sam)} neu"
    finally:
        db.close()
    return None


def kind(wurzel, bei, modus, sz):
    r = subprocess.run([sys.executable, "-c", KIND, fg.__pfad__, wurzel, str(bei), modus,
                        json.dumps(sz["opts"]), sz["op"]], capture_output=True, text=True)
    if r.returncode not in (0, 137):
        return "fehler", r.stderr.strip().splitlines()[-1:] or ["?"]
    return ("fertig" if "fertig" in r.stdout else "tot"), None


def nach_absturz_pruefen(sz, arbeit, vorher, nachher, menge):
    try:
        ist = zustand_von(arbeit, sz["opts"])
        ist2 = zustand_von(arbeit, sz["opts"])
    except Exception as e:
        return f"Öffnen scheitert: {type(e).__name__}: {str(e)[:80]}"
    if ist != ist2:
        return "zweites Öffnen zeigt anderes"
    grund = bewerten(sz, ist, vorher, nachher, menge)
    if grund:
        return grund
    if sz["art"] == "aufraeumend":
        grund = nummern_ok(arbeit, sz["opts"], vorher)
        if grund:
            return grund
        db = oeffnen(arbeit, sz["opts"])
        try:
            exec(sz["nochmal"], {"db": db, "fg": fg})
        except Exception as e:
            db.close()
            return f"zweiter Lauf scheitert: {type(e).__name__}: {str(e)[:80]}"
        db.close()
        if zustand_von(arbeit, sz["opts"]) != nachher:
            return "zweiter Lauf bringt es nicht zu Ende"
    return None


# ---------------------------------------------------------------- Proben
def absturz(sz):
    quelle, vorher, nachher, menge = vorbereiten(sz)
    abw, doppelt_abw, schritte, doppel_laeufe = [], [], 0, 0
    for n in range(1, 2000):
        arbeit = kopie(quelle)
        wie, fehler = kind(arbeit, n, "op", sz)
        if wie == "fehler":
            abw.append((n, f"Kind scheitert: {fehler}"))
            shutil.rmtree(arbeit, ignore_errors=True)
            break
        schritte = n
        absicht = os.path.exists(os.path.join(arbeit, "datenbank", "_absicht.json"))
        if sz.get("doppelt") and wie == "tot" and absicht:
            for m in range(1, 500):
                zweit = kopie(arbeit)
                wie2, fehler2 = kind(zweit, m, "oeffnen", sz)
                doppel_laeufe += 1
                try:
                    ist = zustand_von(zweit, sz["opts"])
                    if ist != nachher:
                        doppelt_abw.append((n, m, "nicht nachher, obwohl die Absicht stand"))
                except Exception as e:
                    doppelt_abw.append((n, m, f"{type(e).__name__}: {str(e)[:60]}"))
                shutil.rmtree(zweit, ignore_errors=True)
                if wie2 != "tot":
                    break
        if absicht and wie == "tot" and sz["art"] == "unteilbar":
            # Stand die Absicht, gilt die Transaktion: nur „nachher“ ist richtig.
            try:
                ist = zustand_von(arbeit, sz["opts"])
                grund = None if ist == nachher else "Absicht stand, trotzdem nicht nachher"
            except Exception as e:
                grund = f"{type(e).__name__}: {str(e)[:80]}"
        else:
            grund = nach_absturz_pruefen(sz, arbeit, vorher, nachher, menge)
        if grund:
            abw.append((n, grund))
        shutil.rmtree(arbeit, ignore_errors=True)
        if wie == "fertig":
            break
    shutil.rmtree(quelle, ignore_errors=True)
    return schritte, abw, doppel_laeufe, doppelt_abw


def plattenfehler(sz):
    quelle, vorher, nachher, menge = vorbereiten(sz)
    abw, schritte = [], 0
    for n in range(1, 2000):
        arbeit = kopie(quelle)
        db = oeffnen(arbeit, sz["opts"])
        _zz.update(n=0, an=True, bei=n, wie="fehler", gefeuert=False)
        geworfen = None
        try:
            exec(sz["op"], {"db": db, "fg": fg})
        except OSError as e:
            geworfen = e
        except Exception as e:
            geworfen = e
            abw.append((n, f"kein OSError, sondern {type(e).__name__}: {str(e)[:60]}"))
        finally:
            _zz["an"] = False
        schritte = n
        try:
            speicher = zustand(db)
            db.close()
            platte = zustand_von(arbeit, sz["opts"])
        except Exception as e:
            abw.append((n, f"danach unlesbar: {type(e).__name__}: {str(e)[:60]}"))
            shutil.rmtree(arbeit, ignore_errors=True)
            break
        if speicher != platte:
            abw.append((n, "Speicher und Platte widersprechen sich"))
        else:
            grund = bewerten(sz, platte, vorher, nachher, menge)
            if grund:
                abw.append((n, grund))
        shutil.rmtree(arbeit, ignore_errors=True)
        # Fertig ist erst, wenn der Fehler gar nicht mehr kam. Ein
        # eingespielter Fehler darf verschluckt werden (fsync aufs Verzeichnis
        # ist mit Absicht nachsichtig) — dann geht es mit n+1 weiter.
        if not _zz["gefeuert"]:
            break
    shutil.rmtree(quelle, ignore_errors=True)
    return schritte, abw


if __name__ == "__main__":
    for sz in SZENARIEN:
        schritte, abw, doppel, dabw = absturz(sz)
        check(f"Ungestört ({sz['name']}): nach dem Neustart steht auf der Platte, was im Speicher stand",
              sz["ungestoert_ok"])
        check(f"Absturz ({sz['name']}): an jedem der {schritte} Schritte ein zugesagter Zustand",
              not abw and schritte > 1, f"{len(abw)} Abweichungen: {abw[:3]}" if abw else "")
        if sz.get("doppelt"):
            check(f"Doppelabsturz ({sz['name']}): stirbt auch das Nachholen, gilt danach die Transaktion",
                  doppel > 0 and not dabw,
                  f"{doppel} Läufe" + (f", {len(dabw)} Abweichungen: {dabw[:3]}" if dabw else ""))
        schritte, abw = plattenfehler(sz)
        check(f"Plattenfehler ({sz['name']}): an jedem der {schritte} Schritte gleichen sich "
              "Speicher und Platte, Zustand zugesagt",
              not abw and schritte > 1, f"{len(abw)} Abweichungen: {abw[:3]}" if abw else "")

    shutil.rmtree(WURZEL, ignore_errors=True)
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\n{n_ok}/{len(results)} Checks bestanden")
    sys.exit(0 if n_ok == len(results) else 1)
