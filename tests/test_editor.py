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
check("Modell: „Steht in“ der Referenz beginnt wie die übrigen Arten klein",
      "steht in" in m.arten and "Steht in" not in m.arten)
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
dk, _ = e.knoten_anlegen("Dokument", "Spez")
try:
    e.kante_anlegen(dk, eq, "gehört zu", felder={"ProcessstepType": "Standard-Fertigungsschritt"})
    check("Kanten-Attribut: an der falschen Art wird abgelehnt", False)
except Fehler as f:
    check("Kanten-Attribut: an der falschen Art wird abgelehnt (das Paar selbst ist erlaubt)",
          "kein Attribut" in str(f), str(f))
check("Kanten-Attribut: das Modell nennt ProcessstepType als Pflicht der Nachfolger-Kante",
      m.kantenfelder[NACH][0]["pflicht"] is True)
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

pl2, _ = e.knoten_anlegen("Equipment", "Zweites Gerät")
e.einhaengen(r1, pl2, "steht in")
check("Einhängen: die Richtung folgt dem Regelwerk (Equipment steht in Raum, auch wenn der Raum der Eltern ist)",
      any((k["von"], k["nach"], k["art"]) == (pl2, r1, "steht in") for k in e.zustand()["kanten"]))
try:
    e.einhaengen(s2, s1, NACH)
    check("Einhängen: Kreis in der Nachfolge wird erkannt", False)
except Fehler:
    check("Einhängen: Kreis in der Nachfolge wird erkannt", True)
try:
    e.einhaengen(r1, pl2, "ist Teil von")
    check("Einhängen: Art ohne Regel für diese Typen wird abgelehnt", False)
except Fehler as f:
    check("Einhängen: Art ohne Regel für diese Typen wird abgelehnt, mit Hinweis auf das Erlaubte",
          "steht in" in str(f), str(f))

neu, _, hin = e.massen("Plant: Linie 2\n  Equipment: Pumpe\n    Raum: Halle", "Plant")
nm = {k["name"]: k["ref"] for k in e.zustand()["knoten"]}
check("Massen mit Regelwerk: Art und Richtung ergeben sich aus den Typen (Pumpe ist Teil von Linie 2, Pumpe steht in Halle)",
      not hin and {(k["von"], k["nach"], k["art"]) for k in e.zustand()["kanten"] if k["von"] in (nm["Pumpe"], nm["Linie 2"])}
      == {(nm["Pumpe"], nm["Linie 2"], "ist Teil von"), (nm["Pumpe"], nm["Halle"], "steht in")}, str(hin))
_, _, hin = e.massen("Plant: Linie 3\n  Dokument: Plan", "Plant")
check("Massen: eine Zeile ohne passende Beziehung wird gemeldet, das Objekt aber angelegt",
      len(hin) == 0 and any(k["name"] == "Plan" for k in e.zustand()["knoten"]))
_, _, hin = e.massen("Material: Pulver\n  Equipment: Mischer", "Material")
check("Massen: zwischen Material und Equipment gibt es keine Beziehung -> Hinweis statt Verknüpfung",
      len(hin) == 1 and "keine Beziehung" in hin[0], str(hin))
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
rm, _ = e.knoten_anlegen("Plant", "Linie C")
ref, neu = e.anlegen_im_kontext("Equipment", "Waage", {"EquipmentGXP": "GXP"},
                                [{"ziel": rm, "art": "ist Teil von", "richtung": "aus"}])
kn = next(k for k in e.zustand()["knoten"] if k["ref"] == ref)
check("Kontext: Feld aus dem Filter ist gesetzt", kn["felder"].get("EquipmentGXP") == "GXP")
check("Kontext: Verknüpfung aus dem Filter ist angelegt",
      [(k["von"], k["nach"], k["art"]) for k in e.zustand()["kanten"]] == [(ref, rm, "ist Teil von")])
try:
    e.anlegen_im_kontext("Equipment", "Pumpe", {"EquipmentGXP": "GXP"},
                         [{"ziel": rm, "art": "wohnt in"}])
    check("Kontext: eine fehlerhafte Verknüpfung nimmt auch das Objekt zurück", False)
except Fehler:
    check("Kontext: eine fehlerhafte Verknüpfung nimmt auch das Objekt zurück",
          not any(k["name"] == "Pumpe" for k in e.zustand()["knoten"]))
plk, _ = e.knoten_anlegen("Plant", "Linie K")
e.anlegen_im_kontext("Equipment", "Waage", None, [{"ziel": plk}])
check("Kontext: gab es das Objekt schon, kommt nur die Verknüpfung dazu (Art und Richtung aus dem Regelwerk)",
      sum(1 for k in e.zustand()["knoten"] if k["name"] == "Waage") == 1
      and sum(1 for k in e.zustand()["kanten"]) == 2
      and any((k["von"], k["nach"], k["art"]) == (ref, plk, "ist Teil von") for k in e.zustand()["kanten"]))
e.schliessen()
shutil.rmtree(W4, ignore_errors=True)

# --- Massenbearbeitung ---
W5 = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(W5, "b"), m)
q1, _ = e.knoten_anlegen("Equipment", "Pumpe")
q2, _ = e.knoten_anlegen("Equipment", "Waage")
q3, _ = e.knoten_anlegen("Plant", "Linie")
rm2, _ = e.knoten_anlegen("Plant", "Halle")
g, u = e.mehrere_aendern([q1, q2, q3], {"EquipmentGXP": "GXP"})
fe = {k["ref"]: k["felder"] for k in e.zustand()["knoten"]}
check("Masse: Feld wird an allen Objekten des Typs gesetzt",
      fe[q1].get("EquipmentGXP") == "GXP" and fe[q2].get("EquipmentGXP") == "GXP" and g == 2)
