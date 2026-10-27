# Graph-Editor

Objekte erfassen, per Ziehen verknüpfen, als Baum lesen. Nur Standardbibliothek.

    python editor/editor.py            # http://127.0.0.1:8765/
    python editor/editor.py --bestand ~/meine_daten --port 9000

Der Bestand liegt in `editor/bestand/` (nicht im Git). Typen und
Beziehungsarten entstehen beim ersten Gebrauch; nichts muss vorab definiert sein.

- **Erfassen:** Typ + Name, Enter. Mehrere Zeilen einfügen: Einrücken heisst
  „hängt unter der Zeile darüber“ (mit der gewählten Beziehungsart).
  `Raum: Reinraum C` setzt den Typ für diese Zeile.
- **Verknüpfen:** Objekt aus der Liste oder dem Baum auf ein anderes ziehen.
  Im Baum ziehen verschiebt, mit Strg/Alt hängt es zusätzlich ein.
  Auf die Detailansicht ziehen: Beziehung zum dort gewählten Objekt.
- **Weitere Beziehungsarten** (Raum beherbergt Equipment, ...): rechts im
  Detail Art + Objekt eintippen, Enter. Unbekannte Namen werden angelegt.
- **Löschen** geht in den Papierkorb, „Rückgängig“ holt es zurück.
- Test: `python tests/test_editor.py`
