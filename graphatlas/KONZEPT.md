# Graphatlas — Konzept für ein Modellierungswerkzeug auf flatgraph

*Arbeitstitel. Die Bezeichnung "Graphatlas" ist ein Vorschlag, kein feststehender Name — ein Atlas ist eine Sammlung von Karten, die zusammen ein Gesamtbild ergeben, das keine einzelne Karte für sich zeigen kann. Genau das ist der Zweck dieses Werkzeugs.*

---

## 0. Ausgangslage und Zweck

Der heutige Zustand: Herstellprozesse existieren als einzelne, statische Flowcharts in Lucid. Jedes Chart zeigt einen Prozess für sich. Niemand hat ein Bild, das mehrere Prozesse gleichzeitig durchquert — "welche Produkte hängen an diesem Equipment", "wo überall wird dieses Material verwendet", "alle kritischen Zeitfenster über alle Prozesse hinweg" sind Fragen, die ein Flowchart strukturell nicht beantworten kann, egal wie sorgfältig es gezeichnet ist.

Graphatlas ist das Werkzeug, mit dem diese Prozesse nicht mehr als Bilder, sondern als **eine durchquerbare Struktur** existieren. Es ist eine eigenständige Anwendung, die auf der flatgraph-Engine aufbaut — nicht Teil von pDMS, auch wenn beide dieselbe Speicherschicht nutzen. pDMS verwaltet Dokumente und hat sich bewusst gegen typisierte Kanten entschieden (ein einziger, unbenannter Verknüpfungstyp reicht dort). Graphatlas ist der Gegenentwurf für eine andere Domäne: Hier *ist* der Beziehungstyp die zentrale Aussage, nicht ein Nebenprodukt der Verlinkung.

Der Kernnutzer ist eine einzelne Person, die komplexe, reale Strukturen (Herstellprozesse mit Produkten, Prozessschritten, Equipment, Plants, Material, Dokumenten, Zeitrandbedingungen) modellieren will — mit Daten, die zu großen Teilen aus bestehenden Systemen (SAP-Exporte, Excel-Listen) stammen, nicht von Grund auf neu erfasst werden.

---

## 1. Leitprinzipien

Diese fünf Sätze entscheiden jede spätere Detailfrage. Wenn eine Funktion einem davon widerspricht, ist die Funktion falsch, nicht das Prinzip.

**Kantentypen sind erstklassige, konfigurierbare Objekte.** Eine Kante heißt nie einfach "Verbindung". Sie hat einen Namen, eine Bedeutung, erlaubte Endpunkte und eigene Eigenschaften. Das Anlegen und Pflegen von Kantentypen ist eine genauso zentrale Tätigkeit wie das Anlegen von Knoten — nicht ein einmaliges Setup, das man vergisst, sobald es steht.

**Das Modell wächst inkrementell, ohne Migrationsschmerz.** Ein neuer Knotentyp, ein neuer Kantentyp, ein neues Feld — jederzeit möglich, ohne bestehende Daten anzufassen oder umzubauen. Der heutige Abend war der Beweis dafür: Dutzende neue Konzepte kamen dazu, ohne dass ein einziges Mal etwas Bestehendes verworfen werden musste.

**Rohdaten bleiben roh und sichtbar.** JSON ist keine interne Implementierungsdetail, das die App verbirgt, sondern eine gleichberechtigte Ansicht. Was aus SAP kommt, soll man importieren, korrigieren, exportieren können, ohne die App als Blackbox zu erleben.

**Import ist ein Kernvorgang, kein Sonderfall.** Ein Knotentyp mit 80 Equipment-Einträgen aus SAP zu befüllen ist ein Alltagsvorgang, keine Ausnahme, die man einmal am Anfang erledigt und danach vergisst.

**Dichte, modallose, tastaturfreundliche Oberfläche.** Kein Dialog unterbricht den Blick auf die Struktur. Bearbeitung passiert inline, an Ort und Stelle, mit Autosave. Das ist dieselbe Grundhaltung, die schon bei anderen eigenen Werkzeugen gilt: Ein Werkzeug, das man benutzt, um zu denken, darf einem nicht ständig im Weg stehen.