check("Masse: ein Feld eines anderen Typs wird übersprungen, nicht angelegt",
      "EquipmentGXP" not in fe[q3] and u == 1)
try:
    e.mehrere_aendern([q1, q2], {"EquipmentCategory": "Kat 9"})
    check("Masse: ungültiger Auswahlwert wird abgelehnt", False)
except Fehler:
    check("Masse: ungültiger Auswahlwert wird abgelehnt", True)
try:
    e.mehrere_aendern([q1, q2], {"EquipmentName": "Gleich"})
    check("Masse: der Name lässt sich nicht für mehrere setzen", False)
except Fehler:
    check("Masse: der Name lässt sich nicht für mehrere setzen",
          [k["name"] for k in e.zustand()["knoten"] if k["ref"] in (q1, q2)] == ["Pumpe", "Waage"])
try:
    e.mehrere_aendern([q1, q2], {"EquipmentCategory": "Kat 1"}, [{"ziel": rm2, "art": "wohnt in"}])
    check("Masse: ein Fehler beim Verknüpfen nimmt auch die Felder zurück", False)
except Fehler:
    check("Masse: ein Fehler beim Verknüpfen nimmt auch die Felder zurück",
          "EquipmentCategory" not in next(k for k in e.zustand()["knoten"] if k["ref"] == q1)["felder"])
e.mehrere_aendern([q1, q2], None, [{"ziel": rm2, "art": "ist Teil von"}])
check("Masse: alle werden mit dem Ziel verknüpft",
      {(k["von"], k["nach"]) for k in e.zustand()["kanten"]} == {(q1, rm2), (q2, rm2)})
e.mehrere_aendern([q1, q2], None, [{"ziel": rm2, "art": "ist Teil von"}])
check("Masse: nochmal verknüpfen legt nichts doppelt an", len(e.zustand()["kanten"]) == 2)
e.mehrere_aendern([q1, q3], None, [{"ziel": q3}])
check("Masse: das Ziel unter den Objekten wird nicht mit sich selbst verknüpft (Art und Richtung aus dem Regelwerk)",
      {(k["von"], k["nach"], k["art"]) for k in e.zustand()["kanten"] if k["art"] == "ist Teil von"} == {(q1, q3, "ist Teil von"), (q1, rm2, "ist Teil von"), (q2, rm2, "ist Teil von")})
try:
    e.mehrere_aendern([q1, q2], None, [{"ziel": q3, "art": "verwendet"}])
    check("Masse: eine Art, die zwischen diesen Typen nicht vorgesehen ist, wird abgelehnt", False)
except Fehler:
    check("Masse: eine Art, die zwischen diesen Typen nicht vorgesehen ist, wird abgelehnt", True)
e.mehrere_loeschen([q1, q2])
check("Masse: löschen legt alle in den Papierkorb",
      not any(k["ref"] in (q1, q2) for k in e.zustand()["knoten"]))
e.mehrere_zurueck([q1, q2])
check("Masse: zurückholen bringt alle samt Verknüpfungen zurück",
      any(k["ref"] == q1 for k in e.zustand()["knoten"]) and len(e.zustand()["kanten"]) == 3)
e.schliessen()
shutil.rmtree(W5, ignore_errors=True)

# --- Reichweite über flatgraphs traverse ---
W6 = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(W6, "b"), m)
pr, _ = e.knoten_anlegen("Prozess", "P1")
s1, _ = e.knoten_anlegen("Prozessschritt", "S1")
s2, _ = e.knoten_anlegen("Prozessschritt", "S2")
pa, _ = e.knoten_anlegen("Parameter", "A")
pb, _ = e.knoten_anlegen("Parameter", "B")
e1, _ = e.knoten_anlegen("Equipment", "E1")
e2, _ = e.knoten_anlegen("Equipment", "E2")
rr, _ = e.knoten_anlegen("Plant", "PL")
for v, n, a in [(s1, pr, "ist Teil von"), (s2, pr, "ist Teil von"), (s1, pa, "definiert"),
                (s2, pb, "definiert"), (e1, pa, "überwacht/regelt"), (e2, pb, "überwacht/regelt"),
                (e1, rr, "ist Teil von"), (e2, rr, "ist Teil von")]:
    e.kante_anlegen(v, n, a)
r = e.reichweite([pr], "beide", 3)[pr]
check("Reichweite: Tiefe je Ziel (Schritte 1, Parameter 2, Equipment 3)",
      r[s1] == 1 and r[s2] == 1 and r[pa] == 2 and r[pb] == 2 and r[e1] == 3 and r[e2] == 3, str(r))
check("Reichweite: die Tiefe begrenzt", pa not in e.reichweite([pr], "beide", 1)[pr])
check("Reichweite: Richtung „ein“ folgt nur eingehenden Kanten",
      set(e.reichweite([pr], "ein", 3)[pr]) == {s1, s2} and e.reichweite([pr], "aus", 3)[pr] == {})
check("Reichweite: Arten beschränken den Weg",
      set(e.reichweite([e1], "beide", 3, ["überwacht/regelt"])[e1]) == {pa}, str(e.reichweite([e1], "beide", 3, ["überwacht/regelt"])))
check("Reichweite: die Nachbar-Parameter liegt über Schritt und Prozess in Tiefe 4",
      e.reichweite([pa], "beide", 4)[pa].get(pb) == 4 and pb not in e.reichweite([pa], "beide", 3)[pa])
tf = e.reichweite([pa], "beide", 4)[pa]
th = e.reichweite([pa], "beide", 4, None, True)[pa]
check("Reichweite: ohne halt kommt man über den Prozess zur anderen Parameter", pb in tf, str(tf))
check("Reichweite: mit halt läuft der Weg nicht durch Objekte gleichen Typs",
      pb not in th and e1 in th and s1 in th, str(th))
