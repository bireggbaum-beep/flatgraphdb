# Graph-Editor

Objekte erfassen, per Ziehen verknüpfen, als Baum lesen. Nur Standardbibliothek.

    python editor/editor.py            # http://127.0.0.1:8765/
    python editor/editor.py --bestand ~/meine_daten --port 9000

Der Bestand liegt in `editor/bestand/` (nicht im Git).

**Datenmodell:** `editor/modell.json` (JSON-Schema mit `*Node`-Typen und
`GraphEdge`) gibt vor, welche Typen, Felder, Auswahlwerte und Beziehungsarten
es gibt. Es erzwingt nichts beim Erfassen: ein Objekt braucht nur Typ und Name,
fehlende Pflichtfelder erscheinen als rotes „!“ in der Liste (Filter
„unvollständig“) und rot umrandet im Detail. Auswahlfelder nehmen nur Werte
aus dem Modell, unbekannte Typen und Beziehungsarten werden abgelehnt.
Ohne Modell: `--modell keins`, dann entstehen Typen und Arten beim Gebrauch.

- **Erfassen:** Typ + Name, Enter. Mehrere Zeilen einfügen: Einrücken heisst
  „hängt unter der Zeile darüber“ (mit der gewählten Beziehungsart).
  `Raum: Reinraum C` setzt den Typ für diese Zeile.
- **Verknüpfen:** Objekt aus der Liste oder dem Baum auf ein anderes ziehen.
  Im Baum ziehen verschiebt, mit Strg/Alt hängt es zusätzlich ein.
  Auf die Detailansicht ziehen: Beziehung zum dort gewählten Objekt.
- **Baumrichtung:** „A → B: B hängt unter A“ oder umgekehrt. Bei „ist Teil
  von“, „gehört zu“, „steht in“ ist es von selbst „umgekehrt“, änderbar.
- **Andere Beziehungen** (Equipment steht in Raum, ...): rechts im Detail
  Art + Objekt eintippen, Enter. Unbekannte Namen werden angelegt.
  „führt zu Nachfolger“ hat das Attribut Prozessschritt-Typ, direkt am Eintrag.
- **Löschen** geht in den Papierkorb, „Rückgängig“ holt es zurück.
- Test: `python tests/test_editor.py`