---

## 2. Zwei Ebenen, zwei Tätigkeiten

Das Modell hat zwei klar getrennte Ebenen, und die App bildet diese Trennung auch in der Oberfläche ab, nicht nur im Datenmodell:

- **Typ-Ebene (Schema):** Was für Knotenarten und Kantenarten gibt es überhaupt, welche Felder tragen sie, welche Endpunkte sind erlaubt. Das ist die Ebene, auf der man das Vokabular des Modells entwirft.
- **Instanz-Ebene (Daten):** Die konkreten Knoten und Kanten, die diesem Vokabular folgen — Prozessschritt "UV-CURING-1", Equipment "EQ-05", die konkrete `laeuft_auf`-Kante zwischen beiden.

Das ist bewusst getrennt, weil es zwei unterschiedliche Tätigkeiten mit unterschiedlichem Rhythmus sind. Das Schema entwerfen passiert selten und nachdenklich (ist "verwendet_medium" eine direkte Kante oder eine Ableitung?). Instanzen anlegen passiert oft und soll schnell gehen (80 Equipment-Zeilen importieren, eine Kante ziehen). Eine Oberfläche, die beides vermischt, macht die seltene Tätigkeit zu geschwätzig und die häufige zu langsam.

---

## 3. Typ-Editor: Knotentypen

Eine flache Liste aller Knotentypen — `produkt`, `prozessschritt`, `material`, `equipment`, `plant`, `raum`, `gebaeude`, `medium`, `lager`, `dokument`, `software`, `infrastruktursystem` und was sonst noch entsteht. Kein Ordnerbaum, keine Kategorien, die selbst wieder gepflegt werden müssten — genau die Flachheit, die sich auch im Datenformat wiederfindet.

Pro Knotentyp konfigurierbar, alles inline in derselben Ansicht:

- **Name** (technischer Bezeichner, z. B. `equipment`)
- **Feld-Schema**: Feldname, Datentyp (Text, Zahl, Wahrheitswert, Liste), optional eine feste Werteliste (Enum, wie `dokumenttyp` mit `sop`, `pq`, `batch_record` …), optional Pflichtfeld
- **Darstellungshinweis** für die spätere Graph-Ansicht: ein Kürzel oder Icon, damit ein Knotentyp auf einen Blick vom anderen unterscheidbar ist, ohne das Label lesen zu müssen

Ein Feld nachträglich hinzuzufügen darf niemals bestehende Instanzen ungültig machen — es ist für sie einfach leer, bis jemand es nachträgt. Das spiegelt genau das Verhalten, das man von einem SAP-Export erwartet: Neue Spalte, alte Zeilen bleiben gültig.

Layout-Skizze der Ansicht:

    [ Knotentypen ]                                    [+ Neuer Typ]
    ─────────────────────────────────────────────────────────────
    equipment          (12 Felder)    243 Instanzen    [bearbeiten]
    plant              ( 4 Felder)     18 Instanzen    [bearbeiten]
    material           ( 6 Felder)    891 Instanzen    [bearbeiten]
    prozessschritt      ...
    dokument            ...

Ein Klick auf "bearbeiten" öffnet nicht ein Modal, sondern erweitert die Zeile selbst zu einem Feld-Editor darunter — dieselbe Fläche, kein Fensterwechsel.

---

## 4. Typ-Editor: Kantentypen — das eigentliche Kernstück

Das ist die Ansicht, um die sich alles andere organisiert, weil hier die Bedeutungsarbeit passiert, die ein generisches "Verbinden-Werkzeug" nie leisten könnte.

### 4.1 Was ein Kantentyp trägt