try:
    e.reichweite([pr], "beide", 2, ["wohnt in"])
    check("Reichweite: unbekannte Beziehungsart wird abgelehnt", False)
except Fehler:
    check("Reichweite: unbekannte Beziehungsart wird abgelehnt", True)
e.knoten_loeschen(s1)
check("Reichweite: gelöschte Objekte zählen nicht", s1 not in e.reichweite([pr], "beide", 3)[pr])
check("Reichweite: von einem gelöschten Objekt aus gibt es nichts", e.reichweite([s1], "beide", 3) == {})
e.schliessen()
shutil.rmtree(W6, ignore_errors=True)

# --- Regelwerk aus dem Datenmodell und Beispielbestand (Teststreifenfertigung) ---
import importlib.util as _iu
_sp = _iu.spec_from_file_location("beispiel_pruefling", "editor/beispiel.py")
bsp = _iu.module_from_spec(_sp)
_sp.loader.exec_module(bsp)
mm = Modell("editor/modell.json")
fehler = mm.lade_wegregeln("editor/regeln.json")
check("Regelwerk: die 22 Beziehungspaare und 12 Arten kommen aus dem Datenmodell",
      len(mm.regeln) == 22 and len(mm.arten) == 12, f"{len(mm.regeln)} Paare, {len(mm.arten)} Arten")
check("Regelwerk: „steht in“ gibt es nur zwischen Equipment und Raum — eine Plant liegt über ihre Equipments im Raum",
      {(r["von"], r["nach"]) for r in mm.regeln if r["art"] == "steht in"} == {("Equipment", "Raum")})
check("Regelwerk: Einstellungen für die Wege passen zum Modell", fehler == [], str(fehler))
check("Regelwerk: zwischen zwei Typen gibt es die erlaubten Arten mit Richtung",
      mm.zwischen("Raum", "Equipment") == [("steht in", "ein")] and mm.zwischen("Equipment", "Raum") == [("steht in", "aus")]
      and mm.zwischen("Material", "Equipment") == [] and mm.zwischen("Plant", "Raum") == [])
# Abgleich mit der Referenz (editor/datenmodell.pdf, Tabelle „OBJEKT TYPES“ und „OBJEKT TYP ATTRIBUTES“)
PDF_PAARE = {("Plant", "steht in", "Raum"), ("Plant", "führt aus", "Prozess"), ("Equipment", "ist Teil von", "Plant"),
             ("Equipment", "führt aus", "Prozessschritt"), ("Prozessschritt", "ist Teil von", "Prozess"),
             ("Prozessschritt", "benötigt Input", "Material"), ("Prozessschritt", "erzeugt Output", "Material"),
             ("Prozessschritt", "führt zu Nachfolger (0:n)", "Prozessschritt"), ("Equipment", "verwendet", "Medium"),
             ("Dokument", "gehört zu", "Equipment"), ("Dokument", "gehört zu", "Prozess"), ("Dokument", "gehört zu", "Prozessschritt"),
             ("Dokument", "gehört zu", "Plant"), ("Dokument", "gehört zu", "Raum"), ("Dokument", "gehört zu", "Software"),
             ("Software", "unterstützt", "Plant"), ("Software", "unterstützt", "Prozess"), ("Software", "unterstützt", "Prozessschritt"),
             ("Software", "steuert", "Equipment"), ("Prozessschritt", "verwendet", "Medium"),
             ("Prozessschritt", "definiert", "Parameter"), ("Equipment", "überwacht/regelt", "Parameter")}
# Bewusste Abweichung (Entscheidung 28.10.2026): in SAP hat jedes Equipment einen Raum
ABWEICHUNG = ({("Plant", "steht in", "Raum")}, {("Equipment", "steht in", "Raum")})
check("Referenz-PDF: die 22 Beziehungspaare stimmen bis auf die eine beschlossene Abweichung (Raum am Equipment) überein",
      {(r["von"], r["art"], r["nach"]) for r in mm.regeln} == (PDF_PAARE - ABWEICHUNG[0]) | ABWEICHUNG[1],
      str({(r["von"], r["art"], r["nach"]) for r in mm.regeln} ^ PDF_PAARE))
