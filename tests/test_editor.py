"""
Graph-Editor (`editor/editor.py`): die Logik hinter der Oberfläche.

Zugesagt und hier geprüft:
  - Anlegen vergibt die Kennung selbst und legt denselben Namen im selben
    Typ nicht zweimal an
  - Typ- und Beziehungsnamen mit Leerzeichen werden zu gültigen Namen
  - Einhängen verschiebt (alte Kante weg, neue hin) und lehnt Kreise ab
  - Massenerfassung: Einrücken hängt unter die Zeile darüber, „Typ: Name“
    setzt den Typ, „Pumpe 12: Nord“ bleibt ein Name
  - Löschen legt in den Papierkorb, Zurückholen bringt Knoten samt Kanten zurück
  - Der Zustand zeigt keine Kanten zu gelöschten Knoten
  - Mit Datenmodell: Typen, Namensfelder, Auswahlwerte, Pflicht-Lücken,
    Beziehungsarten (auch mit Klammern), Kanten-Attribute, umgekehrtes Einhängen

Gegenprobe: jede Eigenschaft einzeln an einer mutierten Kopie von
`editor/editor.py` (siehe CLAUDE.md); EDITOR_DATEI zeigt auf die Kopie.
"""
import importlib.util
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
from flatgraph_laden import lade                                        # noqa: E402

sys.modules["flatgraph"] = lade()
pfad = os.environ.get("EDITOR_DATEI", "editor/editor.py")
spec = importlib.util.spec_from_file_location("editor_pruefling", pfad)
ed_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ed_mod)
Editor, Fehler = ed_mod.Editor, ed_mod.Fehler

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"  {detail}" if detail else ""))


WURZEL = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(WURZEL, "b"))


def kanten(art=None):
    return {(k["von"], k["nach"]) for k in e.zustand()["kanten"] if art in (None, k["art"])}


# --- Anlegen ---
a, neu = e.knoten_anlegen("Raum Typ", "Reinraum C")
check("Anlegen: Typ mit Leerzeichen wird gültig, Kennung aus dem Namen",
      a == "Raum_Typ/reinraum_c" and neu, a)
b, neu2 = e.knoten_anlegen("Raum Typ", "  reinraum c ")
check("Anlegen: gleicher Name im Typ (Gross/klein egal) wird nicht doppelt angelegt",
      b == a and not neu2)
c, _ = e.knoten_anlegen("Equipment", "Autoklav")
d, _ = e.knoten_anlegen("Equipment", "Reinraum C")
check("Anlegen: gleicher Name in anderem Typ ist ein eigenes Objekt", d != a)
try:
    e.knoten_anlegen("Equipment", "  ")
    check("Anlegen: leerer Name wird abgelehnt", False)
except Fehler:
    check("Anlegen: leerer Name wird abgelehnt", True)

# --- Einhängen / Verschieben / Kreis ---
p1, _ = e.knoten_anlegen("Plant", "Nord")
p2, _ = e.knoten_anlegen("Plant", "Süd")
e.einhaengen(p1, c, "enthält")
check("Einhängen: Kante Eltern -> Kind", kanten("enthält") == {(p1, c)})
alt = next(k["id"] for k in e.zustand()["kanten"])
e.einhaengen(p2, c, "enthält", alte_kante=alt)
check("Verschieben: alte Kante weg, neue da", kanten("enthält") == {(p2, c)})
e.einhaengen(p2, c, "enthält")
check("Doppelt einhängen ist kein Fehler und legt keine zweite Kante an",
      len(e.zustand()["kanten"]) == 1)
try:
    e.einhaengen(c, p2, "enthält")
    check("Kreis wird abgelehnt", False)
except Fehler:
    check("Kreis wird abgelehnt", True)
check("Nach abgelehntem Kreis ist nichts geändert", kanten("enthält") == {(p2, c)})
try:
    e.einhaengen(c, c, "enthält")
    check("Ein Objekt unter sich selbst wird abgelehnt", False)
except Fehler:
    check("Ein Objekt unter sich selbst wird abgelehnt", True)

# --- Massenerfassung ---
neu, vorh, hin = e.massen(
    "Werk Ost\n  Halle 1\n    Pumpe 12: Nord\n    Raum: Kühlraum\n  Halle 2\n\tSensor",
    "Anlage", "gehört zu", unter=None)