- **Technischer Name**: `laeuft_auf`, `folgt_auf`, `zeitfenster_zu` — kurz, sprechend, in der Terminologie des Modells.
- **Lesbare Bezeichnung** für die Vorwärtsrichtung und optional eine **Umkehrbezeichnung** für die Rückwärtsrichtung. `steht_in` vorwärts gelesen ("Equipment steht in Raum"), `beherbergt` rückwärts ("Raum beherbergt Equipment"). Das ist keine Spielerei — es entscheidet, ob man beim Navigieren gegen die Pfeilrichtung noch versteht, was man gerade sieht, ohne im Kopf umzudrehen.
- **Erlaubte Quelle-Ziel-Paare**, als Liste, nicht als einzelnes Paar. `laeuft_auf` erlaubt sowohl `prozessschritt → equipment` als auch `prozessschritt → plant`, weil beide Bedeutungen real vorkommen (einzelnes Gerät versus Linienkalibrierung als Ganzes).
- **Metadatenfelder-Schema für die Kante selbst** — dieselbe Feld-Typ-Logik wie bei Knotentypen, nur dass sie an der Kante hängt statt am Knoten. `zeitfenster_zu` braucht `min_minuten` und `max_minuten` als Pflichtzahlenfelder, `folgt_auf` ein optionales Feld `bedingung`.
- **Kaskadenlöschen** (ja/nein): Verschwindet das Zielobjekt mit, wenn die Quelle gelöscht wird? Für "Prozessschritt referenziert Dokument" klar nein, für andere Beziehungen kann es sinnvoll sein.
- **Zyklen erlaubt** (ja/nein): Diese Kantenart darf einen Kreis im Graphen bilden, oder muss sie zusammen mit allen Kanten desselben Typs immer einen zyklenfreien Pfad ergeben? `folgt_auf` verlangt Zyklenfreiheit, `reprocessing_zu` ist bewusst zyklisch. Das Flag ist der Grund, warum man für "Rücksprung im selben Prozess" überhaupt einen eigenen Kantentyp statt eines Attributs auf `folgt_auf` braucht — die Prüfung muss wissen, welche Kanten sie überhaupt betrachtet.
- **Kardinalität** (optional, bewusst nicht erzwungen): Wie viele ausgehende bzw. eingehende Kanten dieses Typs darf oder muss ein Knoten haben — "ein Subprozess hat genau ein `laeuft_auf`", "ein Prozess hat mindestens ein `hat_teilprozess`". Dieses Feld existiert im Editor, ist aber standardmäßig leer/unbeschränkt. Ob sich Kardinalitätsregeln in der Praxis lohnen, ist offen — der Editor hält den Platz dafür frei, ohne dass man beim ersten Anlegen eines Kantentyps schon eine Entscheidung treffen muss, die man noch nicht treffen kann.
- **Visuelle Kodierung** für die Graph-Darstellung: Farbe und/oder Linienstil (durchgezogen, gestrichelt, gepunktet). Bei zwanzig oder mehr Kantentypen gleichzeitig im Bild ist das kein Schönheitsdetail, sondern die einzige Art, wie man "Materialfluss" von "Ablaufreihenfolge" von "Zeitrandbedingung" unterscheidet, bevor man auch nur eine Beschriftung liest — vorbewusste, nicht erst gelesene Unterscheidung.

### 4.2 Duplizieren als Werkzeug

Ein neuer Kantentyp entsteht selten aus dem Nichts, meistens als Variante eines bestehenden. `reprocessing_zu` ist strukturell `folgt_auf` mit umgedrehtem Zyklen-Flag und eigenem Namen. Der Editor bietet deshalb "Duplizieren" als Grundoperation an: bestehenden Kantentyp als Ausgangspunkt nehmen, Name und die abweichenden Eigenschaften ändern, fertig.

### 4.3 Layout-Skizze

    [ Kantentypen ]                                         [+ Neuer Typ]  [Duplizieren ▾]
    ──────────────────────────────────────────────────────────────────────────────────────
    ● folgt_auf          prozessschritt → prozessschritt     azyklisch    148 Kanten
    ● reprocessing_zu     prozessschritt → prozessschritt     zyklisch      9 Kanten
    ● laeuft_auf          prozessschritt → equipment, plant   —           312 Kanten
    ● zeitfenster_zu      prozessschritt → prozessschritt     —            27 Kanten
    ● referenziert        (5 Quelltypen) → dokument           —           204 Kanten
      ...

    > laeuft_auf                                                    [zuklappen]
      Bezeichnung:        "läuft auf"  /  Umkehrung: "wird genutzt von"
      Erlaubte Paare:     prozessschritt → equipment
                           prozessschritt → plant           [+ Paar hinzufügen]
      Metadatenfelder:    (keine)                            [+ Feld hinzufügen]
      Kaskadenlöschen:    nein
      Zyklen erlaubt:     — (nicht relevant, keine Selbstreferenz)
      Kardinalität:       nicht gesetzt                       [festlegen]
      Farbe:              ■ Blau