PDF_ATTR = {  # Typ: {Eigenschaft: (Beschriftung, Auswahlwerte oder None)}
    "Equipment": {"EquipmentName": ("Name", None), "EquipmentID": ("ID", None), "EquipmentGXP": ("GXP-Relevanz", ["GXP", "NGXP"]),
                  "EquipmentCategory": ("Kategorie", ["Kat 1", "Kat 2", "Kat 3", "Kat 4"])},
    "Software": {"SoftwareName": ("Name", None), "SoftwareID": ("ID", None), "SoftwareGXP": ("GXP-Relevanz", ["GXP", "NGXP"]),
                 "SoftwareCategory": ("Kategorie", None), "SoftwareType": ("Typ", ["Embedded / SPS-Software", "System- / Prozess-Software", "Enterprise-Software"]),
                 "SoftwareScope": ("Scope", ["Equipment-spezifisch (SPS, Firmware)", "Prozess- / Linienbezogen (eBR, Auswertetools)", "Plant-weit / Enterprise (MES, ERP, LIMS, QMS)"])},
    "Raum": {"RoomName": ("Name", None), "RoomID": ("ID", None), "RoomClass": ("Klasse", ["ISO 5", "ISO 6", "ISO 7", "ISO 8", "ISO 9"])},
    "Medium": {"MediumName": ("Name", None), "MediumID": ("ID", None), "MediumType": ("Type", ["Process Gases & Compressed Air", "Water Systems", "HVAC / Cleanroom Air"]),
               "MediumGXP": ("GXP-Relevanz", ["GXP", "NGXP"]), "MediumProductContact": ("Produktkontakt", ["Direct Impact", "Indirect Impact", "No Impact"])},
    "Prozess": {"ProcessName": ("Name", None), "ProcessID": ("ID", None)},
    "Prozessschritt": {"ProcessstepName": ("Name", None), "ProcessstepID": ("ID", None)},
    "Material": {"MaterialName": ("Name", None), "MaterialID": ("ID", None),
                 "MaterialType": ("Typ", ["Raw Material", "Component / Purchased Part", "Work in Progress (WIP) / Intermediate Product", "Sub-Assembly", "Finished Good", "Finished Medical Device"])},
    "Plant": {"PlantName": ("Name", None), "PlantID": ("ID", None), "PlantType": ("Type", ["Fertigungslinie", "Funktionsbereich (zB. Labor, QC)", "Einzelplatz"]),
              "PlantGXP": ("GXP-Relevanz", ["GXP", "NGXP"])},
    "Dokument": {"DocumentName": ("Name", None), "DocumentID": ("ID", None), "DocumentDMS": ("Ablageort", ["SAP P1U", "DiaDoc"]),
                 "DocumentType": ("Typ", ["GSP", "Specification", "Batch Record", "Validation Documentation", "Work Instruction", "Form", "Template"])},
    "Parameter": {"ParameterName": ("Name", None), "ParameterID": ("ID", None), "ParameterClass": ("Klasse", ["CPP", "PP"]),
                  "ParameterTargetValue": ("Sollwert", None), "ParameterAcceptanceRange": ("Tolerance", None),
                  "ParameterUnit": ("Einheit", ["°C", "bar", "sec", "min", "h", "rpm", "l", "g", "mg", "kg", "l/min", "%", "%rH"]),
                  "ParameterControlType": ("Kontrollart", ["Closed Loop", "Logged"])}}
ist_attr = {t: {f["name"]: (f["titel"], f["optionen"]) for f in mm.typen[t]["felder"]} for t in mm.typen}
check("Referenz-PDF: alle Eigenschaften mit Beschriftung und Auswahlwerten stimmen mit dem Datenmodell überein",
      ist_attr == PDF_ATTR, str([t for t in PDF_ATTR if ist_attr.get(t) != PDF_ATTR[t]]))
check("Referenz-PDF: die Einheit ist Auswahl oder freier Text, der Nachfolger-Typ ein Kanten-Attribut „Typ“",
      next(f for f in mm.typen["Parameter"]["felder"] if f["name"] == "ParameterUnit")["freitext"] is True
      and mm.kantenfelder["führt zu Nachfolger (0:n)"][0]["titel"] == "Typ")
W7 = tempfile.mkdtemp(prefix="editor_test_")
e = Editor(os.path.join(W7, "b"), mm)
O = bsp.bauen(e)
z = e.zustand()
check("Beispiel: baut mit strikter Prüfung durch (Typen, Auswahlwerte, Arten, Beziehungspaare)",
      len(z["knoten"]) >= 120 and len(z["kanten"]) >= 215, f'{len(z["knoten"])}/{len(z["kanten"])}')
typ = {k["ref"]: k["typ"] for k in z["knoten"]}
erlaubt = {(r["von"], r["art"], r["nach"]) for r in mm.regeln}
abweichend = [(typ[k["von"]], k["art"], typ[k["nach"]]) for k in z["kanten"] if (typ[k["von"]], k["art"], typ[k["nach"]]) not in erlaubt]
check("Beispiel: jede Verknüpfung ist ein erlaubtes Paar", abweichend == [], str(abweichend[:3]))
unbenutzt = erlaubt - {(typ[k["von"]], k["art"], typ[k["nach"]]) for k in z["kanten"]}
check("Beispiel: jedes der 22 Paare kommt mindestens einmal vor", unbenutzt == set(), str(unbenutzt))
namen = {k["ref"]: k["name"] for k in z["knoten"]}
erwartet = sorted(namen[O[k]] for k in ("a_rak", "a_nass", "a_tag", "a_zag", "a_rf", "a_rakc", "a_tc", "a_zc", "a_dos", "a_dd", "a_vak", "a_tv", "a_zv"))
mit_dok = {k["nach"] for k in z["kanten"] if k["art"] == "gehört zu" and typ[k["von"]] == "Dokument"}
ohne = sorted(namen[x] for x in typ if typ[x] == "Equipment" and x not in mit_dok)
check("Beispiel: Equipment ohne Dokument sind genau die absichtlichen Lücken",
      ohne == sorted(namen[O[k]] for k in ("oi01", "tr02", "sn01", "vp01", "w01", "rw01", "vk01", "ks01", "ms01", "kl01", "e_las", "e_ion", "e_vis", "e_gd", "e_mp")), str(ohne))
check("Beispiel: eine Pflichtfeld-Lücke (Mikroskop ohne Kategorie)",
      [k["name"] for k in z["knoten"] if "EquipmentCategory" in k["fehlt"]] == [namen[O["ms01"]]])

# Strenge: nur die Paare des Modells
try:
    e.verknuepfe(O["w01"], O["m_ag"])
    check("Strikt: Equipment und Material haben keine Beziehung -> abgelehnt", False)
except Fehler as f:
    check("Strikt: Equipment und Material haben keine Beziehung -> abgelehnt, mit Erklärung",
          "keine Beziehung" in str(f), str(f))
try:
    e.kante_anlegen(O["rca"], O["w01"], "steht in")
    check("Strikt: die falsche Richtung (Raum steht in Equipment) wird abgelehnt", False)
