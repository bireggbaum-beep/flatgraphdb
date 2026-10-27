# Graph-Editor

Objekte anlegen, verknüpfen und erkunden. Nur Standardbibliothek.

    python editor/editor.py            # http://127.0.0.1:8765/
    python editor/editor.py --bestand ~/meine_daten --port 9000

Der Bestand liegt in `editor/bestand/` (nicht im Git). Dunkles Design.

**Datenmodell:** `editor/modell.json` (JSON-Schema mit `*Node`-Typen und
`Edge_*`-Definitionen) gibt Typen, Felder, Auswahlwerte, deutsche Bezeichnungen
(`title`) und **die erlaubten Beziehungen zwischen den Typen** vor. Referenz ist
`editor/datenmodell.pdf`; `tests/test_editor.py` vergleicht die 22 Typpaare und
die Attribute wörtlich damit. Fehlende Pflichtfelder erscheinen als Lücke (rotes
„!“, Filter „Datenqualität“), erzwungen werden sie beim Anlegen nicht.
Auswahlfelder nehmen nur Werte aus dem Modell; „Einheit“ eines Parameters ist
Auswahl **oder** freier Text.

**Streng:** Eine Verknüpfung gibt es nur, wenn das Modell das Typpaar kennt.
Zwischen zwei Typen mit genau einer Beziehung ergänzt der Editor Art **und**
Richtung selbst; bei mehreren fragt er, bei keiner lehnt er mit einer Erklärung
ab („Zwischen Plant und Material gibt es im Modell keine Beziehung“). Pflicht-
attribute einer Beziehung (zum Beispiel der Typ des Prozessschritts) werden
beim Verknüpfen verlangt. Wer ohne diese Prüfung arbeiten will:
`--locker`; ohne Modell: `--modell keins`.

## Regelwerk und Beispiel

- Das Regelwerk **ist das Modell**: jede `Edge_*`-Definition ist ein Paar
  „Typ –Beziehung→ Typ“ (Equipment *ist Teil von* Plant, Parameter *gehört zu*
  Prozessschritt, …). Ein Equipment liegt demnach nur über seine Plant in einem
  Raum, nicht direkt. Es steuert die strenge Prüfung, die Wege „nach Regelwerk“
  und die Warnung ⚠ am Verknüpfungs-Chip bei Altdaten (Filter „Gegen das
  Regelwerk“, Menü ⋯ → Regelwerk anzeigen).
- `editor/regeln.json` ist **nur Einstellung**, keine Paare: `nicht_durchlaufen`
  (Dokument, Medium, Software) sind Ziele, aber keine Zwischenschritte;
  `nur_einzeln` („führt zu Nachfolger“) wird nicht mit weiteren Schritten
  verkettet.
- `python editor/beispiel.py --bestand editor/bestand_beispiel` baut einen
  Beispielbestand: Fertigung eines Teststreifens über Chemlabor, Siebdruck
  (Ag/Carbon), Dispensieren, Trocknungen, Montage auf anderer Plant und QC
  (130 Objekte, 221 Verknüpfungen, mit absichtlichen Lücken: Equipment ohne
  Dokument, Parameter ohne Equipment, Mikroskop ohne Kategorie).

## Arbeitsfeld aus Spalten

Mehrere **Spalten** nebeneinander. Jede ist eine eigene Abfrage mit eigener
Ansicht (**Tabelle, Liste, Baum, Graph**) und eigenen **Facetten**
(Knopf „Filter“: Typ, Eigenschaften, Datenqualität, „verknüpft mit Typ“
mit/ohne, und jede Beziehung einzeln mit ihren Werten und „keine“). Zahlen in
den Facetten rechnen die anderen Filter der Spalte mit.

- **Zusammenhang** (Facette, in jeder Spalte): der allgemeine Weg. Standard ist
  **„nach Regelwerk“**: bis zu n Typ-Schritte, gegangen werden nur Wege, die das
  Regelwerk vorsieht (Prozess ← Prozessschritt → Material). Mit **„frei“** wählst
  du Richtung, Beziehungsarten und Tiefe selbst. Darunter stehen die
  erreichbaren Objekte **nach Typ** mit Zahlen und „keine“. Innerhalb einer Typ-Liste gilt ODER, zwischen den Listen und
  zwischen mehreren Zusammenhängen UND. Voreinstellungen: „↑ gehört zu“
  (Zugehörigkeitsarten von unten nach oben — „Equipments in Raum X“),
  „↓ enthält“, „beliebig“. So lässt sich jede Kombination bilden, etwa
  „Prozessschritt + Equipment, die zu Prozess Y gehören“ (Typ-Facette beide
  anhaken, Zusammenhang Prozess Y) oder „Equipment in Raum X und Plant Y“.
  **Die Reichweite berechnet flatgraph** (`traverse` mit Kantenfilter) über
  `/api/reichweite` (frei: `traverse` mit Kantenfilter; nach Regelwerk: ein
  `get_connected` je Schritt mit Art, Richtung und Zieltyp); die Oberfläche
  merkt sich das Ergebnis je Wegbeschreibung.