Jede Zeile lässt sich wie bei den Knotentypen direkt in der Liste aufklappen. Kein separates Fenster, kein Kontextwechsel.

---

## 5. Navigationsmodell: wie man ein so dichtes Modell begeht

Ein Modell mit sechs Wochen Prozessdauer, hunderten Equipment-Knoten und zwanzig Kantentypen lässt sich nicht in einer einzigen Ansicht darstellen. Graphatlas bietet deshalb mehrere Ansichten auf dieselben Daten, jede für eine andere Frage optimiert, und einen schnellen Wechsel dazwischen statt einer allwissenden Superansicht.

### 5.1 Fokus-Ansicht (die Standardnavigation)

Ein Knoten steht im Zentrum. Alle seine Kanten sind gruppiert nach Kantentyp und Richtung darum herum aufgelistet, in der visuellen Kodierung aus dem Kantentyp-Editor. Ein Klick auf einen Nachbarn macht ihn zum neuen Zentrum; die zuletzt besuchten Knoten bleiben als Pfad (Breadcrumb) oben sichtbar und anklickbar, damit man nicht "verloren geht".

    ← Prozess A  ←  Stream 1  ←  UV-CURING-3

    ┌─────────────────────────────┐
    │        UV-CURING-3          │   prozessschritt
    └─────────────────────────────┘
    laeuft_auf →           EQ-UV-002
    folgt_auf  →           TROCKNEN-3
    folgt_auf  ←           BESCHICHTEN-3
    verbraucht →           KOMPONENTE-B-WIP
    erzeugt    →           KOMPONENTE-B-CURED
    referenziert →         AA-UV-CURING-v4
    zeitfenster_zu →       FREIGABEPRUEFUNG-1  (12–32 min)

    [+ Kante hinzufügen]

Neue Kanten entstehen direkt von hier aus: Kantentyp wählen, die Zielauswahl ist automatisch auf die im Kantentyp erlaubten Sammlungen gefiltert, Ziel per Suche finden oder neu anlegen. Das ist der Ort, an dem man die meiste Zeit verbringt, wenn man modelliert statt importiert.

### 5.2 Tabellenansicht pro Knotentyp

Für Import, Masseneingabe und -korrektur: alle Instanzen eines Typs als Zeilen, Felder als Spalten, jede Zelle inline editierbar — dieselbe Dichte, die sich bei anderen eigenen Werkzeugen mit Tabellen bereits bewährt hat, statt eine Zeile pro Dialog zu öffnen. Import (JSON/CSV) ist der prominente Einstieg dieser Ansicht, nicht ein verstecktes Menü: Datei wählen, Feldzuordnung bestätigen (bei abweichenden Spaltennamen), Konfliktverhalten festlegen (neue Zeilen anlegen, bestehende per ID aktualisieren), fertig. Mehrere Zeilen lassen sich markieren und in einem Zug verknüpfen — "diese 80 Equipment gehören alle zu Plant A".

### 5.3 Sequenzansicht (der Lucid-Ersatz)

Für Kantentypen mit Ablauf-Charakter (`folgt_auf`, `reprocessing_zu`) generiert diese Ansicht automatisch ein flowchart-artiges Bild aus den vorhandenen Daten — kein von Hand gepflegtes Lucid-Diagramm mehr, sondern eine direkte Sicht auf die Struktur. Bedingungen erscheinen als Beschriftung an der Kante, Reprocessing-Schleifen in ihrer eigenen Farbe aus dem Kantentyp-Editor, Verzweigungen und Zusammenführungen ergeben sich aus der Topologie, ohne dass man sie gesondert einzeichnen müsste. Diese Ansicht ist reine Ableitung, keine eigene Datenquelle — ändert sich die Struktur in der Fokus-Ansicht, ändert sich das Bild hier automatisch mit.