except Fehler as f:
    check("Strikt: die falsche Richtung (Raum steht in Equipment) wird abgelehnt, mit dem Erlaubten",
          "Equipment steht in Raum" in str(f), str(f))
n_vor = len(e.zustand()["kanten"])
e.verknuepfe(O["rca"], O["w01"])      # Art und Richtung ergeben sich aus den Typen
check("Verknüpfen ohne Art: Raum und Equipment ergibt „Equipment steht in Raum“ (schon vorhanden, kein Duplikat)",
      len(e.zustand()["kanten"]) == n_vor)
eq_neu, _ = e.knoten_anlegen("Equipment", "Neues Gerät")
e.verknuepfe(eq_neu, O["plq"])
e.verknuepfe(eq_neu, O["sw_ps"])
check("Verknüpfen ohne Art: die Richtung folgt dem Modell, egal in welcher Reihenfolge die Objekte genannt werden",
      {(k["von"], k["nach"], k["art"]) for k in e.zustand()["kanten"] if eq_neu in (k["von"], k["nach"])}
      == {(eq_neu, O["plq"], "ist Teil von"), (O["sw_ps"], eq_neu, "steuert")})
try:
    e.verknuepfe(eq_neu, O["plq"], "wohnt in")
    check("Strikt: eine unbekannte Art wird nicht stillschweigend ersetzt", False)
except Fehler:
    check("Strikt: eine unbekannte Art wird nicht stillschweigend ersetzt", True)
lo = Editor(os.path.join(W7, "locker"), mm, strikt=False)
a1, _ = lo.knoten_anlegen("Equipment", "A")
m1, _ = lo.knoten_anlegen("Material", "M")
lo.kante_anlegen(a1, m1, "verwendet")
check("Locker: ohne Strenge sind Art und Typen frei kombinierbar, nur die Art muss im Modell stehen",
      len(lo.zustand()["kanten"]) == 1)
lo.schliessen()

# Wege nach Regelwerk
mp = e.metapfade("Prozess", "Material", 2)
check("Regelwerk-Wege: Prozess -> Material nur über den Schritt (Input und Output)",
      sorted(tuple((x["art"], x["richtung"], x["typ"]) for x in w) for w in mp) == sorted([
          (("ist Teil von", "ein", "Prozessschritt"), ("benötigt Input", "aus", "Material")),
          (("ist Teil von", "ein", "Prozessschritt"), ("erzeugt Output", "aus", "Material"))]), str(mp))
check("Regelwerk-Wege: ein Dokument verbindet nicht zwei Dinge, die es nur gemeinsam betrifft",
      not any(x["typ"] == "Dokument" for w in e.metapfade("Prozess", "Material", 3) for x in w[:-1]))
check("Regelwerk-Wege: Dokument ist aber als Ziel erreichbar",
      any(w[-1]["typ"] == "Dokument" for w in e.metapfade("Prozess", "Dokument", 1)))
check("Regelwerk-Wege: der Nachfolger eines Schritts gehört nicht über den Vorgänger zu dessen Prozess",
      not any(x["art"].startswith("führt zu Nachfolger") for w in e.metapfade("Prozessschritt", "Prozess", 3) for x in w))
check("Regelwerk-Wege: ein Weg mit „Nachfolger“ besteht nur aus diesem einen Schritt",
      all(len(w) == 1 for w in e.metapfade("Prozess", None, 3) + e.metapfade("Prozessschritt", None, 3)
          if any(x["art"].startswith("führt zu Nachfolger") for x in w)))
check("Regelwerk-Wege: die Reihenfolge selbst bleibt als einzelner Schritt erreichbar (vor und zurück)",
      sorted(w[0]["richtung"] for w in e.metapfade("Prozessschritt", "Prozessschritt", 1) if w[0]["art"].startswith("führt zu Nachfolger")) == ["aus", "ein"])
check("Regelwerk-Wege: kein Weg über Geschwister (Equipment -> Raum -> Equipment gibt es nicht)",
      e.metapfade("Equipment", "Equipment", 3) == [])
ohne_modell = Editor(os.path.join(W7, "x"))
check("Regelwerk-Wege: ohne Modell keine Wege", ohne_modell.metapfade("Prozess", "Material", 2) == [])
ohne_modell.schliessen()
rg = e.reichweite([O["p_elek"]], modus="regel", tiefe=2, typen=["Parameter"])[O["p_elek"]]
check("Regelwerk: Parameter des Prozesses sind genau die 13 (Schritt definiert Parameter)",
      sorted(namen[x] for x in rg) == erwartet, f"{len(rg)}")
frei = e.reichweite([O["p_elek"]], "beide", 3)[O["p_elek"]]
check("Regelwerk: frei über alle Beziehungen wären es mehr Parameter (Streuung über Ablauf und Equipment)",
      len([x for x in frei if typ[x] == "Parameter"]) > 13, str(len([x for x in frei if typ[x] == "Parameter"])))
check("Regelwerk: Equipment, das einen Parameter überwacht (1 Schritt)",
      set(e.reichweite([O["a_tag"]], modus="regel", tiefe=1, typen=["Equipment"])[O["a_tag"]]) == {O["tr01"]})
check("Regelwerk: Equipment der Plant Montage (1 Schritt: Equipment ist Teil von Plant)",
      sorted(namen[x] for x in e.reichweite([O["plm"]], modus="regel", tiefe=1, typen=["Equipment"])[O["plm"]]) == sorted(namen[O[k]] for k in ("ma01", "sn01", "vp01")))