z = e.zustand()
namen = {k["name"]: k for k in z["knoten"]}
check("Massen: 6 Zeilen -> 6 neue Objekte", neu == 6 and vorh == 0 and not hin, f"{neu}/{vorh}/{hin}")
check("Massen: „Typ: Name“ setzt den Typ", namen["Kühlraum"]["typ"] == "Raum")
check("Massen: Präfix mit Ziffer bleibt Teil des Namens",
      "Pumpe 12: Nord" in namen and namen["Pumpe 12: Nord"]["typ"] == "Anlage")
ref = lambda n: namen[n]["ref"]
check("Massen: Einrücken hängt unter die Zeile darüber",
      kanten("gehört zu") == {(ref("Werk Ost"), ref("Halle 1")),
                              (ref("Halle 1"), ref("Pumpe 12: Nord")),
                              (ref("Halle 1"), ref("Kühlraum")),
                              (ref("Werk Ost"), ref("Halle 2")),
                              (ref("Halle 2"), ref("Sensor"))}, str(kanten("gehört zu")))
neu, vorh, _ = e.massen("Werk Ost\n  Halle 1", "Anlage", "gehört zu")
check("Massen: Vorhandenes wird wiederverwendet, nichts doppelt",
      neu == 0 and vorh == 2 and len(kanten("gehört zu")) == 5)
e.massen("Neu A\nNeu B", "Anlage", "gehört zu", unter=ref("Werk Ost"))
check("Massen: oberste Ebene hängt unter `unter`",
      (ref("Werk Ost"), e.zustand() and next(k["ref"] for k in e.zustand()["knoten"] if k["name"] == "Neu A"))
      in kanten("gehört zu"))
_, _, hin = e.massen("Halle 1\n  Werk Ost", "Anlage", "gehört zu")
check("Massen: ein Kreis meldet einen Hinweis statt abzubrechen", len(hin) == 1, str(hin))

# --- Löschen / Zurückholen ---
n_vorher = len(e.zustand()["kanten"])
e.knoten_loeschen(ref("Halle 1"))
z = e.zustand()
check("Löschen: Knoten weg, keine Kanten zu gelöschten Knoten",
      all(k["ref"] != ref("Halle 1") for k in z["knoten"])
      and all(ref("Halle 1") not in (k["von"], k["nach"]) for k in z["kanten"]))
e.knoten_zurueck(ref("Halle 1"))
check("Zurückholen: Knoten und seine Kanten sind wieder da",
      len(e.zustand()["kanten"]) == n_vorher)

# --- Felder ---
e.knoten_aendern(c, name="Autoklav 2", felder={"Kritikalität": "hoch"})
k = next(k for k in e.zustand()["knoten"] if k["ref"] == c)
check("Ändern: Name und freies Feld", k["name"] == "Autoklav 2" and k["felder"] == {"Kritikalität": "hoch"})

# --- Bestand überlebt Neustart ---
e.schliessen()
e = Editor(os.path.join(WURZEL, "b"))
check("Neustart: Objekte bleiben", any(k["name"] == "Autoklav 2" for k in e.zustand()["knoten"]))
check("Neustart: Kanten bleiben", (p2, c) in kanten("enthält"))
e.schliessen()

# --- Mit Datenmodell (editor/modell.json) ---
Modell = ed_mod.Modell
m = Modell(os.environ.get("MODELL_DATEI", "editor/modell.json"))
check("Modell: 10 Typen, Namensfeld je Typ aus dem Modell",
      len(m.typen) == 10 and m.typen["Plant"]["namefeld"] == "PlantName"
      and m.typen["Raum"]["namefeld"] == "RoomName", str(list(m.typen)))
check("Modell: „Steht in“ und „steht in“ sind eine Art",
      sum(1 for a in m.arten if a.casefold() == "steht in") == 1 and "steht in" in m.arten)
check("Modell: Kanten-Attribut hängt an „führt zu Nachfolger (0:n)“",
      list(m.kantenfelder) == ["führt zu Nachfolger (0:n)"], str(m.kantenfelder))

W2 = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(W2, "b"), m)
try:
    e.knoten_anlegen("Ding", "X")
    check("Modell: unbekannter Typ wird abgelehnt", False)
except Fehler:
    check("Modell: unbekannter Typ wird abgelehnt", True)