### 5.4 Hierarchieansicht

Für rekursive Kantentypen (`hat_teilprozess`, `besteht_aus`): ein auf- und zuklappbarer Baum, wie man ihn von einem Dateibaum kennt, nur dass die "Datei" hier ein beliebiger Knotentyp sein kann. Hauptprozess aufklappen zeigt die Streams, ein Stream aufklappen zeigt seine Subprozesse.

### 5.5 Cross-Modell-Ansicht (kantentyp-zentriert)

Das ist die Ansicht, die kein Flowchart je leisten konnte: alle Kanten eines bestimmten Typs, über alle Prozesse und Knoten hinweg, als eine Liste. "Alle 27 `zeitfenster_zu`-Regeln, egal zu welchem Prozess", "alle `qualifiziert_fuer`-Zuordnungen zwischen Streams und Plants", "alle Dokumente vom Typ PQ, an welchem Knoten auch immer sie hängen". Diese Ansicht beantwortet genau die Fragen, wegen derer dieses Werkzeug überhaupt entsteht.

### 5.6 JSON-Rohansicht

Immer erreichbar, pro Knotentyp oder Kantentyp: die zugrundeliegende Datei, direkt lesbar und editierbar. Kein Notausgang für den Fehlerfall, sondern eine gleichwertige Ansicht für den Fall, den der ganze Abend über Thema war — ein SAP-Export korrigieren, ohne durch fünf Formulare zu müssen.

---

## 6. Validierung als eigener, jederzeit aufrufbarer Bereich

Validierung passiert bewusst *nicht* als Fehlermeldung im Moment des Tippens. Ein Modell mit vielen Abhängigkeiten befindet sich die meiste Zeit im Aufbau — dort wäre eine strenge Prüfung bei jeder einzelnen Kante eine Bremse, kein Nutzen. Stattdessen gibt es einen eigenen Bereich, den man aufruft, wenn man es wissen will (typischerweise vor einer Freigabe oder nach einem größeren Import):

- **Vollständigkeit**: fehlende Kanten, wo eine Kardinalitätsregel gesetzt ist
- **Zyklenfreiheit**: prüft nur die Kantentypen, die im Editor als "azyklisch" markiert sind, gegen den tatsächlichen Graphen
- **Erreichbarkeit**: Knoten ohne eingehende oder ausgehende Kanten des erwarteten Typs — ein Subprozess ohne Nachfolger, der nicht bewusst das Ende ist
- **Materialfluss-Kontinuität**: jedes `verbraucht`-Material stammt aus Stückliste oder vorherigem `erzeugt`
- **Konsistenz zwischen Ebenen**: logische Ablaufreihenfolge (`folgt_auf`) widerspricht nicht der physischen Linienreihenfolge (`naechste_station`); jedes von einem Stream genutzte Equipment gehört zu einer Plant, für die der Stream qualifiziert ist
- **Zeitfenster-Widersprüche**: sich überlappende `zeitfenster_zu`-Regeln, die sich gegenseitig unmöglich machen

Das Ergebnis ist eine Liste, kein Popup — jeder Eintrag springt direkt zum betroffenen Knoten in der Fokus-Ansicht. Prüfen ist damit eine Navigation wie jede andere, kein Sonderzustand der App.

---

## 7. Mehrere Prozesse

Die oberste Navigationsebene ist eine Liste der Hauptprozesse als Einstiegspunkte — ähnlich einer Projektliste. Wichtig ist, was darunter *nicht* getrennt wird: Stammdaten wie Equipment, Plants, Räume und Dokumente sind global dieselben Knoten, unabhängig davon, von welchem Hauptprozess aus man sie erreicht. Ein Equipment, das in drei Prozessen verwendet wird, ist ein einziger Knoten mit drei eingehenden `laeuft_auf`-Kanten aus drei verschiedenen Prozessbäumen — nicht drei Kopien. Genau daraus ergeben sich die Cross-Prozess-Fragen aus Abschnitt 5.5, ohne zusätzlichen Aufwand.

---

## 8. Bedienungsgrundsätze (durchgängig, in jeder Ansicht)