check("Regelwerk: die Tiefe zählt Typ-Schritte (Equipment -> Schritt -> Prozess = 2)",
      e.reichweite([O["ma01"]], modus="regel", tiefe=2, typen=["Prozess"])[O["ma01"]][O["p_mont"]] == 2
      and e.reichweite([O["ma01"]], modus="regel", tiefe=1, typen=["Prozess"])[O["ma01"]] == {})
check("Regelwerk: Schritte, die zum Prozess gehören — nur die eigenen, nicht die des Nachbarprozesses",
      {k for k, v in e.reichweite([O[x] for x in ("s_lam", "s_kuehl", "s_ag", "s_vak")], modus="regel", tiefe=2, typen=["Prozess"]).items()
       if O["p_elek"] in v} == {O["s_ag"], O["s_vak"]})
# --- Atlas: Primär → Sekundär, abgeleitete Spalten, Layouts ---
b_mo = e.bereich(O["rmo"])
check("Bereich: Equipments im Raum sind die, die dort stehen (wie in SAP, nicht alle der Plant)",
      set(b_mo.get("Equipment", [])) == {O["ma01"], O["sn01"]}, str(sorted(namen[x] for x in b_mo.get("Equipment", []))))
check("Bereich: die Räume einer Plant ergeben sich über ihre Equipments",
      set(e.bereich(O["plm"])["Raum"]) == {O["rmo"], O["rko"]})
check("Bereich: Prozessschritte am Prozess sind genau seine Schritte",
      set(e.bereich(O["p_mont"])["Prozessschritt"]) == {O["s_lam"], O["s_schn"], O["s_kon"]})
check("Bereich: ein Prozess hat genau einen Weg zu Equipment — über die Schritte, nicht grob über die Plant",
      [[s["typ"] for s in w] for w in e.kurze_wege("Prozess", 3)["Equipment"]] == [["Prozessschritt", "Equipment"]])
# Ein Gerät der Linie, das keinen Schritt ausführt: grob über die Plant wäre es dabei
reserve, _ = e.knoten_anlegen("Equipment", "Reserveofen", {"EquipmentID": "EQ-199", "EquipmentGXP": "GXP", "EquipmentCategory": "Kat 3"})
e.verknuepfe(reserve, O["pl1"])
z = e.zustand()
ausfuehrend = {k["von"] for k in z["kanten"] if k["art"] == "führt aus" and k["nach"] in e.bereich(O["p_elek"])["Prozessschritt"]}
check("Bereich: am Prozess beteiligt ist, wer einen seiner Schritte ausführt",
      set(e.bereich(O["p_elek"])["Equipment"]) == ausfuehrend and reserve not in ausfuehrend, str(len(ausfuehrend)))
check("Bereich: ohne Anker alle Objekte je Typ",
      sum(len(v) for v in e.bereich(None).values()) == len(e.zustand()["knoten"]) > 100)
sp_eq = {s["titel"]: s for s in e.spalten("Equipment")}
sp_pl = {s["titel"]: s for s in e.spalten("Plant")}
check("Spalten: der Raum einer Plant kommt über ihre Equipments und ist ab Werk sichtbar",
      [x["typ"] for x in sp_pl["Raum"]["pfad"]] == ["Equipment", "Raum"] and sp_pl["Raum"]["standard"]
      and [x["typ"] for x in sp_eq["Raum"]["pfad"]] == ["Raum"])
check("Spalten: Material als Input und als Output sind zwei Spalten, keine vermischte",
      {"Material (benötigt Input)", "Material (erzeugt Output)"} <= set(sp_eq) and "Material" not in sp_eq, str(sorted(sp_eq)))
check("Spalten: Vorgänger und Nachfolger eines Schritts sind getrennte Spalten",
      sorted(s["pfad"][0]["richtung"] for s in e.spalten("Prozessschritt") if s["typ"] == "Prozessschritt") == ["aus", "ein"])
tb = e.tabelle(O["p_elek"], "Parameter")
k_eq = next(s["key"] for s in tb["spalten"] if s["titel"] == "Equipment")
check("Tabelle: die 13 Parameter des Prozesses mit dem Equipment, das sie regelt",
      sorted(namen[x] for x in tb["zeilen"]) == erwartet and tb["werte"][O["a_tag"]][k_eq] == [O["tr01"]] and tb["werte"][O["a_rf"]][k_eq] == [])
check("Tabelle: die Zahlen je Typ sind die Größen des Bereichs", tb["zahlen"]["Parameter"] == 13 and tb["zahlen"]["Prozessschritt"] == 7, str(tb["zahlen"]))
check("Inspektor: das Umfeld eines Objekts sind dieselben Werte wie in seiner Tabellenzeile",
      e.umfeld(O["a_tag"])["werte"] == tb["werte"][O["a_tag"]])
e.layout_speichern("Equipment", "Qualifizierung", {"spalten": ["f:EquipmentID", k_eq], "sort": "name", "gruppe": ""}, vorgabe=True)
lay_neu = Editor.__new__(Editor); lay_neu._layoutdatei = e._layoutdatei
check("Layout: eine Variante liegt beim Bestand und ist Vorgabe für den Typ",
      lay_neu.layouts()["Equipment"]["vorgabe"] == "Qualifizierung"
      and lay_neu.layouts()["Equipment"]["varianten"]["Qualifizierung"]["spalten"][0] == "f:EquipmentID")
e.layout_speichern("Equipment", "Qualifizierung", None)
check("Layout: löschen entfernt Variante und Vorgabe, keine Arbeitsdatei bleibt liegen",
      e.layouts()["Equipment"] == {"varianten": {}, "vorgabe": None} and not os.path.exists(e._layoutdatei + ".arbeit"))
