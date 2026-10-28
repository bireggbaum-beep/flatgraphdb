# Anwendung: Fertigungsatlas

Objekte der Fertigung (Plant, Raum, Equipment, Prozess, Prozessschritt,
Parameter, Material, Dokument, Software, Medium) anlegen, zuordnen, pflegen und
als Tabelle nachschlagen. Alle Beispiele laufen auf dem Beispielbestand:

    python editor/beispiel.py --bestand editor/bestand_beispiel
    python editor/editor.py --bestand editor/bestand_beispiel   # http://127.0.0.1:8765/

## Das Prinzip: Primär → Sekundär → Tabelle

```
 Seitenleiste            Typ-Leiste über der Tabelle        rechts
 PRIMÄR: worum geht es   SEKUNDÄR: was soll ich sehen       DETAIL
 ─────────────────────   ────────────────────────────────   ─────────
 Ort      Raum ▸ Plant   Equipment 5 · Prozessschritt 7 …   Inspektor
 Prozesse Prozess ▸ Schritt
 oder ein beliebiges Objekt („Als Bereich →“) oder „Alle Objekte“
```

1. **Primär** wählen: links einen Raum, eine Plant, einen Prozess, einen Schritt –
   oder „Alle Objekte“. Jedes andere Objekt wird Primär über **Als Bereich →** im
   Inspektor.
2. **Sekundär** wählen: über der Tabelle steht je Typ, wie viele Objekte zum
   Bereich gehören. Klick = diese Objekte als Zeilen.
3. Die **Tabelle** zeigt eigene Eigenschaften und **abgeleitete Spalten** (Plant,
   Raum, Prozess, Dokumente …). Rechts der **Inspektor** des gewählten Objekts.

Was „gehört zu“ heißt, ergibt sich aus dem Datenmodell, niemand muss es
pflegen: es gilt der kürzeste Weg über die erlaubten Beziehungen. Equipments
eines Raums sind die, die dort stehen (der Raum hängt wie in SAP am Equipment);
die Räume einer Plant ergeben sich über ihre Equipments; am Prozess beteiligt
ist ein Equipment, das einen seiner Schritte ausführt.

## Typische Fragen

| Frage | Primär | Sekundär | in der Tabelle |
|---|---|---|---|
| Liste aller Equipments der Linie 1 | Ort › Siebdruckraum › *Sensorfertigung Linie 1* | Equipment | – |
| Welche Equipments sind am Prozess beteiligt? | Prozesse › *Elektrodenfertigung* | Equipment | – |
| Welche Schritte hat der Prozess, in welcher Reihenfolge? | Prozesse › *Elektrodenfertigung* (aufklappen) | Prozessschritt | Reihenfolge steht links im Baum |
| CPPs im Prozess und wer sie regelt | Prozesse › *Elektrodenfertigung* | Parameter | Facette Klasse = CPP, Spalte Equipment |
| Equipments im Montageraum | Ort › *Montageraum* | Equipment | – |
| Materialien im Prozess | Prozesse › *Materialvorbereitung Reagenz* | Material | Spalten Input / Output am Schritt |
| Equipment ohne Dokument | Alle Objekte | Equipment | Gruppieren nach Dokument → „(keine)“ |
| Was hängt an diesem CPP? | – | – | Zeile anklicken: Inspektor zeigt Zusammenhang und Beziehungen |
| Was betrifft ein Ausfall von Gerät X? | X öffnen → **Als Bereich →** | Prozessschritt oder Prozess | – |

## Pflegen

- **Anlegen:** ＋ Neu ▾ oder „＋ Raum anlegen“ / „＋ Prozess anlegen“ in der
  Seitenleiste. Ist ein Bereich gewählt, schlägt der Dialog die Verknüpfung
  gleich vor (neues Equipment in Plant X: „ist Teil von“).
- **Eigenschaften:** im Inspektor auf den Wert klicken. Pflichtfelder tragen *
  und zeigen „fehlt“, auch in der Tabelle.
- **Zuordnen:** im Inspektor unter *Beziehungen* stehen alle Beziehungen, die
  das Modell für diesen Typ kennt, auch leere. „＋ Plant“ → Namen tippen →
  Enter. Gibt es den Namen noch nicht, wird das Objekt angelegt. Pflichtattribute
  einer Beziehung (Typ eines Schrittübergangs) werden dabei abgefragt. × löst eine
  Beziehung (mit Rückgängig).
- **Mehrere auf einmal:** Häkchen setzen (oder Umschalt-Klick), dann *Zuordnen
  zu …*, *Feld setzen …* oder *Löschen* (mit Rückgängig).
- **IDs** (aus SAP) sind je Typ eindeutig. Eine schon vergebene ID wird
  abgelehnt, mit dem Objekt, das sie schon hat.

## Import aus SAP, Sheets, Excel

1. In Sheets oder Excel die Zeilen **mit Kopfzeile** markieren und kopieren.
2. Im Atlas **Strg V** (ausserhalb eines Eingabefelds) oder ＋ Neu ▾ → *Tabelle
   einfügen*. Typ wählen (Equipment, Raum, …).
3. Jede Spalte zuordnen: **ID** (Schlüssel, Pflicht), Name, ein Feld, oder eine
   **Beziehung** („Raum“ → *steht in Raum*, „Funktionsplatz“ → *ist Teil von
   Plant*). Spalten, die wie ein Feld oder ein Typ heissen, sind vorgeschlagen;
   eine einmal gewählte Zuordnung merkt sich der Atlas je Spaltenname.
4. **Prüfen** zeigt, was passiert: neu, geändert (mit den Feldern), unverändert,
   welche Räume oder Plants mit angelegt werden, und Fehler mit Zeilennummer.
5. **Übernehmen** – alles oder nichts. Solange es Fehler gibt (doppelte ID in der
   Tabelle, Wert, den das Modell nicht kennt), wird nichts übernommen.

Regeln: Gibt es die ID schon, wird das Objekt geändert, sonst angelegt. Eine
**leere Zelle ändert nichts**. Eine Beziehungsspalte **ersetzt** die bisherige
Zuordnung (ein Gerät, das in SAP umgezogen ist, steht danach nur im neuen
Raum). Ein Ziel wird über seine ID oder seinen Namen gefunden; mehrere Ziele in
einer Zelle mit „;“ trennen.

## Tabellenlayout (wie Layout-Varianten in SAP)

Pro Typ gibt es ein **Standard**-Layout aus dem Modell: alle Eigenschaften,
die direkten Beziehungen und der Ort- und Prozess-Kontext. Über **⋯ → Spalten
wählen** Spalten ein- und ausblenden und ordnen, dazu **Sortieren** und
**Gruppieren**. **Als neue Variante speichern** legt die Einstellung beim
Bestand ab (`layouts.json`), damit alle Nutzer sie sehen. Eine Variante kann
**Vorgabe** für ihren Typ werden. Ein „*“ im Layout-Feld heißt: geändert, nicht
gespeichert. **CSV** exportiert die Tabelle, wie sie zu sehen ist.

## Tasten

Strg K: Suche (filtert die Tabelle und findet Objekte im ganzen Bestand) ·
↑ ↓ oder j k: nächste Zeile · Esc: Auswahl aufheben, dann Inspektor leeren.

Der frühere Editor mit Spalten, Fragen und Fluss liegt unter `/alt`.
