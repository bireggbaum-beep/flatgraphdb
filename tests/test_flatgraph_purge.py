"""
`purge(sammlung, kennung)`: einen Knoten aus dem Papierkorb endgültig
entfernen, mit genau seiner Kaskade — sonst nichts.

Anlass: In partAtlas rief „Papierkorb leeren“ den Müllsammler auf. Der
entfernt ALLES Gelöschte im Bestand, auch entfernte Ordner und Baugruppen,
an die beim Klick niemand dachte. Gezielt zurückholen konnte flatgraph
(`restore_node`), gezielt endgültig entfernen nicht.

Zugesagt und hier geprüft:
  - entfernt den Knoten und die mit ihm Gelöschten (`_geloescht_durch`),
    ein zweiter Papierkorb-Eintrag samt seiner Kaskade bleibt
  - lehnt einen lebenden Knoten ab (`NichtImPapierkorb`), ändert nichts
  - nach dem Neustart ist der Knoten weg, alles andere unverändert
  - lange Texte anderer Papierkorb-Knoten bleiben, die eigenen gehen
  - nicht in einer Transaktion (`NichtInTransaktion`)
  - Eintrag im Audit- und im Wartungsprotokoll

Gegenprobe: gegen 4.0.0 fällt diese Suite (es gibt kein `purge`); jede
Eigenschaft einzeln an einer mutierten Kopie.

Importiert `pdms` nicht.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade, neu_oeffnen, platte                  # noqa: E402

fg = lade()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def versuch(fn):
    """Ergebnis von fn(), oder die Ausnahme selbst."""
    try:
        return fn()
    except Exception as e:                                             # noqa: BLE001
        return e


WURZEL = tempfile.mkdtemp(prefix="fg_purge_")
LANG = 100


def bestand():
    """Zwei Aufträge im Papierkorb, jeder mit einem Unterobjekt per Kaskade,
    dazu ein unabhängig gelöschter Knoten und zwei lebende.

        e --nutzt--> a ==teil==> b          (a gelöscht, b mit ihm)
                     p ==teil==> q          (p gelöscht, q mit ihm)
                     u                      (unabhängig gelöscht)
                     d                      (lebt)
    Jeder hat einen langen Text.
    """
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), longtext_threshold=LANG)
    for k in "abpqude":
        db.create_node("x", k, {"name": k, "t": k * 500})
    db.create_edge("x/a", "x/b", "teil", cascade_delete=True)
    db.create_edge("x/p", "x/q", "teil", cascade_delete=True)
    db.create_edge("x/e", "x/a", "nutzt")
    db.create_edge("x/d", "x/p", "nutzt")
    db.soft_delete("x", "a")
    db.soft_delete("x", "p")
    db.soft_delete("x", "u")
    return db


def da(db, k):
    return db.get_node_raw(f"x/{k}") is not None


def texte(db):
    vt = os.path.join(db.root, "vault_text")
    return sorted(os.listdir(vt)) if os.path.isdir(vt) else []


def text_von(db, k):
    return (db.get_node_full(f"x/{k}") or {}).get("t")


# =========================================================================
print("--- Nur der Knoten und seine Kaskade ---")
# =========================================================================
db = bestand()
check("Vorher liegen a, b, p, q, u im Papierkorb",
      all(da(db, k) and db.get_node(f"x/{k}") is None for k in "abpqu"))
stats = versuch(lambda: db.purge("x", "a"))
check("purge liefert eine Statistik", isinstance(stats, dict), repr(stats))
stats = stats if isinstance(stats, dict) else {}
check("a und seine Kaskade b sind weg", not da(db, "a") and not da(db, "b"))
check("Der zweite Papierkorb-Eintrag p bleibt, mit seiner Kaskade q",
      da(db, "p") and da(db, "q"))
check("… und liegt weiter im Papierkorb (zurückholbar)",
      db.get_node("x/p") is None
      and db.get_node_raw("x/q").get("_geloescht_durch") == "x/p")
check("Der unabhängig gelöschte u bleibt", da(db, "u"))
check("Die lebenden d, e bleiben", db.get_node("x/d") is not None
      and db.get_node("x/e") is not None)
check("Die Kanten von a und b sind weg, die anderen bleiben",
      db.get_connected("x/e", rel_type="nutzt", include_deleted=True) == []
      and db.get_connected("x/d", rel_type="nutzt", include_deleted=True) == ["x/p"])
check("Statistik: zwei Knoten, zwei Kanten",
      stats.get("nodes_purged") == 2 and stats.get("edges_removed") == 2, repr(stats))
db.restore_node("x", "p")
check("restore_node auf p holt p und q danach unverändert zurück",
      db.get_node("x/p") is not None and db.get_node("x/q") is not None)
db.close()

# =========================================================================
print("--- Lebende Knoten werden abgelehnt ---")
# =========================================================================
db = bestand()
vorher = platte(db.root)
fehler = versuch(lambda: db.purge("x", "d"))
check("purge auf einen lebenden Knoten wirft NichtImPapierkorb",
      isinstance(fehler, getattr(fg, "NichtImPapierkorb", ())), repr(fehler))
check("… das ist auch ein FlatGraphFehler und ValueError",
      isinstance(fehler, fg.FlatGraphFehler) and isinstance(fehler, ValueError))
check("… und auf der Platte hat sich nichts geändert", platte(db.root) == vorher)
check("… d lebt weiter", db.get_node("x/d") is not None)
fehler = versuch(lambda: db.purge("x", "gibtsnicht"))
check("purge auf einen fehlenden Knoten wirft KnotenFehlt",
      isinstance(fehler, fg.KnotenFehlt), repr(fehler))


def in_transaktion():
    with db.transaction():
        db.purge("x", "a")


fehler = versuch(in_transaktion)
check("purge in einer Transaktion wirft NichtInTransaktion",
      isinstance(fehler, fg.NichtInTransaktion), repr(fehler))
check("… und a ist noch da", da(db, "a") and da(db, "b"))
db.close()

# =========================================================================
print("--- Nach dem Neustart ---")
# =========================================================================
db = bestand()
vorher_knoten, vorher_kanten = platte(db.root), platte(db.root, "edges")
versuch(lambda: db.purge("x", "a"))
db = neu_oeffnen(db, longtext_threshold=LANG)
check("Nach dem Neustart sind a und b weg", not da(db, "a") and not da(db, "b"))
nachher = platte(db.root)
erwartet = {k: v for k, v in vorher_knoten["x"].items() if k not in ("a", "b")}
check("… alle anderen Knoten stehen unverändert auf der Platte",
      nachher.get("x") == erwartet, str(sorted(nachher.get("x", {}))))
kanten = platte(db.root, "edges")
check("… die Kante p→q und d→p stehen unverändert da",
      kanten.get("teil") and len(kanten["teil"]) == 1
      and list(kanten["teil"].values())[0]["source"] == "x/p"
      and kanten.get("nutzt") and len(kanten["nutzt"]) == 1
      and list(kanten["nutzt"].values())[0]["source"] == "x/d",
      str(kanten))
check("… und p, q, u liegen weiter im Papierkorb",
      all(da(db, k) and db.get_node(f"x/{k}") is None for k in "pqu"))
db.close()

# =========================================================================
print("--- Lange Texte ---")
# =========================================================================
db = bestand()
vorher = texte(db)
versuch(lambda: db.purge("x", "a"))
nachher = texte(db)
check("Die Texte von a und b sind weg",
      not any(n.startswith(("x__a__", "x__b__")) for n in nachher), str(nachher))
check("Die Texte der anderen Papierkorb-Knoten p, q, u bleiben",
      all(any(n.startswith(f"x__{k}__") for n in nachher) for k in "pqu"), str(nachher))
check("Genau zwei Dateien weniger", len(vorher) - len(nachher) == 2,
      f"{len(vorher)} → {len(nachher)}")
db = neu_oeffnen(db, longtext_threshold=LANG)
db.restore_node("x", "p")
db.restore_node("x", "u")
check("Nach dem Neustart: zurückgeholt haben p, q, u ihren Text",
      all(text_von(db, k) == k * 500 for k in "pqu"))
db.close()

# Eine alte Fassung eines Texts (eine Waise) ist kein Text von a: purge
# lässt sie dem Müllsammler, statt bestandsweit aufzuräumen.
db = bestand()
db.restore_node("x", "u")
db.update_node("x", "u", {"t": "v" * 500})
waisen = [n for n in texte(db) if n.startswith("x__u__")]
versuch(lambda: db.purge("x", "a"))
check("Waisen anderer Knoten bleiben (purge räumt nicht bestandsweit)",
      all(n in texte(db) for n in waisen) and len(waisen) == 2, str(waisen))
db.close()

# `k.1` und `k_1` heissen als Dateiname gleich (Sonderzeichen werden „_“);
# mit gleichem Text teilen sie sich EINE Datei. Die darf nicht gehen,
# solange der andere noch darauf zeigt.
db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), longtext_threshold=LANG)
db.create_node("x", "k.1", {"t": "k" * 500})
db.create_node("x", "k_1", {"t": "k" * 500})
check("Vorbedingung: beide zeigen auf dieselbe Datei",
      db.get_node_raw("x/k.1")["t"] == db.get_node_raw("x/k_1")["t"])
db.soft_delete("x", "k.1")
versuch(lambda: db.purge("x", "k.1"))
db = neu_oeffnen(db, longtext_threshold=LANG)
check("Eine Datei, auf die ein anderer Knoten noch zeigt, bleibt",
      text_von(db, "k_1") == "k" * 500, str(text_von(db, "k_1"))[:40])
db.close()

# =========================================================================
print("--- Protokolle ---")
# =========================================================================
db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL), audit=True)
db.create_node("x", "a", {"name": "a"})
db.create_node("x", "b", {"name": "b"})
db.create_edge("x/a", "x/b", "teil", cascade_delete=True)
db.soft_delete("x", "a")
versuch(lambda: db.purge("x", "a"))
eintraege = {(e.get("ref"), e.get("action"))
             for e in db._cache["nodes"].get("_audit_log", {}).values()}
check("Audit: je ein Eintrag „purge“ für a und b",
      ("x/a", "purge") in eintraege and ("x/b", "purge") in eintraege, str(eintraege))
log = os.path.join(db.root, "datenbank", "maintenance.log")
zeilen = open(log, encoding="utf-8").read().splitlines() if os.path.exists(log) else []
letzte = json.loads(zeilen[-1].split("] ", 1)[1]) if zeilen else {}
check("Wartungsprotokoll: ein Eintrag mit der Referenz",
      letzte.get("ref") == "x/a" and letzte.get("nodes_purged") == 2, str(letzte))
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
