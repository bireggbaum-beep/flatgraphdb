# Graph-Editor

Objekte anlegen, verknüpfen und erkunden. Nur Standardbibliothek.

    python editor/editor.py            # http://127.0.0.1:8765/
    python editor/editor.py --bestand ~/meine_daten --port 9000

Der Bestand liegt in `editor/bestand/` (nicht im Git).

**Datenmodell:** `editor/modell.json` (JSON-Schema mit `*Node`-Typen und
`GraphEdge`) gibt Typen, Felder, Auswahlwerte und Beziehungsarten vor. Es
erzwingt nichts beim Erfassen: ein Objekt braucht nur Typ und Name, fehlende
Pflichtfelder erscheinen als Lücke (rotes „!“, Übersicht, Filter). Auswahlfelder
nehmen nur Werte aus dem Modell; unbekannte Typen und Beziehungsarten werden
abgelehnt. Ohne Modell: `--modell keins`.

## Bedienung

- **Strg K** (oder `/`): suchen, öffnen — oder `Typ: Name` + Enter zum Anlegen.
  Links bei jedem Typ ein ＋.
- **Objektseite:** Titel zum Umbenennen anklicken; Eigenschaften nach Modell;
  **Notiz** mit `[[Name]]` (beim Tippen von `[[` erscheinen Vorschläge);
  Verknüpfungen nach Art und Richtung gruppiert; „Erwähnt in“ zeigt, wo das
  Objekt in Notizen vorkommt; Nachbarschaftsgraph (Tiefe 1 oder 2, Klick = hin).
- **Verknüpfen:** im Feld „Verknüpfen mit …“ tippen, Enter. Die Art wird aus
  dem vorgeschlagen, was zwischen diesen beiden Typen schon benutzt wurde.
  `Typ: Name` legt das Ziel neu an. „führt zu Nachfolger“ hat sein Attribut
  direkt am Eintrag.
- **Typseite:** Tabelle mit Sortierung, Filter, „nur unvollständig“, Neu-Feld.
- **Baum:** Hierarchie einer Beziehungsart; ziehen = verschieben, Strg/Alt =
  zusätzlich einhängen. Bei „ist Teil von“, „gehört zu“, „steht in“ hängt A
  unter B (änderbar).
- **Liste einfügen** (links unten): eine Zeile je Objekt, Einrücken = hängt
  darunter, `Typ: Name` setzt den Typ der Zeile.
- Löschen geht in den Papierkorb, „Rückgängig“ holt es zurück.
- Umbenennen zieht `[[Verweise]]` in allen Notizen nach.

Test: `python tests/test_editor.py`