pl, _ = e.knoten_anlegen("plant", "Linie 1")
check("Modell: Typ gross/klein egal, Name liegt im Namensfeld des Modells",
      pl == "Plant/linie_1" and e.db.get_node(pl)["PlantName"] == "Linie 1")
k = next(k for k in e.zustand()["knoten"] if k["ref"] == pl)
check("Modell: fehlende Pflichtfelder stehen als Lücke da",
      sorted(k["fehlt"]) == ["PlantGXP", "PlantID", "PlantType"], str(k["fehlt"]))
try:
    e.knoten_aendern(pl, felder={"PlantGXP": "vielleicht"})
    check("Modell: Wert ausserhalb der Auswahl wird abgelehnt", False)
except Fehler:
    check("Modell: Wert ausserhalb der Auswahl wird abgelehnt", True)
e.knoten_aendern(pl, felder={"PlantGXP": "GXP", "PlantID": "P-1", "PlantType": "Einzelplatz"})
k = next(k for k in e.zustand()["knoten"] if k["ref"] == pl)
check("Modell: gültige Werte schliessen die Lücke", k["fehlt"] == [])

r1, _ = e.knoten_anlegen("Raum", "Raum 1")
eq, _ = e.knoten_anlegen("Equipment", "Autoklav")
try:
    e.kante_anlegen(eq, r1, "wohnt in")
    check("Modell: unbekannte Beziehungsart wird abgelehnt", False)
except Fehler as f:
    check("Modell: unbekannte Beziehungsart wird abgelehnt, mit den erlaubten Arten",
          "im Modell nicht" in str(f) and "steht in" in str(f), str(f))
e.kante_anlegen(eq, r1, "Steht in")
check("Modell: „Steht in“ wird als „steht in“ gespeichert und angezeigt",
      [(k["art"]) for k in e.zustand()["kanten"]] == ["steht in"])

s1, _ = e.knoten_anlegen("Prozessschritt", "Waschen")
s2, _ = e.knoten_anlegen("Prozessschritt", "Trocknen")
NACH = "führt zu Nachfolger (0:n)"
e.kante_anlegen(s1, s2, NACH, felder={"ProcessstepType": "Wartezeit / Haltezeit"})
kk = next(k for k in e.zustand()["kanten"] if k["art"] == NACH)
check("Kanten-Attribut: Art mit Klammern wird original angezeigt, Attribut gespeichert",
      kk["felder"] == {"ProcessstepType": "Wartezeit / Haltezeit"}, str(kk))
try:
    e.kante_anlegen(eq, r1, "gehört zu", felder={"ProcessstepType": "Standard-Fertigungsschritt"})
    check("Kanten-Attribut: an der falschen Art wird abgelehnt", False)
except Fehler:
    check("Kanten-Attribut: an der falschen Art wird abgelehnt", True)
try:
    e.kante_aendern(kk["id"], {"ProcessstepType": "irgendwas"})
    check("Kanten-Attribut: Wert ausserhalb der Auswahl wird abgelehnt", False)
except Fehler:
    check("Kanten-Attribut: Wert ausserhalb der Auswahl wird abgelehnt", True)
e.kante_aendern(kk["id"], {"ProcessstepType": "Gate / In-Process Control (IPC)"})
k2 = [k for k in e.zustand()["kanten"] if k["art"] == NACH]
check("Kanten-Attribut ändern: gleiche Enden, neuer Wert, genau eine Kante",
      len(k2) == 1 and (k2[0]["von"], k2[0]["nach"]) == (s1, s2)
      and k2[0]["felder"] == {"ProcessstepType": "Gate / In-Process Control (IPC)"})

e.einhaengen(r1, eq, "gehört zu", umgekehrt=True)
check("Einhängen umgekehrt: die Kante läuft Kind -> Eltern",
      any((k["von"], k["nach"], k["art"]) == (eq, r1, "gehört zu") for k in e.zustand()["kanten"]))
try:
    e.einhaengen(eq, r1, "gehört zu", umgekehrt=True)
    check("Einhängen umgekehrt: Kreis wird erkannt", False)
except Fehler:
    check("Einhängen umgekehrt: Kreis wird erkannt", True)

