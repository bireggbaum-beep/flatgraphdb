"""
Löschen mit Vorschau (Issue #34): wissen, was passiert, bevor es passiert.

Bis 3.0.0 markierte erst der Müllsammler die Ziele von Kaskadenkanten und
löschte sie im SELBEN Lauf endgültig. Sie lagen nie im Papierkorb, und man
konnte es sich nicht mehr anders überlegen. Und es gab keine Frage, mit der
eine Anwendung VORHER erfuhr, was alles mitgeht.

Zugesagt und hier geprüft:
  - `traverse(..., kantenfilter=)` geht nur Kanten, die die Bedingung erfüllen
  - `verwendungen(ref)`: wer zeigt hierher, nach Kantenart
  - `loeschfolgen(ref)`: was mit in den Papierkorb geht und welche Kanten
    danach verschwinden — ohne etwas zu verändern
  - `soft_delete` legt die Kaskade sofort mit in den Papierkorb,
    `restore_node` holt genau diese zurück, nicht unabhängig Gelöschtes
  - der Müllsammler löscht nur, was vor seinem Lauf im Papierkorb lag

Gegenprobe: gegen die Fassung vor #34 fällt diese Suite.

Importiert `pdms` nicht.
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


def versuch(fn, ersatz=None):
    """Ergebnis von fn(), oder `ersatz`, wenn die Fassung es nicht kann."""
    try:
        return fn()
    except (TypeError, AttributeError) as e:
        print(f"      (Fassung kann das nicht: {e!r})")
        return ersatz


class _Abbruch(Exception):
    pass


WURZEL = tempfile.mkdtemp(prefix="fg_loeschen_")


def bestand():
    """Ein Auftrag a mit zwei Stufen Unterobjekten (b, c) per Kaskade, einem
    Werkzeug d, das er nur benutzt, und einem Projekt e, das auf ihn zeigt.

        e --gehoert--> a ==teil==> b ==teil==> c
                       a --nutzt--> d
    """
    db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
    for k in "abcde":
        db.create_node("x", k, {"name": k})
    kanten = {
        "ab": db.create_edge("x/a", "x/b", "teil", cascade_delete=True),
        "bc": db.create_edge("x/b", "x/c", "teil", cascade_delete=True),
        "ad": db.create_edge("x/a", "x/d", "nutzt"),
        "ea": db.create_edge("x/e", "x/a", "gehoert"),
    }
    return db, kanten


def lebt(db, k):
    return db.get_node(f"x/{k}") is not None


# =========================================================================
print("--- traverse mit Kantenfilter ---")
# =========================================================================
db = fg.FlatGraphDB(tempfile.mkdtemp(dir=WURZEL))
for k in ["kunde", "v1", "v2", "v3"]:
    db.create_node("x", k, {})
db.create_edge("x/kunde", "x/v1", "vertrag", meta={"laeuft": True})
db.create_edge("x/kunde", "x/v2", "vertrag", meta={"laeuft": False})
db.create_edge("x/v1", "x/v3", "vertrag", meta={"laeuft": True})
laufend = versuch(lambda: db.traverse("x/kunde", kantenfilter=lambda k: k.get("laeuft")), [])
check("Nur Kanten, die die Bedingung erfüllen, werden gegangen — über mehrere Stufen",
      laufend == ["x/v1", "x/v3"], str(laufend))
check("Ohne Filter bleibt traverse wie es war",
      sorted(db.traverse("x/kunde")) == ["x/v1", "x/v2", "x/v3"])


def veraendern(k):
    k["laeuft"] = False
    return True


fehler = None
try:
    db.traverse("x/kunde", kantenfilter=veraendern)
except TypeError as e:
    fehler = e
except Exception as e:
    fehler = e
check("Der Filter kann die Kante nicht verändern",
      isinstance(fehler, TypeError)
      and versuch(lambda: db.traverse("x/kunde", kantenfilter=lambda k: k.get("laeuft")), [])
      == ["x/v1", "x/v3"], repr(fehler))
db.close()

# =========================================================================
print("--- verwendungen ---")
# =========================================================================
db, kanten = bestand()
db.create_node("x", "f", {})
kante_fa = db.create_edge("x/f", "x/a", "gehoert")
db.soft_delete("x", "f")
v = versuch(lambda: db.verwendungen("x/a"), {})
check("Wer auf den Knoten zeigt, nach Kantenart; Enden im Papierkorb zählen nicht",
      v == {"gehoert": [(kanten["ea"], "x/e")]}, str(v))
v = versuch(lambda: db.verwendungen("x/a", direction="both"), {})
check("… mit direction='both' auch, worauf er selbst zeigt",
      v == {"gehoert": [(kanten["ea"], "x/e")], "teil": [(kanten["ab"], "x/b")],
            "nutzt": [(kanten["ad"], "x/d")]}, str(v))
db.close()

# =========================================================================
print("--- loeschfolgen ---")
# =========================================================================
db, kanten = bestand()
vorher = db.list_nodes("x", include_deleted=True)
f = versuch(lambda: db.loeschfolgen("x/a"), {})
check("Die Kaskade über zwei Stufen geht mit, das nur Benutzte nicht",
      f.get("knoten") == ["x/b", "x/c"], str(f.get("knoten")))
check("… und alle Kanten, die danach verschwinden",
      sorted(f.get("kanten", [])) == sorted(kanten.values()), str(f.get("kanten")))
check("loeschfolgen verändert nichts",
      db.list_nodes("x", include_deleted=True) == vorher and all(lebt(db, k) for k in "abcde"))
try:
    db.loeschfolgen("x/gibtsnicht")
    e = None
except Exception as fehler:
    e = fehler
check("Ein unbekannter Knoten: KnotenFehlt", type(e).__name__ == "KnotenFehlt", repr(e))
db.close()

# =========================================================================
print("--- soft_delete nimmt die Kaskade mit in den Papierkorb ---")
# =========================================================================
db, kanten = bestand()
# f war schon VORHER gelöscht, unabhängig von a — und hängt per Kaskade an a.
db.create_node("x", "f", {})
db.create_edge("x/a", "x/f", "teil", cascade_delete=True)
db.soft_delete("x", "f")
db.soft_delete("x", "a")
check("Nach soft_delete liegen die Kaskaden-Ziele sofort im Papierkorb",
      not lebt(db, "b") and not lebt(db, "c"))
check("… sichtbar markiert, mit wem sie gingen",
      (db.get_node_raw("x/c") or {}).get("_geloescht_durch") == "x/a",
      str(db.get_node_raw("x/c")))
check("Was er nur benutzt oder wer auf ihn zeigt, bleibt", lebt(db, "d") and lebt(db, "e"))
check("Unabhängig Gelöschtes bekommt keine fremde Markierung",
      "_geloescht_durch" not in (db.get_node_raw("x/f") or {}))
db = neu_oeffnen(db)
check("… und das steht so auf der Platte", not lebt(db, "b") and not lebt(db, "c") and lebt(db, "d"))

db.restore_node("x", "a")
check("restore_node holt die Mitgelöschten zurück",
      all(lebt(db, k) for k in "abc"), str([lebt(db, k) for k in "abc"]))
check("… ohne Markierung", "_geloescht_durch" not in (db.get_node_raw("x/c") or {}))
check("… aber nicht, was unabhängig davon gelöscht war", not lebt(db, "f"))
db.close()

db, kanten = bestand()
db.soft_delete("x", "a")
db.restore_node("x", "b")
check("Einen Mitgelöschten einzeln zurückholen: nur ihn",
      lebt(db, "b") and not lebt(db, "a") and not lebt(db, "c"))
db.close()

db, kanten = bestand()
try:
    with db.transaction():
        db.soft_delete("x", "a")
        raise _Abbruch()
except _Abbruch:
    pass
check("In einer zurückgerollten Transaktion: alle wieder da",
      all(lebt(db, k) for k in "abc"))
db = neu_oeffnen(db)
check("… auch auf der Platte", all(lebt(db, k) for k in "abc"))
db.close()

# =========================================================================
print("--- Der Müllsammler löscht nur, was im Papierkorb lag ---")
# =========================================================================
db, kanten = bestand()
db.soft_delete("x", "a")
db.run_garbage_collection()
check("Nach soft_delete und Müllsammler sind a, b, c endgültig weg",
      all(db.get_node_raw(f"x/{k}") is None for k in "abc"))
check("… mit ihren Kanten", db.get_connected("x/e", rel_type="gehoert") == []
      and db.get_edge(kanten["ad"]) is None)
check("… und d, e bleiben", lebt(db, "d") and lebt(db, "e"))
db.close()

# Ein Rest aus der Zeit davor: die Löschmarke ohne Kaskade (so hinterliess
# sie jede Fassung bis 3.0.0). Der Müllsammler darf b und c nicht mehr im
# selben Lauf endgültig löschen — sie waren nie im Papierkorb.
db, kanten = bestand()
db.update_node("x", "a", {"_deletion_flag": "2026-09-01T00:00:00+00:00", "_keep_asset": True})
db.run_garbage_collection()
check("Alter Rest: der Markierte ist weg",  db.get_node_raw("x/a") is None)
check("… die Kaskaden-Ziele liegen im Papierkorb, nicht endgültig gelöscht",
      db.get_node_raw("x/b") is not None and not lebt(db, "b"), str(db.get_node_raw("x/b")))
db.run_garbage_collection()
check("… und gehen erst beim nächsten Lauf", db.get_node_raw("x/b") is None)
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)

bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