- **Menge aus anderer Spalte** (Facette): „in“ oder „nicht in“ den Treffern einer
  früheren Spalte — Schnitt und Differenz.
- **Eigene Frage** (Fragen-Spalte oder Strg K): ein Satz wird zu Filtern einer
  freien Spalte (je Bedingung ein Zusammenhang nach Regelwerk, 2 Typ-Schritte).
  Danach mit den Facetten änderbar.
- **Weiter →** legt eine Folgespalte an: Ziel-Typ und Tiefe; im Dialog stehen die
  Wege, die gegangen werden. Frei: Richtung, Beziehungsarten. Quelle ist entweder die
  **Auswahl** in der Spalte (Klick auf eine Zeile füllt die nächste Spalte live)
  oder **alle Treffer**. Mehrere Folgespalten aus derselben Quelle sind möglich.
  Die Spalte „über“ zeigt, über welches Objekt der Vorgänger ein Ergebnis
  erreicht wurde. ⚙ ändert die Quelle nachträglich.
- **Fragen** (oben): Vorlagen für typische Auswertungen als fertige Spaltenkette, zum Beispiel
  „CPPs eines Prozesses – und wer sie kontrolliert“ oder „Materialien in einem
  Prozess“. Objekt wählen, fertig.
- **Anlegen im Kontext** (＋ in der Spalte): Filter, die genau einen Wert
  festlegen, gelten für das neue Objekt (Typ, Eigenschaften, Verknüpfung).
- **Ansichten** speichern das ganze Arbeitsfeld; **CSV** exportiert die Spalte
  (Semikolon, für Excel); **Spalten wählen** blendet Eigenschaften und
  Verknüpfungen als Tabellenspalten ein.
- **Gruppe** (je Spalte): nach Typ, Eigenschaft oder Verknüpfung; zuklappbare
  Gruppenköpfe mit Anzahl (Tabelle und Liste). Bei einer Verknüpfung steht ein
  Objekt in jeder seiner Gruppen, ohne Verknüpfung in „(keine)“.
- **Diagramm**: Balken je Gruppe (Standard: Typ); Klick auf einen Balken filtert.
- **Fluss**: Prozessschritte entlang „führt zu Nachfolger“ als Ablaufdiagramm
  (Schichten von links nach rechts; Gate/IPC, Wartezeit und Schleife aus dem
  Kanten-Attribut; Punkte zeigen Parameter, Material, Equipment, Dokument,
  Software am Schritt). Erscheint, sobald solche Beziehungen in der Spalte liegen.
- **Filter über verknüpfte Objekte**: in der Facette einer Beziehung stehen
  darunter die Auswahlfelder der verknüpften Objekte, zum Beispiel
  „steht in → Raum: RoomClass = ISO 7“ (Facette aufklappen).
- **Massenbearbeitung** (Spaltenmenü ⋯): für alle Treffer ein Feld setzen,
  alle mit einem Objekt verknüpfen oder alle löschen. Alles oder nichts;
  ein Feld, das das Modell einem anderen Typ zuordnet, wird dort übersprungen.
- Baum: Hierarchie innerhalb der Treffer einer Spalte, nach einer Beziehungsart;
  ziehen = verschieben, Strg/Alt = zusätzlich einhängen.

## Inspektor (rechts)

Titel zum Umbenennen anklicken; Eigenschaften nach Modell, darunter „+ eigenes Feld“
(beliebiger Name und Wert, ohne Dropdown und Pflichtprüfung; bereits benutzte
Namen werden vorgeschlagen; auch als Tabellenspalte wählbar); **Notiz** mit
`[[Name]]` (Vorschläge beim Tippen von `[[`; Umbenennen zieht die Verweise
nach); **Wo** (oben): die Zugehörigkeitskette (Plant, Raum, Prozess, Prozessschritt);
**Umfeld**: je Typ, was bis 1–3 Typ-Schritte nach Regelwerk erreichbar ist; Klick öffnet eine Spalte;
Abschnitte lassen sich einklappen; Verknüpfungen nach Art und Richtung gruppiert, die Art wird aus dem
vorgeschlagen, was zwischen den beiden Typen schon benutzt wurde; „Erwähnt in“;
Nachbarschaftsgraph. **Erkunden →** startet eine Spalte von diesem Objekt aus.

## Tasten

Strg K oder `/`: suchen, öffnen — oder `Typ: Name` + Enter zum Anlegen ·
↑ ↓ (oder j k): Zeile in der aktiven Spalte · Esc: Inspektor schließen.
„Liste einfügen“ (Menü ⋯): eine Zeile je Objekt, Einrücken = hängt darunter.

Test: `python tests/test_editor.py`
