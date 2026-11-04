"""
Belastung: Leser und Schreiber gemischt, extreme Einzelwerte, Dauerlauf.

  Gemischt      Schreiber buchen in Transaktionen um und nehmen zurück,
                Leser lesen dabei. Kein Leser darf einen halben oder einen
                zurückgenommenen Stand sehen (VERTRAG 3.1: eine Transaktion
                hält die Sperre über den ganzen Block).
  Extreme       ein Knoten mit 50 MB, 100 000 Felder, eine Kennung mit
                100 000 Zeichen, Verschachtelung an der Grenze, ein Knoten
                mit 20 000 Kanten, 2000 Sammlungen. Jeder Wert kommt nach
                dem Neustart gleich zurück oder wird abgewiesen — dann
                bleibt der Bestand, wie er war.
  Dauerlauf     20 000 gemischte Operationen gegen ein Modell, mit
                Neustarts, Rollbacks, weichem und endgültigem Löschen und
                Müllsammler. Der Bestand bleibt gleich gross (bei gleich
                vielen lebenden Knoten): Dateizahl, Platz auf der Platte,
                Arbeitsspeicher nach dem Öffnen.

Nicht geprüft: Lesen mit `readonly=True` während geschrieben wird — das
ist ausdrücklich nicht zugesagt (VERTRAG 3.1); das Audit-Protokoll wächst
mit Absicht und ist im Dauerlauf aus.

    python tests/test_flatgraph_belastung.py
"""
import os
import random
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen                          # noqa: E402

fg = lade()
results = []
WURZEL = tempfile.mkdtemp(prefix="fg_last_")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def frisch(**kw):
    return fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), **kw)


def platz(wurzel):
    """Dateien und Bytes des Bestands, OHNE maintenance.log; das Protokoll
    wird getrennt gezählt (es wächst mit jedem Lauf, siehe Dauerlauf)."""
    dateien, groesse, protokoll = 0, 0, 0
    for r, _, fs in os.walk(wurzel):
        for f in fs:
            pfad = os.path.join(r, f)
            if f == "maintenance.log":
                protokoll = os.path.getsize(pfad)
                continue
            dateien += 1
            groesse += os.path.getsize(pfad)
    return dateien, groesse, protokoll


# ======================================================================
# 7. Leser und Schreiber gemischt
# ======================================================================
def gemischt(dauer=3.0, schreiber=3, leser=4):
    db = frisch()
    with db.transaction():
        for i in range(10):
            db.create_node("konto", str(i), {"v": 100})
    for i in range(9):
        db.create_edge(f"konto/{i}", f"konto/{i + 1}", "kette")
    ende = time.time() + dauer
    fehler, verstoesse = [], []
    zaehler = {"buchungen": 0, "rollbacks": 0, "lesungen": 0}
    sperre = threading.Lock()

    def schreiben(nr):
        rnd = random.Random(nr)
        try:
            while time.time() < ende:
                a, b = rnd.sample(range(10), 2)
                betrag = rnd.randint(1, 20)
                zurueck = rnd.random() < 0.3
                try:
                    with db.transaction():
                        va = db.get_node(f"konto/{a}")["v"]
                        db.update_node("konto", str(a), {"v": va - betrag})
                        if zurueck:
                            # Ein Zwischenstand, der nie gelten darf.
                            db.update_node("konto", str(b), {"v": -999999})
                            raise ZeroDivisionError
                        vb = db.get_node(f"konto/{b}")["v"]
                        db.update_node("konto", str(b), {"v": vb + betrag})
                        # Kanten paarweise: ihre Zahl bleibt gerade.
                        k1 = db.create_edge(f"konto/{a}", f"konto/{b}", "buchung")
                        k2 = db.create_edge(f"konto/{b}", f"konto/{a}", "buchung")
                    with sperre:
                        zaehler["buchungen"] += 1
                    if rnd.random() < 0.5:
                        with db.transaction():
                            db.delete_edge(k1)
                            db.delete_edge(k2)
                except ZeroDivisionError:
                    with sperre:
                        zaehler["rollbacks"] += 1
        except Exception as e:
            fehler.append(f"Schreiber {nr}: {type(e).__name__}: {e}")

    def lesen(nr):
        try:
            while time.time() < ende:
                knoten = db.list_nodes("konto")
                summe = sum(d["v"] for d in knoten.values())
                if summe != 1000:
                    verstoesse.append(f"Summe {summe}")
                if any(d["v"] == -999999 for d in knoten.values()):
                    verstoesse.append("zurückgenommener Stand sichtbar")
                if len(db.list_edges("buchung")) % 2:
                    verstoesse.append("ungerade Kantenzahl")
                if len(db.traverse("konto/0", rel_type="kette")) != 9:
                    verstoesse.append("Kette unvollständig")
                with sperre:
                    zaehler["lesungen"] += 1
        except Exception as e:
            fehler.append(f"Leser {nr}: {type(e).__name__}: {e}")

    faeden = [threading.Thread(target=schreiben, args=(i,)) for i in range(schreiber)]
    faeden += [threading.Thread(target=lesen, args=(i,)) for i in range(leser)]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join()
    speicher = (db.list_nodes("konto"), len(db.list_edges("buchung")))
    db = neu_oeffnen(db)
    platte = (db.list_nodes("konto"), len(db.list_edges("buchung")))
    db.close()
    return zaehler, fehler, verstoesse, speicher == platte


