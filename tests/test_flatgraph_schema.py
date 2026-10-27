"""
Schema: optionale Felder und die Bestandsprüfung `schema_verstoesse`.

Schemata gelten beim Schreiben. Ändert man sie später, sagt nur
`schema_verstoesse`, wo der Bestand nicht mehr passt — mit derselben
Prüfung wie beim Schreiben.

    python tests/test_flatgraph_schema.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                       # noqa: E402

fg = lade()
results = []
WURZEL = tempfile.mkdtemp(prefix="fg_schema_")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


def wirft(fn):
    try:
        fn()
        return None
    except Exception as e:
        return type(e).__name__


def frisch(name, **kw):
    return fg.FlatGraphDB(os.path.join(WURZEL, name), **kw)


# --- optionale Felder -------------------------------------------------------
print("--- optional ---")
db = frisch("opt", schemas={"raeume": {
    "name": str,
    "kommentar": {"type": str, "optional": True},
    "volumen": {"type": float, "optional": True},
    "klasse": {"type": str, "options": ["A", "B"], "optional": True},
}})
check("Ein optionales Feld darf fehlen",
      wirft(lambda: db.create_node("raeume", "r1", {"name": "Hof"})) is None)
check("Ein optionales Feld mit None zählt als fehlend",
      wirft(lambda: db.create_node("raeume", "r2", {"name": "Hof", "kommentar": None})) is None)
check("Ein vorhandenes optionales Feld wird auf den Typ geprüft",
      wirft(lambda: db.create_node("raeume", "r3", {"name": "Hof", "volumen": "gross"}))
      == "TypeError")
check("Ein vorhandenes optionales Feld wird gegen die Optionen geprüft",
      wirft(lambda: db.create_node("raeume", "r4", {"name": "Hof", "klasse": "C"}))
      == "ValueError")
check("Ein erlaubter Wert im optionalen Aufzählungsfeld geht durch",
      wirft(lambda: db.create_node("raeume", "r5", {"name": "Hof", "klasse": "A"})) is None)
check("Das Pflichtfeld daneben bleibt Pflicht",
      wirft(lambda: db.create_node("raeume", "r6", {"kommentar": "x"})) == "ValueError")
check("update_node prüft ein optionales Feld ebenfalls",
      wirft(lambda: db.update_node("raeume", "r1", {"volumen": "gross"})) == "TypeError")
db.close()

# --- Bestandsprüfung --------------------------------------------------------
print("--- schema_verstoesse ---")
db = frisch("best")
db.create_node("raeume", "r1", {"name": "Hof"})
db.create_node("raeume", "r2", {"name": "Keller", "klasse": "C"})
db.create_node("raeume", "r3", {"name": 5})
db.create_node("raeume", "r4", {"name": "Dach"})
db.create_node("raeume", "r5", {})
db.soft_delete("raeume", "r5")
db.create_node("geraete", "g1", {"raum": "raeume/r1"})
db.create_node("geraete", "g2", {"raum": "raeume/r9"})
check("Ohne Schema gibt es nichts zu melden", db.schema_verstoesse() == [])

db.schemas = {
    "raeume": {"name": str, "klasse": {"type": str, "options": ["A", "B"], "optional": True}},
    "geraete": {"raum": {"type": "link", "target": "raeume"}},
}
v = db.schema_verstoesse()
refs = sorted((r, f) for r, f, _ in v)
check("Gemeldet werden Typfehler, falscher Wert und tote Verweise",
      refs == [("geraete/g2", "raum"), ("raeume/r2", "klasse"), ("raeume/r3", "name")],
      str(refs))
check("Passende Knoten fehlen in der Meldung",
      not any(r in ("raeume/r1", "raeume/r4", "geraete/g1") for r, _, _ in v))
check("Weich gelöschte Knoten fehlen in der Meldung",
      not any(r == "raeume/r5" for r, _, _ in v))
check("Eine Sammlung lässt sich einzeln fragen",
      [r for r, _, _ in db.schema_verstoesse("geraete")] == ["geraete/g2"])
check("Jede Meldung trägt einen Grundtext",
      all(isinstance(t, str) and t for _, _, t in v))

db.schemas["raeume"]["pflicht"] = int
v2 = db.schema_verstoesse("raeume")
check("Ein neues Pflichtfeld meldet jeden Knoten, der es nicht hat",
      sorted(r for r, f, _ in v2 if f == "pflicht") == ["raeume/r1", "raeume/r2",
                                                         "raeume/r3", "raeume/r4"])
check("Die Meldung ändert den Bestand nicht",
      db.get_node("raeume/r3")["name"] == 5)
check("Schreiben und Melden urteilen gleich",
      wirft(lambda: db.create_node("raeume", "r7", {"name": "x", "pflicht": 1})) is None
      and not [1 for r, _, _ in db.schema_verstoesse("raeume") if r == "raeume/r7"])
db.close()

shutil.rmtree(WURZEL, ignore_errors=True)
print("\n" + "=" * 60)
print(f"Geprüft: {fg.__pfad__}  ({fg.__version__})")
failed = [r for r in results if not r[1]]
print(f"{len(results) - len(failed)}/{len(results)} Checks bestanden")
sys.exit(1 if failed else 0)