# --- Materialfluss: Produkte, Herkunft, Verwendung ---
check("Produkte: Endprodukte sind die Produkttypen, die nirgends mehr eingehen (die Dose geht in die Freigabe)",
      set(e.produkte()) == {O["o_bga"], O["m_dev"]}, str([namen[x] for x in e.produkte()]))
check("Produkte: Komponenten des Analysators sind die fünf Materialien der Endmontage",
      set(e.produktbaum()["kinder"][O["o_bga"]]) == {O[k] for k in ("o_o2", "o_glu", "o_elek", "o_geh", "o_schr")})
hk = e.fluss(O["o_bga"])
check("Herkunft: Stufen entlang der Materialien (O2-Sensor 1, Sensorlösung 2, Pool 3, Rohstoff 4)",
      [hk.get(O[k]) for k in ("o_o2", "o_lo2", "o_pool", "o_enz", "o_bg")] == [1, 2, 3, 4, 2], str([hk.get(O[k]) for k in ("o_o2", "o_lo2", "o_pool", "o_enz", "o_bg")]))
check("Herkunft: über die Vorgänger gehören Kalibrierung, Lagerung und Funktionsprüfung zur Kette",
      {O["x_kal"], O["x_lag"], O["x_fp"]} <= set(hk))
ht = e.fluss(O["m_dev"])
check("Herkunft: erzeugt ein Vorgänger ein Material, liegt es eine Stufe tiefer (lose Streifen unter der Dose, nicht daneben)",
      (ht[O["m_pack"]], ht[O["m_strip"]]) == (1, 2), str((ht.get(O["m_pack"]), ht.get(O["m_strip"]))))
check("Herkunft: eine Schleife im Ablauf führt nicht zurück (IPC gehört nicht zur Herkunft der Ag-Folie)",
      O["s_ipc"] not in e.fluss(O["m_foag"]))
vw = e.fluss_bereich(O["o_pool"], "verwendung")
check("Verwendung: der Pool geht in beide Lösungen, beide Sensoren und den Analysator — nicht in den Teststreifen",
      set(vw["Material"]) == {O[k] for k in ("o_lo2", "o_lglu", "o_o2", "o_glu", "o_bga")}, str([namen[x] for x in vw["Material"]]))
b_o2 = e.fluss_bereich(O["o_o2"])
check("Bereich O2-Sensor: Kalibrierung und Lagerung gehören dazu (kein neues Material), die Montage nicht",
      {O["x_kal"], O["x_lag"]} <= set(b_o2["Prozessschritt"]) and O["x_mon"] not in b_o2["Prozessschritt"]
      and set(b_o2["Plant"]) == {O["plc"], O["pl_o2"], O["pl_kal"]}, str([namen[x] for x in b_o2["Plant"]]))
tb_o2 = e.tabelle(O["o_o2"], "Equipment", "herkunft")
check("Tabelle entlang des Flusses: Equipments des O2-Sensors samt Chemlab und Kalibrierstand, ohne Montageplatz",
      {O["rw01"], O["e_cdu"], O["e_kal"]} <= set(tb_o2["zeilen"]) and O["e_mp"] not in tb_o2["zeilen"])
tm = e.tabelle(None, "Material")
check("Material-Spalten: der Pool geht in den Analysator, Stufe 3",
      tm["werte"][O["o_pool"]]["x:produkt"] == [O["o_bga"]] and tm["werte"][O["o_pool"]]["x:stufe"] == ["3"])
luecke, _ = e.knoten_anlegen("Material", "Zwischenprodukt ohne Herkunft", {"MaterialID": "MAT-990", "MaterialType": "Work in Progress (WIP) / Intermediate Product"})
roh, _ = e.knoten_anlegen("Material", "Rohstoff ohne Schritt", {"MaterialID": "MAT-991", "MaterialType": "Raw Material"})
lk = {k["ref"]: k["luecken"] for k in e.zustand()["knoten"]}
check("Lücke: ein Zwischenprodukt, das kein Schritt erzeugt, fällt auf — ein Rohstoff nicht",
      lk[luecke] and not lk[roh] and not any(lk[O[k]] for k in ("o_pool", "o_bg", "m_strip")))

# --- IDs aus SAP: je Typ eindeutig ---
try:
    e.knoten_anlegen("Equipment", "Andere Anlage", {"EquipmentID": "EQ-101"})
    check("ID: eine schon vergebene ID wird beim Anlegen abgelehnt", False)
except Fehler as f:
    check("ID: eine schon vergebene ID wird beim Anlegen abgelehnt, mit dem Objekt, das sie hat",
          "EQ-101" in str(f) and "Siebdruckanlage SD-01" in str(f), str(f))
try:
    e.knoten_aendern(O["oi01"], felder={"EquipmentID": " EQ-101 "})
    check("ID: eine schon vergebene ID wird beim Ändern abgelehnt", False)
except Fehler:
    check("ID: eine schon vergebene ID wird beim Ändern abgelehnt (Leerzeichen zählen nicht)", True)
e.knoten_aendern(O["sd01"], felder={"EquipmentID": "EQ-101", "EquipmentCategory": "Kat 3"})
check("ID: die eigene ID beim Ändern stehen zu lassen ist kein Fehler", e.db.get_node(O["sd01"])["EquipmentID"] == "EQ-101")
zweite, neu_w = e.knoten_anlegen("Equipment", "Analysenwaage W-01", {"EquipmentID": "EQ-399"})
check("ID: gleicher Name mit anderer ID ist ein anderes Objekt (zwei Waagen in SAP)", neu_w and zweite != O["w01"])
check("ID: gleicher Name ohne ID bleibt dasselbe Objekt", e.knoten_anlegen("Raum", "Montageraum") == (O["rmo"], False))
try:
    e.mehrere_aendern([O["ms01"], O["kl01"]], felder={"EquipmentID": "EQ-900"})
    check("ID: dieselbe ID für mehrere Objekte wird abgelehnt", False)
