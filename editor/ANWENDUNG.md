# Anwendung: Fertigungsgraph erfassen und erkunden

Kurzanleitung mit typischen Fragen. Alle Beispiele laufen auf dem Beispielbestand:

    python editor/beispiel.py --bestand editor/bestand_beispiel
    python editor/editor.py --bestand editor/bestand_beispiel   # http://127.0.0.1:8765/

Funktionen im Einzelnen: `README.md`. Erlaubte Beziehungen: `modell.json` (22 Typpaare).

## Grundidee

Alles ist ein **Objekt** (Plant, Raum, Equipment, Software, Medium, Prozess,
Prozessschritt, Material, Dokument, Parameter) mit **Verknüpfungen**. Du arbeitest in
**Spalten**: jede Spalte ist eine Abfrage mit Filtern (Facetten). Eine Folgespalte
(„Weiter →“) geht von den Treffern einer Spalte zu verbundenen Objekten.

## Erfassen

1. **Anlegen:** Strg K, `Equipment: Trockner TR-02`, Enter. Oder ＋ in einer Spalte –
   Filter, die genau einen Wert festlegen, gelten dann schon für das neue Objekt.
2. **Verknüpfen:** im Inspektor „Typ: Name“ eintippen, z. B. `Plant: Montage`.
   Art und Richtung ergänzt der Editor aus dem Modell; gibt es zwischen den Typen
   keine Beziehung, sagt er das.
3. **Vieles auf einmal:** Menü ⋯ → „Liste einfügen“ (eine Zeile je Objekt, Einrücken =
   hängt darunter) oder Massenbearbeitung für alle Treffer einer Spalte.
4. **Lücken** zeigt das rote „!“; Filter „Datenqualität“ listet sie.

## Erkunden

- **Typ-Facette:** welche Objektarten sind in der Spalte.
- **Zusammenhang:** „erreichbar von Objekt X“, standardmässig *nach Regelwerk*
  (nur Wege, die das Modell vorsieht). Mehrere Zusammenhänge verknüpfen sich mit UND.
- **Weiter →:** Folgespalte aus Auswahl oder allen Treffern.
- **Fragen** (oben, oder Strg K mit einem Satz): fertige Spaltenketten.
- Ansichten: Tabelle, Liste, Baum, Graph, Diagramm, Fluss (Prozessschritte entlang
  „führt zu Nachfolger“). Gruppieren pro Spalte.

## Use Cases

### 1. „Alle Equipments im Raum X von Plant Y“
Spalte, Typ = Equipment. Zusammenhang → Raum *Montageraum*, zweiter Zusammenhang →
Plant *Montage*. Equipments liegen nur über ihre Plant im Raum; der Weg ist
Equipment → Plant → Raum, das Regelwerk findet ihn selbst.
Oder als Satz in Strg K.

### 2. Prozessvalidierer: „CPPs im Prozess X – wer kontrolliert sie?“
Frage „CPPs eines Prozesses – und wer sie kontrolliert“, Prozess *Elektrodenfertigung*
wählen. Ergebnis: Parameter-Spalte, daneben Folgespalte mit den Equipments, die sie
überwachen/regeln. Klick auf einen Parameter füllt die Equipment-Spalte live.

### 3. „Wo ist dieser CPP, was hängt an ihm?“
Parameter (z. B. *Temperatur Trocknung Ag*) öffnen. Inspektor: **Wo** zeigt die Kette
Prozess → Schritt, **Umfeld** je Typ, was bis 3 Schritte entfernt liegt (Equipment,
Material, Dokument), **Verknüpfungen** die direkten Nachbarn. „Erkunden →“ macht daraus
eine Spalte.

### 4. „Welche Materialien gehen in Prozess Y?“
Frage „Materialien in einem Prozess“. Oder: Typ = Material, Zusammenhang → Prozess Y.
Für „in Plant Z verwendet“: zweiten Zusammenhang → Plant Z ergänzen.

### 5. Qualifizierer: „Equipment ohne Dokument“
Typ = Equipment; Facette „verknüpft mit Typ“ → Dokument → **ohne**. Im Beispiel
bleiben 10 Equipments übrig = Qualifizierungs-Lücken. Mit Menü ⋯ → CSV als Liste
exportieren, oder Gruppe nach Plant, um zu sehen, wo die meisten fehlen.

### 6. Prozessablauf ansehen
Typ = Prozessschritt, Zusammenhang → Prozess *Siebdruck*, Ansicht **Fluss**:
Schritte von links nach rechts, Gates/IPC, Wartezeiten und Schleifen sichtbar, Punkte
am Schritt zeigen Parameter, Material, Equipment.

### 7. „Was ist betroffen, wenn Equipment E ausfällt?“
Equipment öffnen → Erkunden →; Folgespalte Typ Prozessschritt, dann Folgespalte
Prozess. Zeigt die Schritte, die E nutzt, und die Prozesse darüber. Für Parameter und
Materialien eine weitere Folgespalte aus derselben Quelle anlegen.

### 8. Überblick und Qualität
Gruppe nach Typ, Ansicht Diagramm (Anzahl je Typ; Klick auf Balken filtert).
Filter „Gegen das Regelwerk“ zeigt Altdaten mit Verknüpfungen, die das Modell nicht
kennt (⚠). Die Arbeitsfeld-Aufstellung lässt sich als **Ansicht** speichern und
wieder aufrufen.

## Tipps

- Mengenlogik: Facette „Menge aus anderer Spalte“ – in / nicht in den Treffern einer
  früheren Spalte (Schnitt, Differenz).
- Freie Wege (statt Regelwerk) im Zusammenhang auf „frei“ stellen, wenn du Richtung,
  Art und Tiefe selbst bestimmen willst.
- Notizen im Inspektor verlinken mit `[[Name]]`; Umbenennen zieht die Verweise nach.