# ======================================================================
# 8. Extreme Einzelwerte
# ======================================================================
def rundreise(werte, kennung="x", sammlung="a"):
    db = frisch()
    db.create_node(sammlung, kennung, werte)
    db = neu_oeffnen(db)
    ok = db.get_node(f"{sammlung}/{kennung}") == werte
    db.close()
    return ok


def verschachtelt(listen):
    r = []
    c = r
    for _ in range(listen - 1):
        c.append([])
        c = c[0]
    return r


def abgewiesen_und_unveraendert(werte):
    """Abgewiesen mit NichtSpeicherbar, und der Bestand ist danach, wie er war."""
    db = frisch()
    db.create_node("a", "da", {"v": 1})
    try:
        db.create_node("a", "neu", werte)
        art = "angenommen"
    except fg.NichtSpeicherbar:
        art = "NichtSpeicherbar"
    except Exception as e:
        art = type(e).__name__
    speicher = db.list_nodes("a")
    db = neu_oeffnen(db)
    platte = db.list_nodes("a")
    db.close()
    return art, speicher == platte == {"da": {"v": 1}}


def hub(n):
    db = frisch()
    with db.transaction():
        db.create_node("h", "hub", {})
        for i in range(n):
            db.create_node("b", str(i), {})
            db.create_edge("h/hub", f"b/{i}", "t")
    db = neu_oeffnen(db)
    t = time.perf_counter()
    nachbarn = db.get_connected("h/hub")
    dauer = time.perf_counter() - t
    rueck = db.get_connected(f"b/{n - 1}", direction="in")
    db.close()
    return len(nachbarn) == n and rueck == ["h/hub"], dauer


def viele_sammlungen(n):
    db = frisch()
    with db.transaction():
        for i in range(n):
            db.create_node(f"s{i}", "x", {"i": i})
    t = time.perf_counter()
    db = neu_oeffnen(db)
    dauer = time.perf_counter() - t
    ok = (len(db.list_collections()) == n
          and db.get_node(f"s{n - 1}/x") == {"i": n - 1})
    db.close()
    return ok, dauer


