"""
Die Graph-Natur von flatgraph — genau der Teil, den pDMS nie anfasst.

pDMS ruft 14 von 21 öffentlichen Methoden auf. Ungenutzt bleiben
`traverse`, `collect_related`, `get_connected`, `find_nodes`,
`get_node_raw`, `list_collections` und `transaction` — also Mehrsprung,
Pfade und gerichtete Nachbarschaft. pDMS benutzt flatgraph im Grunde als
flachen Schlüssel-Wert-Speicher mit einer Kantenart.

Diese Suite prüft genau diese Fläche. Sie ist damit der eigentliche Schutz
gegen Overfitting: würde flatgraph nur gegen pDMS entwickelt, verrottete
ein Drittel seiner Oberfläche unbemerkt — ungeprüft, unentworfen,
irgendwann falsch.

Das Modell ist deshalb bewusst NICHT dokumentförmig: Anlagen, Räume und
Prozesse, mit mehreren Kantenarten und beiden Richtungen. Käme hier
"Dokument" oder "Kontext" vor, wäre die Prüfung schon wieder pDMS-förmig.

    python tests/test_flatgraph_graph.py
    FLATGRAPH_DATEI=flatgraph/flatgraph.py python tests/test_flatgraph_graph.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen                          # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


WURZEL = tempfile.mkdtemp(prefix="fg_graph_")


def index_stimmt(db):
    """Behauptet der Nachbarschaftsindex dasselbe wie die Kanten?

    Die gefaehrliche Art, den Index kaputtzumachen, ist nicht Langsamkeit,
    sondern eine Ansicht, die etwas zeigt, das es nicht gibt — oder etwas
    verschweigt, das es gibt. Wer Kanten anfasst, ohne ueber _index_edge
    und _unindex_edge zu gehen, erzeugt genau das, und es faellt sonst erst
    auf, wenn jemand die falsche Zahl liest.

    Deshalb nach JEDER Veraenderung: den Index aus den Kanten neu bauen und
    mit dem mitgefuehrten vergleichen.
    """
    if not hasattr(db, "_out_index"):
        return True, "diese Fassung hat keinen Nachbarschaftsindex"
    ist_out = {r: {a: set(e) for a, e in arten.items()}
               for r, arten in db._out_index.items()}
    ist_in = {r: {a: set(e) for a, e in arten.items()}
              for r, arten in db._in_index.items()}
    soll_out, soll_in = {}, {}
    for art, eimer in db._cache["edges"].items():
        for eid, e in eimer.items():
            soll_out.setdefault(e["source"], {}).setdefault(art, set()).add(eid)
            soll_in.setdefault(e["target"], {}).setdefault(art, set()).add(eid)
    if ist_out != soll_out:
        return False, "ausgehend weicht ab"
    if ist_in != soll_in:
        return False, "eingehend weicht ab"
    return True, ""


def werk():
    """Ein kleines Werk: Prozesse mit Unterprozessen, Anlagen, Räume.

    P1 ──hat_teil──> P2 ──hat_teil──> P3
    P1 ──braucht──> A1        P2 ──braucht──> A2        P3 ──braucht──> A2
    A1 ──steht_in──> R1       A2 ──steht_in──> R2
    """
    ort = tempfile.mkdtemp(dir=WURZEL)
    db = fg.MaintenanceEngine(ort)
    for p in ("P1", "P2", "P3"):
        db.create_node("prozesse", p, {"name": f"Prozess {p}", "takt": 30})
    for a in ("A1", "A2"):
        db.create_node("anlagen", a, {"name": f"Anlage {a}", "kw": 7})
    for r in ("R1", "R2"):
        db.create_node("raeume", r, {"name": f"Raum {r}"})
    db.create_edge("prozesse/P1", "prozesse/P2", "hat_teil")
    db.create_edge("prozesse/P2", "prozesse/P3", "hat_teil")
    db.create_edge("prozesse/P1", "anlagen/A1", "braucht")
    db.create_edge("prozesse/P2", "anlagen/A2", "braucht")
    db.create_edge("prozesse/P3", "anlagen/A2", "braucht")
    db.create_edge("anlagen/A1", "raeume/R1", "steht_in")
    db.create_edge("anlagen/A2", "raeume/R2", "steht_in")
    return db, ort


db, _ = werk()

# --- get_connected: Richtung ist eine Zusage, keine Nebensache -----------
check("Ausgehend findet die direkten Nachbarn",
      set(db.get_connected("prozesse/P1")) == {"prozesse/P2", "anlagen/A1"},
      str(sorted(db.get_connected("prozesse/P1"))))
check("Eingehend findet die andere Seite",
      set(db.get_connected("anlagen/A2", direction="in"))
      == {"prozesse/P2", "prozesse/P3"},
      str(sorted(db.get_connected("anlagen/A2", direction="in"))))
check("Nach Kantenart gefiltert",
      db.get_connected("prozesse/P1", rel_type="braucht") == ["anlagen/A1"],
      str(db.get_connected("prozesse/P1", rel_type="braucht")))
check("Nach Zielsammlung gefiltert",
      db.get_connected("prozesse/P1", target_collection="prozesse") == ["prozesse/P2"],
      str(db.get_connected("prozesse/P1", target_collection="prozesse")))
check("Ein Knoten ohne ausgehende Kanten hat keine",
      db.get_connected("raeume/R1") == [], str(db.get_connected("raeume/R1")))

# --- get_connected_edges: dasselbe, aber mit den Kanten selbst ----------
kanten = db.get_connected_edges("prozesse/P1")
check("Die Kanten kommen mit Kennung und Inhalt",
      len(kanten) == 2 and all(isinstance(k, tuple) and len(k) == 2 for k in kanten),
      str(kanten)[:80])
check("Und tragen ihre Art",
      {k[1]["type"] for k in kanten} == {"hat_teil", "braucht"},
      str({k[1]["type"] for k in kanten}))

# --- traverse: der Mehrsprung -------------------------------------------
check("Mehrsprung erreicht auch das Entfernte",
      set(db.traverse("prozesse/P1", rel_type="hat_teil"))
      == {"prozesse/P2", "prozesse/P3"},
      str(sorted(db.traverse("prozesse/P1", rel_type="hat_teil"))))
check("Tiefe 1 bleibt beim direkten Nachbarn",
      db.traverse("prozesse/P1", rel_type="hat_teil", max_depth=1) == ["prozesse/P2"],
      str(db.traverse("prozesse/P1", rel_type="hat_teil", max_depth=1)))
check("Der Startknoten ist normalerweise nicht dabei",
      "prozesse/P1" not in db.traverse("prozesse/P1", rel_type="hat_teil"))
check("Auf Wunsch schon",
      "prozesse/P1" in db.traverse("prozesse/P1", rel_type="hat_teil",
                                   include_start=True))
check("Ohne Kantenart läuft er über alle",
      "raeume/R2" in db.traverse("prozesse/P1"),
      str(sorted(db.traverse("prozesse/P1"))))

# Ein Kreis darf ihn nicht zum Stehen bringen. Das ist die eine Eigenschaft,
# deren Fehlen nicht zu einem falschen Ergebnis führt, sondern zu einem
# Server, der nicht mehr antwortet.
db.create_edge("prozesse/P3", "prozesse/P1", "hat_teil")
im_kreis = db.traverse("prozesse/P1", rel_type="hat_teil")
ok, warum = index_stimmt(db)
check("Der Nachbarschaftsindex stimmt nach dem Anlegen von Kanten", ok, warum)

check("Ein Kreis führt nicht in die Endlosschleife",
      set(im_kreis) == {"prozesse/P2", "prozesse/P3"}, str(sorted(im_kreis)))
check("Und liefert jeden Knoten genau einmal",
      len(im_kreis) == len(set(im_kreis)), str(im_kreis))
# Der Start bleibt draussen, auch wenn der Kreis zu ihm zurueckfuehrt —
# sonst haette `include_start` keine Bedeutung mehr.
check("Der Start bleibt draussen, auch wenn der Kreis ihn erreicht",
      "prozesse/P1" not in im_kreis)

# --- collect_related: der Pfad über wechselnde Kantenarten --------------
db2, _ = werk()
# Die Zwischenstufen NEHMEN DEN START MIT ("Keep start level too" im Code):
# gesucht sind die Anlagen der Unterprozesse UND die des Prozesses selbst.
# Das ist eine Zusage, keine Nachlaessigkeit — sonst faende man die Anlage
# des Hauptprozesses nie.
alle_anlagen = db2.collect_related("prozesse/P1", ["hat_teil", "braucht"])
check("Pfad über zwei Kantenarten sammelt Start UND Unterstufen ein",
      set(alle_anlagen) == {"anlagen/A1", "anlagen/A2"}, str(sorted(alle_anlagen)))
check("Pfad über drei Stufen kommt bei den Räumen an",
      set(db2.collect_related("prozesse/P1", ["hat_teil", "braucht", "steht_in"]))
      == {"raeume/R1", "raeume/R2"},
      str(sorted(db2.collect_related("prozesse/P1", ["hat_teil", "braucht", "steht_in"]))))
check("Ein leerer Pfad liefert nichts",
      db2.collect_related("prozesse/P1", []) == [])
check("Eine unbekannte Kantenart liefert nichts",
      db2.collect_related("prozesse/P1", ["gibtsnicht"]) == [])

# --- find_nodes: die Feldsuche ------------------------------------------
check("Teilzeichenkette, Gross/Klein egal",
      set(db2.find_nodes("prozesse", {"name": "prozess p2"})) == {"P2"},
      str(sorted(db2.find_nodes("prozesse", {"name": "prozess p2"}))))
check("Platzhalter",
      set(db2.find_nodes("anlagen", {"name": "Anlage*"})) == {"A1", "A2"},
      str(sorted(db2.find_nodes("anlagen", {"name": "Anlage*"}))))
check("Eigene Bedingung als Funktion",
      set(db2.find_nodes("prozesse", {"takt": lambda v: v == 30})) == {"P1", "P2", "P3"},
      str(sorted(db2.find_nodes("prozesse", {"takt": lambda v: v == 30}))))
check("Mehrere Felder werden UND-verknüpft",
      set(db2.find_nodes("prozesse", {"name": "P2", "takt": lambda v: v == 30})) == {"P2"})
check("Ohne Treffer kommt nichts",
      db2.find_nodes("prozesse", {"name": "gibtsnicht"}) == {})
check("Ohne Bedingung kommt alles",
      len(db2.find_nodes("prozesse", {})) == 3)

# Der Feldindex ist ABGELEITET. Die Probe bekommt eine EIGENE Datenbank:
# sie loescht ein Verzeichnis, und eine zerstoerende Probe darf den Rest
# der Suite nicht vergiften. Beim ersten Lauf tat sie genau das.
db_idx, ort_idx = werk()
db_idx.find_nodes("anlagen", {"name": "Anlage*"})          # Index aufbauen
idx = os.path.join(ort_idx, "datenbank", "index")
# Bis 3.0.0 lag der Index hier als Datei, gesichert durch eine Pruefsumme
# ueber den ganzen Inhalt der Sammlung. Seit 3.0.0-entwurf liegt er nur im
# Speicher: die Pruefung der Datei kostete zwanzigmal mehr als der
# Neuaufbau, den sie ersparen sollte (flatgraph/bench, Block 6). Diese
# Pruefung hiess vorher „Der Index landet auf der Platte“.
check("Der Feldindex liegt nicht auf der Platte",
      not (os.path.isdir(idx) and os.listdir(idx)),
      str(os.listdir(idx)) if os.path.isdir(idx) else "")
# Ein Verzeichnis, das eine aeltere Fassung hinterlassen hat, darf
# jederzeit verschwinden — die Suche darf es nie gebraucht haben.
shutil.rmtree(idx, ignore_errors=True)
check("Ohne Index kommt beim Lesen dasselbe heraus",
      set(db_idx.find_nodes("anlagen", {"name": "Anlage*"})) == {"A1", "A2"})

# Und er darf sich danach wieder aufbauen. Der Vertrag sagt, der Index
# duerfe jederzeit geloescht werden — beim ersten Lauf stimmte das nicht:
# der naechste Schreibvorgang starb an FileNotFoundError, weil niemand das
# Verzeichnis wieder anlegte. Abgefangen, damit diese Suite auch eine
# aeltere Fassung noch charakterisieren kann statt an ihr abzubrechen.
try:
    db_idx.update_node("anlagen", "A1", {"kw": 9})
    wieder = set(db_idx.find_nodes("anlagen", {"name": "Anlage A1"})) == {"A1"}
    grund = ""
except Exception as e:
    wieder, grund = False, f"{type(e).__name__}: {str(e)[:70]}"
check("Und baut sich danach wieder auf", wieder, grund)

# Der Index darf nicht veralten, wenn sich ein WERT aendert und die
# Kennungen gleich bleiben — daran ist eine fruehere Fassung gescheitert.
db_wert, _ = werk()
db_wert.find_nodes("prozesse", {"name": "Prozess"})        # Index aufbauen
db_wert.update_node("prozesse", "P1", {"name": "Umbenannt"})
check("Nach einer Wertänderung findet die Suche den neuen Wert",
      set(db_wert.find_nodes("prozesse", {"name": "Umbenannt"})) == {"P1"},
      str(sorted(db_wert.find_nodes("prozesse", {"name": "Umbenannt"}))))
check("Und den alten nicht mehr",
      "P1" not in db_wert.find_nodes("prozesse", {"name": "Prozess P1"}),
      str(sorted(db_wert.find_nodes("prozesse", {"name": "Prozess P1"}))))

# --- get_node_raw und list_collections ----------------------------------
db3, _ = werk()
db3.soft_delete("anlagen", "A1")
check("Weich Gelöschtes ist über get_node weg",
      db3.get_node("anlagen/A1") is None)
check("Über get_node_raw aber noch da",
      (db3.get_node_raw("anlagen/A1") or {}).get("name") == "Anlage A1",
      str(db3.get_node_raw("anlagen/A1"))[:60])
check("Und trägt die Löschmarke",
      "_deletion_flag" in (db3.get_node_raw("anlagen/A1") or {}))
check("get_node_raw auf Unbekanntes bleibt None",
      db3.get_node_raw("anlagen/gibtsnicht") is None)

check("list_collections nennt die eigenen Sammlungen",
      set(db3.list_collections()) == {"prozesse", "anlagen", "raeume"},
      str(sorted(db3.list_collections())))

# Weich Gelöschtes verschwindet auch aus der Nachbarschaft — sonst zeigte
# eine Sicht auf etwas, das es nicht mehr gibt.
check("Ein weich gelöschter Nachbar taucht nicht mehr auf",
      "anlagen/A1" not in db3.get_connected("prozesse/P1"),
      str(db3.get_connected("prozesse/P1")))
check("Mit include_deleted schon",
      "anlagen/A1" in db3.get_connected("prozesse/P1", include_deleted=True),
      str(db3.get_connected("prozesse/P1", include_deleted=True)))

# --- transaction ---------------------------------------------------------
db4, ort4 = werk()
vorher = len(db4.list_nodes("prozesse"))
try:
    with db4.transaction():
        db4.create_node("prozesse", "P9", {"name": "Neu", "takt": 1})
        db4.create_edge("prozesse/P1", "prozesse/P9", "hat_teil")
        raise RuntimeError("Abbruch mittendrin")
except RuntimeError:
    pass
check("Nach einem Abbruch ist der Knoten weg",
      db4.get_node("prozesse/P9") is None)
check("Und die Kante auch",
      "prozesse/P9" not in db4.get_connected("prozesse/P1"),
      str(db4.get_connected("prozesse/P1")))
check("Der Rest ist unversehrt", len(db4.list_nodes("prozesse")) == vorher)

with db4.transaction():
    db4.create_node("prozesse", "P8", {"name": "Bleibt", "takt": 2})
check("Ohne Abbruch bleibt es", db4.get_node("prozesse/P8") is not None)

# Verschachtelt: erst das Verlassen der äussersten schreibt.
with db4.transaction():
    db4.create_node("prozesse", "P7", {"name": "Aussen", "takt": 3})
    with db4.transaction():
        db4.create_node("prozesse", "P6", {"name": "Innen", "takt": 4})
check("Verschachtelte Transaktionen schreiben beide",
      db4.get_node("prozesse/P7") and db4.get_node("prozesse/P6"))

# Und was in der Transaktion entstand, überlebt einen Neustart.
db4.flush()
db5 = neu_oeffnen(db4)
check("Nach einem Neustart ist alles aus der Transaktion da",
      all(db5.get_node(f"prozesse/{p}") for p in ("P6", "P7", "P8")))
check("Und das Abgebrochene ist auch nach dem Neustart weg",
      db5.get_node("prozesse/P9") is None)

# --- Der Index unter allem, was Kanten veraendert ------------------------
db_i, ort_i = werk()
faelle = []
ok, w = index_stimmt(db_i); faelle.append(("nach dem Aufbau", ok, w))

kante = db_i.get_connected_edges("prozesse/P1")[0][0]
db_i.delete_edge(kante)
ok, w = index_stimmt(db_i); faelle.append(("nach delete_edge", ok, w))

# INNERHALB einer Transaktion ist der Punkt. Bis 3.0.0 wurde draussen nach
# jedem Schreibvorgang ohnehin neu gebaut (_flush_edge_type ersetzte den
# Eimer), das ueberdeckte einen kaputten _unindex_edge vollstaendig — eine
# Pruefung nur ausserhalb fing ihn nicht. Seit 3.0.0-entwurf wird auch
# draussen nicht mehr neu gebaut; drinnen bleibt der Fall, in dem nichts
# geschrieben wird und die Pflege beim Aendern selbst stimmen muss. Und
# drinnen fragt ein Aufrufer genauso nach Nachbarn wie draussen.
with db_i.transaction():
    rest = db_i.get_connected_edges("prozesse/P1")
    if rest:
        db_i.delete_edge(rest[0][0])
    db_i.create_edge("prozesse/P1", "raeume/R2", "frisch")
    ok, w = index_stimmt(db_i)
    faelle.append(("mitten in einer Transaktion", ok, w))
    nachbarn_drin = db_i.get_connected("prozesse/P1", rel_type="frisch")
faelle.append(("und die Nachbarschaft stimmt dort auch",
               nachbarn_drin == ["raeume/R2"], str(nachbarn_drin)))

db_i.flush()
ok, w = index_stimmt(db_i); faelle.append(("nach flush", ok, w))

try:
    with db_i.transaction():
        db_i.create_edge("prozesse/P1", "raeume/R1", "erfunden")
        raise RuntimeError("Abbruch")
except RuntimeError:
    pass
ok, w = index_stimmt(db_i); faelle.append(("nach einem Rollback", ok, w))

db_i.soft_delete("anlagen", "A2")
db_i.run_garbage_collection()
ok, w = index_stimmt(db_i); faelle.append(("nach dem Aufräumen", ok, w))

db_i2 = neu_oeffnen(db_i)
ok, w = index_stimmt(db_i2); faelle.append(("nach einem Neustart", ok, w))

for name, ok, warum in faelle:
    check(f"Der Index stimmt {name}", ok, warum)

# Und er waechst nicht mit jedem jemals dagewesenen Knoten weiter: nachdem
# alle Kanten eines Knotens weg sind, darf er nicht mehr im Index stehen.
db_l, _ = werk()
for eid, _ in db_l.get_connected_edges("raeume/R1", direction="in"):
    db_l.delete_edge(eid)
check("Ein Knoten ohne Kanten verschwindet aus dem Index",
      "raeume/R1" not in db_l._in_index, str(list(db_l._in_index))[:60])

shutil.rmtree(WURZEL, ignore_errors=True)

print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