- **Keine Modals.** Bearbeitung passiert inline oder durch Umschalten der Ansicht, nie durch ein Fenster über dem Fenster.
- **Autosave.** Jede Feldänderung speichert bei Verlassen des Feldes, kein Speichern-Button, kein "ungesicherte Änderungen"-Zustand.
- **Suche als Basisnavigation.** Eine Suchleiste findet Knoten, Kantentypen und Knotentypen gleichermaßen, unabhängig davon, in welcher Ansicht man sich gerade befindet.
- **Tastaturwege für die häufigen Handgriffe.** Schnell zwischen Fokus-Knoten springen, schnell eine Kante anlegen, ohne jedes Mal zur Maus zu greifen — das ist die Bedienung, die bei vielen kleinen Handgriffen den Unterschied macht, nicht die, die in einer Demo gut aussieht.
- **JSON immer eine Ansicht entfernt, nie eine Blackbox darunter.**

---

## 9. Anforderungen an flatgraph (Backlog für die Engine)

Diese App stellt Anforderungen an die zugrundeliegende Engine, die flatgraph heute so noch nicht erfüllt. Das ist kein Hindernis für dieses Konzept, sondern schlicht eine Liste offener Punkte für die Weiterentwicklung von flatgraph selbst:

1. **Schema und Kantenregeln müssen zur Laufzeit veränderbar und selbst persistiert sein.** Heute sind `schemas` und `edge_constraints` Konstruktor-Parameter — beim Start fest vorgegeben. Wenn man Knoten- und Kantentypen aber *durch die App* anlegt und konfiguriert, muss diese Konfiguration selbst gespeicherte, ladbare Daten sein, keine Python-Argumente. Das ist die zentrale Voraussetzung für den gesamten Typ-Editor aus Abschnitt 3 und 4.
2. **`kantenfilter` als Parameter für `collect_related`**, analog zu `traverse` — für Ableitungen wie "alle Equipments entlang des freigegebenen Pfads, nicht des Reprocessing-Pfads" (bereits an früherer Stelle notiert).
3. **Kardinalitätsprüfung als optionale Validierungsschicht.** Ob wirklich gebraucht, ist offen (siehe Abschnitt 4.1) — der Haken im Editor sollte aber schon vorbereitet sein, falls sich der Bedarf zeigt.
4. **Umkehrbezeichnung und visuelle Kodierung eines Kantentyps** sind reine App-Konfiguration, kein flatgraph-Bedarf — nur der Vollständigkeit halber hier vermerkt, damit klar ist, dass dafür keine Engine-Änderung nötig ist.

---

## 10. Bewusst nicht Teil dieser App

- **Kein Ausführungs-/Tracking-Layer.** Das Modell bildet den Zustand ab (wie ein Prozess aufgebaut ist), nicht den Verlauf (was wann tatsächlich passiert ist). Der Batch Record ist ein Dokumenttyp, kein eigener Knoten mit Zeitstempeln.
- **Keine Kapazitätsplanung oder Terminierung.** Ob zwei Aufträge sich ein Equipment zeitlich streitig machen, ist eine andere Fragestellung als die hier modellierte Struktur.
- **Kein Rechte-/Rollenmanagement.** Einzelnutzer-Kontext, wie bei den anderen eigenen Werkzeugen auch.
- **Keine Genehmigungs-Workflows für Änderungen am Modell selbst.** Das Modell ist ein Arbeitswerkzeug zum Verstehen und Abfragen der Struktur, kein geregeltes Dokument mit eigenem Freigabeprozess (die realen SOPs und Spezifikationen, auf die es verweist, sind das — nicht das Werkzeug selbst).

---

## 11. Vorschlag für die Bau-Reihenfolge

1. **Typ-Editor** für Knoten- und Kantentypen — das Fundament, ohne das keine andere Ansicht sinnvoll etwas anzeigen kann
2. **Tabellenansicht + Import** pro Knotentyp — damit überhaupt reale Daten (SAP-Exporte) ins Modell kommen
3. **Fokus-Ansicht** mit Kantennavigation und Kante-anlegen — der tägliche Arbeitsmodus
4. **Sequenzansicht** für `folgt_auf` — der unmittelbare, sichtbare Ersatz für die Lucid-Charts
5. **Validierungsbereich**
6. **Hierarchieansicht, Cross-Modell-Ansicht, Mehrprozess-Übersicht** — die Ansichten, die den eigentlichen Mehrwert gegenüber Einzel-Flowcharts sichtbar machen, sobald mehrere Prozesse tatsächlich erfasst sind