except Fehler:
    check("ID: dieselbe ID für mehrere Objekte wird abgelehnt", True)

# --- Import per Einfügen aus Sheets/Excel ---
ZIELE = ["name", "feld:EquipmentID", "bez:steht in|aus|Raum", "bez:ist Teil von|aus|Plant", "feld:EquipmentCategory"]
TEXT = ("Bezeichnung\tEquipment\tRaum\tFunktionsplatz\tKategorie\n"
        "Siebdruckanlage SD-01 (neu)\tEQ-101\tSiebdruckraum\tSensorfertigung Linie 1\tKat 2\n"
        "Plasmareiniger\tEQ-591\tR-591 Neuraum\tSensorfertigung Linie 1\tKat 1\n"
        "Optische Inline-Inspektion OI-01\tEQ-102\tTrockenraum\t\t\n")
vorher = len(e.zustand()["knoten"])
pr = e.importieren("Equipment", TEXT, ZIELE, probe=True)
check("Import: der Probelauf zählt neu und geändert und nennt neue Räume",
      (pr["neu"], pr["geaendert"], pr["fehler"], pr["neue_ziele"]) == (1, 2, [], {"Raum": ["R-591 Neuraum"]}), str(pr))
check("Import: der Probelauf ändert nichts", len(e.zustand()["knoten"]) == vorher
      and e.db.get_node(O["sd01"])["EquipmentName"] == "Siebdruckanlage SD-01" and not pr["uebernommen"])
ue = e.importieren("Equipment", TEXT, ZIELE, probe=False)
plasma = next(x["ref"] for x in ue["zeilen"] if x["name"] == "Plasmareiniger")
check("Import: vorhandene ID wird geändert (Name, Kategorie), neue angelegt",
      ue["uebernommen"] and e.db.get_node(O["sd01"])["EquipmentName"] == "Siebdruckanlage SD-01 (neu)"
      and e.db.get_node(O["sd01"])["EquipmentCategory"] == "Kat 2" and e.db.get_node(plasma)["EquipmentID"] == "EQ-591")
check("Import: der neue Raum ist angelegt, das neue Gerät steht darin und gehört zur Plant",
      [namen.get(x, e.db.get_node(x)["RoomName"]) for x in e.db.get_connected(plasma, "out", target_collection="Raum")] == ["R-591 Neuraum"]
      and e.db.get_connected(plasma, "out", target_collection="Plant") == [O["pl1"]])
check("Import: eine Beziehungsspalte ersetzt den bisherigen Raum (umgezogen, nicht in zwei Räumen)",
      e.db.get_connected(O["oi01"], "out", target_collection="Raum") == [O["rtr"]])
check("Import: eine leere Zelle ändert nichts (Plant bleibt)", e.db.get_connected(O["oi01"], "out", target_collection="Plant") == [O["pl1"]])
check("Import: die Zuordnung der Spalten ist für den Typ gemerkt",
      e.importe()["Equipment"]["Funktionsplatz"] == "bez:ist Teil von|aus|Plant")
nochmal = e.importieren("Equipment", TEXT, ZIELE, probe=True)
check("Import: derselbe Import noch einmal ändert nichts", (nochmal["neu"], nochmal["geaendert"], nochmal["gleich"]) == (0, 0, 3), str(nochmal))
vorher = len(e.zustand()["knoten"])
dop = e.importieren("Equipment", "Name;ID\nA;EQ-700\nB;EQ-700\n", ["name", "feld:EquipmentID"], probe=False)
check("Import: doppelte ID im Block -> abgelehnt mit Zeilen, nichts angelegt (auch CSV mit Semikolon)",
      not dop["uebernommen"] and "Zeilen 2, 3" in dop["fehler"][0]["text"] and len(e.zustand()["knoten"]) == vorher, str(dop["fehler"]))
schlecht = e.importieren("Equipment", "Name\tID\tKat\nGut\tEQ-701\tKat 1\nSchlecht\tEQ-702\tKat 9\n",
                         ["name", "feld:EquipmentID", "feld:EquipmentCategory"], probe=False)
check("Import: ein ungültiger Wert -> Fehler mit Zeilennummer, auch die gute Zeile wird nicht übernommen",
      not schlecht["uebernommen"] and schlecht["fehler"][0]["zeile"] == 3 and len(e.zustand()["knoten"]) == vorher, str(schlecht["fehler"]))
try:
    e.importieren("Equipment", "Name\tKat\nX\tKat 1\n", ["name", "feld:EquipmentCategory"])
    check("Import: ohne ID-Spalte geht es nicht", False)
except Fehler as f:
    check("Import: ohne ID-Spalte geht es nicht", "EquipmentID" in str(f), str(f))

sa, _ = e.knoten_anlegen("Prozessschritt", "Testschritt A")
sb, _ = e.knoten_anlegen("Prozessschritt", "Testschritt B")
e.verknuepfe(sa, sb, "führt zu Nachfolger (0:n)", "ein", {"ProcessstepType": "Standard-Fertigungsschritt"})
check("Verknüpfen: bei einer Selbstbeziehung bleibt die verlangte Richtung (Vorgänger eintragen)",
      e.db.get_connected(sb, "out", target_collection="Prozessschritt") == [sa] and e.db.get_connected(sa, "out") == [])
e.schliessen()
shutil.rmtree(W7, ignore_errors=True)

shutil.rmtree(WURZEL, ignore_errors=True)
bestanden = sum(1 for _, ok, _ in results if ok)
print(f"\n{bestanden}/{len(results)} Checks bestanden")
sys.exit(0 if bestanden == len(results) else 1)
