# Graph-Editor

Objekte anlegen, verknüpfen und erkunden. Nur Standardbibliothek.

    python editor/editor.py            # http://127.0.0.1:8765/
    python editor/editor.py --bestand ~/meine_daten --port 9000

Der Bestand liegt in `editor/bestand/` (nicht im Git). Dunkles Design.

**Datenmodell:** `editor/modell.json` (JSON-Schema mit `*Node`-Typen und
`GraphEdge`) gibt Typen, Felder, Auswahlwerte und Beziehungsarten vor. Es
erzwingt nichts beim Erfassen: ein Objekt braucht nur Typ und Name, fehlende
Pflichtfelder erscheinen als Lücke (rotes „!“, Filter „Datenqualität“).
Auswahlfelder nehmen nur Werte aus dem Modell; unbekannte Typen und
Beziehungsarten werden abgelehnt. Ohne Modell: `--modell keins`.

## Arbeitsfeld aus Spalten

Mehrere **Spalten** nebeneinander. Jede ist eine eigene Abfrage mit eigener
Ansicht (**Tabelle, Liste, Baum, Graph**) und eigenen **Facetten**
(Knopf „Filter“: Typ, Eigenschaften, Datenqualität, „verknüpft mit Typ“
mit/ohne, und jede Beziehung einzeln mit ihren Werten und „keine“). Zahlen in
den Facetten rechnen die anderen Filter der Spalte mit.

- **Weiter →** legt eine Folgespalte an: Ziel-Typ, Tiefe (1–5 Beziehungsschritte),
  Richtung, nur über bestimmte Beziehungsarten. Quelle ist entweder die
  **Auswahl** in der Spalte (Klick auf eine Zeile füllt die nächste Spalte live)
  oder **alle Treffer**. Mehrere Folgespalten aus derselben Quelle sind möglich.
  Die Spalte „über“ zeigt, über welches Objekt der Vorgänger ein Ergebnis
  erreicht wurde. ⚙ ändert die Quelle nachträglich.
- **Fragen** (oben): typische Auswertungen als fertige Spaltenkette, zum Beispiel
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
nach); **Umfeld**: je Typ, was bis 3 Beziehungsschritte erreichbar ist (gleichartige
Geschwister blockieren den Weg, so hängt der Temperaturfühler nicht an der Nachbar-CPP);
Klick öffnet eine Spalte; Verknüpfungen nach Art und Richtung gruppiert, die Art wird aus dem
vorgeschlagen, was zwischen den beiden Typen schon benutzt wurde; „Erwähnt in“;
Nachbarschaftsgraph. **Erkunden →** startet eine Spalte von diesem Objekt aus.

## Tasten

Strg K oder `/`: suchen, öffnen — oder `Typ: Name` + Enter zum Anlegen ·
↑ ↓ (oder j k): Zeile in der aktiven Spalte · Esc: Inspektor schließen.
„Liste einfügen“ (Menü ⋯): eine Zeile je Objekt, Einrücken = hängt darunter.

Test: `python tests/test_editor.py`