neu, _, hin = e.massen("Plant: Linie 2\n  Equipment: Pumpe\n  Raum: Halle", "Plant", "ist Teil von",
                       umgekehrt=True)
nm = {k["name"]: k["ref"] for k in e.zustand()["knoten"]}
check("Massen mit Modell und umgekehrter Art: Kinder zeigen auf das Elternteil",
      not hin and {(k["von"], k["nach"]) for k in e.zustand()["kanten"] if k["art"] == "ist Teil von"}
      == {(nm["Pumpe"], nm["Linie 2"]), (nm["Halle"], nm["Linie 2"])}, str(hin))
try:
    e.massen("Plant: Nichts", "Plant", "wohnt in")
    check("Massen: unbekannte Art lehnt ab, bevor etwas angelegt wird", False)
except Fehler:
    check("Massen: unbekannte Art lehnt ab, bevor etwas angelegt wird",
          not any(k["name"] == "Nichts" for k in e.zustand()["knoten"]))

e.schliessen()
e = Editor(os.path.join(W2, "b"), m)
check("Neustart mit Modell: Art und Attribut bleiben erhalten",
      any(k["art"] == NACH and k["felder"] for k in e.zustand()["kanten"]))
e.schliessen()
shutil.rmtree(W2, ignore_errors=True)

# --- Notiz mit [[Verweisen]] ---
W3 = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(W3, "b"))
x, _ = e.knoten_anlegen("Raum", "Reinraum C")
y, _ = e.knoten_anlegen("Equipment", "Autoklav")
e.knoten_aendern(y, notiz="steht im [[Reinraum C]] und [[reinraum c]].")
ky = next(k for k in e.zustand()["knoten"] if k["ref"] == y)
check("Notiz: wird als eigenes Feld geliefert, nicht unter den Feldern",
      ky["notiz"].startswith("steht im") and "notiz" not in ky["felder"])
e.knoten_aendern(x, name="Reinraum D")
ky = next(k for k in e.zustand()["knoten"] if k["ref"] == y)
check("Umbenennen zieht [[Verweise]] in anderen Notizen nach (Gross/klein egal)",
      ky["notiz"] == "steht im [[Reinraum D]] und [[Reinraum D]].", ky["notiz"])
e.knoten_aendern(y, name="Autoklav 2")
check("Umbenennen mit eigener Notiz bleibt heil",
      next(k for k in e.zustand()["knoten"] if k["ref"] == y)["notiz"].startswith("steht im"))
e.schliessen()
shutil.rmtree(W3, ignore_errors=True)

# --- Anlegen im Kontext (Werte und Verknüpfungen aus dem Filter) ---
W4 = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(W4, "b"), m)
rm, _ = e.knoten_anlegen("Raum", "Reinraum C")
ref, neu = e.anlegen_im_kontext("Equipment", "Waage", {"EquipmentGXP": "GXP"},
                                [{"ziel": rm, "art": "steht in", "richtung": "aus"}])
kn = next(k for k in e.zustand()["knoten"] if k["ref"] == ref)
check("Kontext: Feld aus dem Filter ist gesetzt", kn["felder"].get("EquipmentGXP") == "GXP")
check("Kontext: Verknüpfung aus dem Filter ist angelegt",
      [(k["von"], k["nach"], k["art"]) for k in e.zustand()["kanten"]] == [(ref, rm, "steht in")])
try:
    e.anlegen_im_kontext("Equipment", "Pumpe", {"EquipmentGXP": "GXP"},
                         [{"ziel": rm, "art": "wohnt in"}])
    check("Kontext: eine fehlerhafte Verknüpfung nimmt auch das Objekt zurück", False)
except Fehler:
    check("Kontext: eine fehlerhafte Verknüpfung nimmt auch das Objekt zurück",
          not any(k["name"] == "Pumpe" for k in e.zustand()["knoten"]))
e.anlegen_im_kontext("Equipment", "Waage", None, [{"ziel": rm, "art": "gehört zu"}])
check("Kontext: gab es das Objekt schon, kommt nur die Verknüpfung dazu",
      sum(1 for k in e.zustand()["knoten"] if k["name"] == "Waage") == 1
      and sum(1 for k in e.zustand()["kanten"]) == 2)
e.schliessen()
shutil.rmtree(W4, ignore_errors=True)

shutil.rmtree(WURZEL, ignore_errors=True)
bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