# ======================================================================
# 9. Dauerlauf
# ======================================================================
def dauerlauf(schritte=20000, seed=7, kennungen=400, punkte=5):
    """Gegen ein Modell: {kennung: (wert, im_papierkorb)} und {(q, z)}."""
    rnd = random.Random(seed)
    db = frisch()
    wurzel = db.root
    knoten, kanten = {}, set()
    messungen, abweichung = [], None

    def lebend():
        return [k for k, (_, weg) in knoten.items() if not weg]

    for i in range(1, schritte + 1):
        art = rnd.random()
        k = f"k{rnd.randrange(kennungen):04d}"
        if art < 0.25:
            if k not in knoten:
                db.create_node("d", k, {"v": i, "text": "x" * rnd.randrange(200)})
                knoten[k] = (i, False)
        elif art < 0.50:
            if k in knoten and not knoten[k][1]:
                db.update_node("d", k, {"v": i})
                knoten[k] = (i, False)
        elif art < 0.62:
            ziele = lebend()
            if len(ziele) > 1:
                q, z = rnd.sample(ziele, 2)
                if (q, z) not in kanten:
                    db.create_edge(f"d/{q}", f"d/{z}", "t")
                    kanten.add((q, z))
        elif art < 0.70:
            if kanten:
                q, z = rnd.choice(sorted(kanten))
                eid = next(e for e, d in db.list_edges("t").items()
                           if d["source"] == f"d/{q}" and d["target"] == f"d/{z}")
                db.delete_edge(eid)
                kanten.discard((q, z))
        elif art < 0.78:
            if k in knoten and not knoten[k][1]:
                db.soft_delete("d", k)
                knoten[k] = (knoten[k][0], True)
        elif art < 0.82:
            if k in knoten and knoten[k][1]:
                db.restore_node("d", k)
                knoten[k] = (knoten[k][0], False)
        elif art < 0.86:
            if k in knoten and knoten[k][1]:
                db.purge("d", k)
                del knoten[k]
                kanten = {(q, z) for q, z in kanten if k not in (q, z)}
        elif art < 0.96:
            try:
                with db.transaction():
                    for _ in range(3):
                        kk = f"k{rnd.randrange(kennungen):04d}"
                        if kk in knoten and not knoten[kk][1]:
                            db.update_node("d", kk, {"v": -1})
                    raise ZeroDivisionError
            except ZeroDivisionError:
                pass
        elif art < 0.99:
            db = neu_oeffnen(db)
        else:
            db.run_garbage_collection()
            weg = [kk for kk, (_, w) in knoten.items() if w]
            for kk in weg:
                del knoten[kk]
            kanten = {(q, z) for q, z in kanten if q not in weg and z not in weg}

        if i % (schritte // punkte) == 0:
            db.run_garbage_collection()
            weg = [kk for kk, (_, w) in knoten.items() if w]
            for kk in weg:
                del knoten[kk]
            kanten = {(q, z) for q, z in kanten if q not in weg and z not in weg}
            db.close()
            tracemalloc.start()
            db = fg.FlatGraphDB(wurzel)
            speicher = tracemalloc.get_traced_memory()[0]
            tracemalloc.stop()
            ist = {kk: (d["v"], False) for kk, d in db.list_nodes("d").items()}
            ist_kanten = {(d["source"][2:], d["target"][2:])
                          for d in db.list_edges("t").values()}
            if abweichung is None and (ist != knoten or ist_kanten != kanten):
                abweichung = f"nach Schritt {i}: {len(ist)} statt {len(knoten)} Knoten, " \
                             f"{len(ist_kanten)} statt {len(kanten)} Kanten"
            dateien, groesse, protokoll = platz(wurzel)
            messungen.append({"schritt": i, "knoten": len(knoten), "kanten": len(kanten),
                              "dateien": dateien, "bytes": groesse, "speicher": speicher,
                              "protokoll": protokoll})
    db.close()
    return messungen, abweichung


if __name__ == "__main__":
    print("--- 7. Leser und Schreiber gemischt ---")
    z, fehler, verstoesse, gleich = gemischt()
    detail = f"{z['buchungen']} Buchungen, {z['rollbacks']} Rollbacks, {z['lesungen']} Lesungen"
    check("Gemischt: kein Faden scheitert", not fehler, detail + (f"; {fehler[:2]}" if fehler else ""))
    check("Gemischt: kein Leser sieht einen halben oder zurückgenommenen Stand",
          not verstoesse and z["lesungen"] > 100 and z["rollbacks"] > 0,
          f"{len(verstoesse)} Verstösse: {verstoesse[:3]}" if verstoesse else detail)
    check("Gemischt: nach dem Neustart steht auf der Platte, was im Speicher stand", gleich)

    print("--- 8. Extreme Einzelwerte ---")
    t = time.perf_counter()
    check("Ein Knoten mit 50 MB Text kommt nach dem Neustart gleich zurück",
          rundreise({"t": "y" * (50 * 2 ** 20)}), f"{time.perf_counter() - t:.1f} s")
    check("Ein Knoten mit 100 000 Feldern kommt gleich zurück",
          rundreise({f"f{i}": i for i in range(100000)}))
    check("Eine Kennung mit 100 000 Zeichen", rundreise({"v": 1}, kennung="k" * 100000))
    check("Kennung und Sammlung mit Emoji, Umlaut und Leerzeichen",
          rundreise({"v": "🚲"}, kennung="🚲 Fahrräd ü", sammlung="Bäume"))
    grenze = getattr(fg, "MAX_VERSCHACHTELUNG", None)
    if grenze:
        check(f"Verschachtelt bis zur Grenze ({grenze} Ebenen, der Knoten zählt mit) kommt gleich zurück",
              rundreise({"t": verschachtelt(grenze - 1)}))
    for ebenen in ((grenze or 100) + 1, 990, 5000):
        art, unveraendert = abgewiesen_und_unveraendert({"t": verschachtelt(ebenen)})
        check(f"{ebenen} Ebenen: NichtSpeicherbar, der Bestand bleibt, wie er war",
              art == "NichtSpeicherbar" and unveraendert, art)
    selbst = {}
    selbst["x"] = selbst
    art, unveraendert = abgewiesen_und_unveraendert(selbst)
    check("Ein Wert, der sich selbst enthält: NichtSpeicherbar, Bestand unverändert",
          art == "NichtSpeicherbar" and unveraendert, art)
    ok, dauer = hub(20000)
    check("Ein Knoten mit 20 000 Kanten: alle Nachbarn, auch rückwärts",
          ok, f"get_connected {dauer * 1000:.1f} ms")
    ok, dauer = viele_sammlungen(2000)
    check("2000 Sammlungen: nach dem Neustart alle da", ok, f"Öffnen {dauer:.2f} s")

    print("--- 9. Dauerlauf ---")
    t = time.perf_counter()
    messungen, abweichung = dauerlauf()
    dauer = time.perf_counter() - t
    for m in messungen:
        print(f"      Schritt {m['schritt']:>6}: {m['knoten']:>4} Knoten, {m['kanten']:>4} Kanten, "
              f"{m['dateien']:>4} Dateien, {m['bytes'] / 1024:>7.1f} KB, "
              f"Speicher nach Öffnen {m['speicher'] / 1024:>7.1f} KB, "
              f"maintenance.log {m['protokoll'] / 1024:>5.1f} KB")
    check("Dauerlauf: an jedem Messpunkt gleicht der Bestand dem Modell",
          abweichung is None, abweichung or f"{len(messungen)} Messpunkte, {dauer:.1f} s")
    # Gleich gross heisst: je lebendem Eintrag nicht mehr als am ersten
    # Messpunkt (mit Spielraum für Fächer, die gerade nicht voll sind).
    erste, letzte = messungen[0], messungen[-1]
    def je_eintrag(m, feld):
        return m[feld] / max(1, m["knoten"] + m["kanten"])
    for feld, name in (("dateien", "Dateien"), ("bytes", "Platz auf der Platte"),
                       ("speicher", "Arbeitsspeicher nach dem Öffnen")):
        verhaeltnis = je_eintrag(letzte, feld) / max(1e-9, je_eintrag(erste, feld))
        check(f"Dauerlauf: {name} je Eintrag wächst nicht (letzter / erster Messpunkt ≤ 1.5)",
              verhaeltnis <= 1.5, f"{verhaeltnis:.2f}")

    shutil.rmtree(WURZEL, ignore_errors=True)
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\n{n_ok}/{len(results)} Checks bestanden")
    sys.exit(0 if n_ok == len(results) else 1)