---

## 12. Abgleich mit flatgraph (25.09.2026)

Abschnitte 0–11 stehen wie entworfen. Hier, was der Abgleich mit
`flatgraph.py` (`4.0.0-entwurf`) ergeben hat und was entschieden ist.

### Entschieden

- **Graphatlas ersetzt Trellis.** Issues #24–#32 geschlossen.
- **Eigenes Repo.** Bis es angelegt ist, liegt Graphatlas hier unter
  `graphatlas/` (siehe `README.md` dort zum Umzug).
- **Speicherform: eine Datei je Knotentyp, eine je Kantentyp.** Nicht über
  die alte Form 1 (Fassung 2.1: Deltadatei neben jeder Sammlung, kein
  fsync, nicht threadsicher, keine Prozesssperre), sondern über 4.0 mit
  `FlatGraphDB(wurzel, fach_groesse=EINE_DATEI)`. Die Fachgrösse gehört
  dem Bestand (`_meta.json`), siehe `VERTRAG.md` §6. Datei:
  `datenbank/nodes/<typ>/fach_000001.json`, reines `{id: knoten}`.
  Gemessen, Knoten zu ~300 Byte: 6 ms je Schreibvorgang bei 1000 Einträgen
  je Typ, 26 ms bei 5000 — für Autosave ausreichend, Import in
  `transaction()`.

### Ergänzungen zu Abschnitt 9 (Anforderungen an flatgraph)

1. **Schema zur Laufzeit** (9.1) — die Lücke ist grösser als dort
   beschrieben. flatgraph prüft beim Schreiben und hart (`_validate_node`);
   §6 will lax erfassen und auf Abruf prüfen. Das Feldmodell kennt kein
   optionales Textfeld (einfacher Typ = Pflicht, `link` = optional),
   `float` lehnt `int` ab, Kanten-Metadaten werden gar nicht geprüft.
   Naheliegend: Graphatlas prüft selbst (Validierungsbereich) und nutzt
   flatgraph-`schemas` nicht oder nur für harte Invarianten.
2. **Kante ändern fehlt.** Es gibt kein `update_edge`; Metadaten einer
   Kante (`min_minuten`) ändern heisst löschen und neu anlegen, mit neuer
   Kennung. Für Inline-Bearbeitung mit Autosave (§8) nötig.
3. **Kaskade hängt an der einzelnen Kante**, nicht am Kantentyp
   (`create_edge(..., cascade_delete=True)`). Die App setzt sie beim
   Anlegen aus dem Typ; eine spätere Änderung am Typ erreicht vorhandene
   Kanten nicht von selbst.
4. **`kantenfilter` für `collect_related`** (9.2) — bestätigt; `traverse`
   hat ihn. Zudem hält `collect_related` die Startebene in jeder Stufe
   fest (`current | next_set`): keine strenge Pfadabfrage.
5. **Typdefinitionen speichern:** Namen mit führendem `_` gehören
   flatgraph (`VERTRAG.md` §4). Als Knoten in eigener Sammlung also ohne
   Unterstrich — oder 9.1 wird Engine-Funktion, dann in Bibliothekssprache.
6. **CSV-Import liefert Text.** Zahlenfelder brauchen Umwandlung vor dem
   Schreiben, sonst lehnt eine Schemaprüfung sie ab.

### Was ohne Engine-Änderung geht

Zyklenprüfung, Kardinalität, Erreichbarkeit: über `list_edges(rel_type)`
und `get_connected` in der App. Die JSON-Rohansicht (§5.6) ist mit
`EINE_DATEI` genau die Datei auf der Platte; bearbeiten bei laufender App
nur über die Engine (eine offene Instanz je Bestand), bei geschlossener
App direkt in der Datei. Weich gelöschte Knoten stehen mit
`_deletion_flag` darin.
