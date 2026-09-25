# flatgraph — was diese Bibliothek zusagt und was nicht

Gilt für `2.0.0-entwurf` und für die Basislinie, soweit nicht anders
vermerkt. **Beschrieben ist das heutige Verhalten, auch wo es schlecht
ist** — ein Vertrag, der Absichten beschreibt, ist keiner. Was sich ändern
soll, steht unter „Bekannte Schwächen" und in den GitHub-Issues des pDMS-Repos.

Alles hier ist **nachgemessen**, nicht aus dem Code gelesen. Die
Messungen stammen vom 17.09.2026.

---

## 1. Was flatgraph ist

Ein eingebetteter Graphspeicher in einer Datei, ohne Fremdabhängigkeiten.
Knoten liegen in Sammlungen, Kanten verbinden Knoten über Referenzen der
Form `sammlung/kennung`. Der gesamte Bestand liegt im Arbeitsspeicher;
die Dateien auf der Platte sind die Sicherung dieses Zustands, nicht die
Arbeitsgrundlage.

Daraus folgt das Wichtigste: **flatgraph ist für Bestände gedacht, die in
den Arbeitsspeicher passen.** Es gibt kein Nachladen, keine Seiten, keinen
Puffer.

---

## 2. Was zugesagt wird

### 2.1 Ein Schreibvorgang ist ganz oder gar nicht

Jede Datei wird über eine Arbeitsdatei daneben geschrieben und per
`os.replace` eingetauscht. Ein Absturz mitten im Schreiben hinterlässt
entweder den alten oder den neuen Inhalt, nie eine Mischung.

### 2.2 Geschriebenes ist dauerhaft

Vor dem Eintauschen wird die Arbeitsdatei mit `fsync` auf die Platte
gezwungen, danach auch der Verzeichniseintrag. Ohne das wäre der Austausch
zwar in der Reihenfolge atomar, die Daten stünden aber womöglich noch im
Schreibpuffer — bei Stromausfall zeigt der neue Name dann auf eine leere
Datei.

**Ausgelagerte Langtexte** (`vault_text/`, bei eingeschaltetem
`longtext_threshold`) gehen ab `3.0.0-entwurf` denselben Weg. Jede Fassung
eines Texts bekommt eine eigene Datei, benannt nach Knoten, Feld und dem
Hash ihres Inhalts; keine wird je überschrieben. Sie ist geschrieben,
bevor der Knoten auf sie zeigt. Ein Rollback oder ein Absturz lässt den
Knoten also auf seinen alten Text zeigen, und der ist unverändert da.
Alte Fassungen und abgebrochene Arbeitsdateien räumt der Müllsammler weg;
bis dahin belegen sie Platz. Bis 3.0.0 wurde die Datei an Ort und Stelle
überschrieben, ohne fsync: nach einem Rollback stand der neue Text im
alten Knoten, und `DOC.1` und `DOC_1` teilten sich eine Datei. Geprüft in
`tests/test_flatgraph_langtext.py`.

> **Unter Windows** lässt sich ein Verzeichnis nicht mit `fsync`
> sichern; flatgraph überspringt diesen Schritt dort still. Der Inhalt
> jeder Datei ist gesichert, dass der neue Name nach einem Stromausfall
> gilt, aber nicht. Das ist nicht geprüft.

> **Einschränkung:** Diese Zusage gilt in der Basislinie und in der
> Arbeitsfassung, **nicht in Version 1**. Sie ist eine örtliche Abweichung
> (siehe `HERKUNFT.md`) und muss beim Zusammenführen nach oben.

### 2.3 Eine Transaktion ist alles oder nichts

`transaction()` puffert Schreibvorgänge im Speicher und schreibt sie
gebündelt beim Verlassen. Fliegt eine Ausnahme, wird der Zustand des
Arbeitsspeichers auf den Stand davor zurückgesetzt und nichts geschrieben.
**Nachgemessen:** nach einem Rollback ist der angelegte Knoten weg.

Verschachtelte Transaktionen schliessen sich der äusseren an; erst deren
Verlassen schreibt.

**Ab `3.0.0-entwurf`: Undo-Log statt Kopie.** Bis 3.0.0 wurde beim
Betreten der ganze Bestand tief kopiert — bei 10 000 Knoten 156 ms, auch
für eine einzige Änderung. Jetzt merkt sich jede Änderung vor ihrer ersten
Berührung den alten Zustand, und ein Rollback setzt nur diese Einträge
zurück, samt Nachbarschafts- und Feldindex und samt Sammlungen und
Kantenarten, die erst in der Transaktion entstanden. Auch
Protokolleinträge (`audit=True`) einer zurückgenommenen Änderung
verschwinden. Geprüft in `tests/test_flatgraph_transaktion.py`, über den
vollständigen Zustand im Speicher und nach einem Neustart.

**Der Müllsammler läuft nicht in einer Transaktion** (`NichtInTransaktion`,
ein `RuntimeError`). Er verschiebt Anhänge und schreibt sofort; ein
Rollback könnte das nie zurücknehmen.

**Ab `3.0.0-entwurf` gilt „alles oder nichts“ auch auf der Platte**
(Absichtsdatei, Issue #32). Bis dahin wurde beim Verlassen Fach für Fach
geschrieben; ein Absturz, ein Stromausfall oder eine volle Platte
dazwischen liess die Transaktion halb ausgeführt zurück — gezeigt an einer
Umbuchung von 100 zwischen zwei Knoten, nach dem Neustart waren beide 0.
Jetzt, sobald der Abschluss mehr als ein Fach berührt:

1. jedes neue Fach als `<fach>.neu` dauerhaft schreiben (das Alte bleibt),
2. `datenbank/_absicht.json` dauerhaft schreiben — **ab hier gilt die
   Transaktion**,
3. die `.neu` an ihren Platz umbenennen, Leergewordenes löschen,
4. die Absichtsdatei löschen.

Beim Öffnen wird eine gefundene Absicht zu Ende geführt, bevor gelesen
wird; eine `.neu` ohne Absicht gilt nicht und wird weggeräumt. Eine
Absicht, die etwas anderes als ein Fach dieses Bestands nennt, oder eine
unlesbare, wird mit `DateiKaputt` abgewiesen, nicht übergangen.

Scheitert Schritt 1 oder 2, wirft die Transaktion den Fehler, und Speicher
wie Platte stehen auf dem alten Stand. Scheitert Schritt 3 mit einem
Fehler (kein Absturz), wirft sie `AbschlussHaengt` (auch `OSError`): die
Transaktion **gilt**, der Speicher zeigt den neuen Stand, und der nächste
Schreibvorgang oder das nächste Öffnen holt den Rest nach.

Berührt der Abschluss nur ein Fach — der Normalfall ausserhalb einer
Transaktion —, schreibt er es wie bisher direkt: `os.replace` ist für eine
Datei schon atomar, und es kostet nichts zusätzlich. Geprüft in
`tests/test_flatgraph_absicht.py`, mit einem simulierten Absturz an jeder
Stelle des Abschlusses.

### 2.4 Löschen ist zweistufig und wiederholbar

`soft_delete` setzt nur eine Marke; der Knoten verschwindet aus `get_node`
und `list_nodes`, bleibt aber lesbar über `get_node_raw`. Erst
`run_garbage_collection` entfernt ihn endgültig, und dieser Lauf darf
jederzeit abbrechen und neu starten, ohne Schaden anzurichten.

**Kaskade (ab `3.0.0-entwurf`, Issue #34).** Eine Kante mit
`cascade_delete=True` nimmt ihr Ziel mit, über beliebig viele Stufen, nur
in Richtung Quelle → Ziel. Im Normalfall ist das **aus**.

- `soft_delete` legt die Kaskaden-Ziele **sofort** mit in den Papierkorb,
  markiert mit `_geloescht_durch` (dem Knoten, mit dem sie gingen), in
  einer Transaktion: ganz oder gar nicht. Bis 3.0.0 markierte erst der
  Müllsammler sie und löschte sie im selben Lauf endgültig — sie lagen nie
  im Papierkorb.
- An einem schon gelöschten Knoten endet die Kaskade; was dahinter liegt,
  gehört zu dessen Löschen.
- `restore_node(x)` holt zurück, was mit `x` gegangen ist, nicht aber, was
  unabhängig davon gelöscht war. Einen Mitgelöschten einzeln
  zurückzuholen, holt nur ihn.
- **Der Müllsammler löscht nur, was vor seinem Lauf im Papierkorb lag.**
  Findet er noch Kaskaden-Ziele, die dort nicht liegen (Bestände von
  früher), legt er sie hinein; endgültig weg sind sie erst beim nächsten
  Lauf. Nichts verschwindet, ohne im Papierkorb gewesen zu sein.

**Vorher fragen**, für einen Löschdialog („nur hier entfernen“ oder
„überall löschen“), ohne etwas zu verändern:

- `verwendungen(ref, direction="in")`: wer zeigt hierher,
  `{kantenart: [(kanten_id, andere_ref), ...]}`; `"both"` für Anwender
  mit ungerichteten Kanten. Enden im Papierkorb zählen nicht.
- `loeschfolgen(ref)`: `{"knoten": [...], "kanten": [...]}` — was
  `soft_delete` mit in den Papierkorb legt und welche Kanten der
  Müllsammler danach entfernt.

Beides baut auf `traverse(..., kantenfilter=f)`: gegangen werden nur Kanten,
für die `f(kante)` wahr ist; die Kante kommt schreibgeschützt.

Kosten, gemessen bei 10 000 Knoten, die Hälfte der Löschungen mit einem
Kaskaden-Ziel: `soft_delete` 0.78 → 0.98 ms (Median), weil das
Mitgenommene jetzt gleich geschrieben wird. Geprüft in
`tests/test_flatgraph_loeschen.py`.

### 2.5 Eine Kante zeigt auf vorhandene Knoten

`create_edge` prüft beim Anlegen, ob Quelle und Ziel existieren und nicht
weich gelöscht sind, und wirft sonst `ValueError`. **Nachgemessen.**

> Das gilt beim **Anlegen**. Wird ein Knoten später weich gelöscht,
> bleiben seine Kanten bestehen; `get_connected` filtert sie beim Lesen
> heraus.

### 2.6 Der Speicher ändert sich nur, wenn die Platte die Änderung aufnehmen kann

Ab `3.0.0-entwurf`. Geprüft wird VOR jeder Änderung am Arbeitsspeicher:

- **Der Wert kommt als JSON unverändert zurück.** Abgewiesen mit
  `NichtSpeicherbar` (auch ein `TypeError`): Werte ohne JSON-Form
  (`date`, `set`, eigene Objekte), `NaN` und Unendlich (kein gültiges
  JSON), Tupel (kämen als Liste zurück) und Zahlenschlüssel (kämen als
  Text zurück). Geprüft bei `create_node`, `update_node` (am
  zusammengeführten Knoten) und `create_edge` (samt Metadaten).
- **Sammlungen und Kantenarten haben sichere Namen.** Abgewiesen mit
  `UngueltigerName` (auch ein `ValueError`), siehe Abschnitt 7.
- **Kennungen sind nicht leere Zeichenketten.** Ebenfalls `UngueltigerName`.

Vorher wurde erst der Speicher geändert und dann geschrieben. Scheiterte
das Schreiben, widersprachen sich beide — nachgewiesen:

| Eingabe | Folge vorher |
|---|---|
| Knoten mit einem `date` | im Speicher, nicht auf der Platte; nicht neu anlegbar (`KnotenExistiert`); nach dem Neustart weg; `.tmp` blieb liegen |
| Kante mit einem `date` in den Metadaten | jede weitere Kante DIESER Art scheiterte, bis zum Neustart |
| Zahl als Kennung (`5`) | vor dem Neustart unter `a/5` nicht auffindbar, danach schon |
| zu lange Kennung (nur Speicherform 2, als Dateiname) | `OSError` beim Schreiben, Knoten blieb im Speicher |
| `NaN` | geschrieben — für jedes Werkzeug ausser Python unlesbar |
| Sammlung `../../x` | Verzeichnis ausserhalb von `datenbank/` angelegt |

Die Prüfung kostet 7 µs je Knoten mit 500 Bytes (40 µs bei 6 kB), unter
1 % eines Schreibvorgangs. Festgenagelt in
`tests/test_flatgraph_haertung.py`; gegen 3.0.0 fallen dort 34 von 47
Prüfungen.

---

### 2.7 Meldungen bei Änderungen

Ab `3.0.0-entwurf` (flatgraph 4.0). `FlatGraphDB(wurzel, bei_aenderung=f)`
ruft `f(meldung)` nach jeder Änderung auf, mit einem Dict:
`{"ereignis", "ref", "zeit"}` plus `"sammlung"` bei Knoten bzw.
`"kantenart"`, `"quelle"`, `"ziel"` bei Kanten. Ereignisse:
`create_node`, `update_node`, `soft_delete`, `restore_node`,
`create_edge`, `delete_edge`. Interne Sammlungen (`_audit_log`) werden
nicht gemeldet.

- **In einer Transaktion** wird erst nach dem erfolgreichen Abschluss
  gemeldet, in Reihenfolge; nach einem Rollback nie — auch nicht später.
- **Ein Fehler im Rückruf** macht die Änderung nicht rückgängig und
  erreicht den Aufrufer nicht; er wird über `logging` (Logger
  `"flatgraph"`) protokolliert.
- Der Rückruf läuft im aufrufenden Thread, unter der Sperre der Instanz. Er
  darf lesen und schreiben (die Sperre lässt denselben Thread wieder
  hinein); was lange dauert (etwa HTTP), gehört in einen eigenen Thread
  oder eine Warteschlange des Anwenders. Solange er läuft, warten alle
  anderen Threads. **Wartet er selbst auf einen Thread, der die Datenbank
  braucht, hängen beide für immer.** Das ist Absicht: nur unter der Sperre
  kommen die Meldungen in der Reihenfolge der Änderungen an, auch wenn
  mehrere Threads schreiben.

Bis 3.0.0 verschickte flatgraph stattdessen selbst HTTP (`webhooks=`): je
Ereignis ein neuer Thread ohne Obergrenze, jeder Fehler still
verschluckt, und in einer Transaktion sofort — auch für Änderungen, die
ein Rollback danach zurücknahm. `webhooks=` wirft ab 4.0 einen
`TypeError`, statt still wirkungslos zu sein. Geprüft in
`tests/test_flatgraph_rueckruf.py`.

## 3. Was ausdrücklich NICHT zugesagt wird

### 3.1 Threadsicherheit — ab `3.0.0-entwurf` zugesagt

Jede öffentliche Methode läuft unter einer Sperre der Instanz
(wiedereintrittsfähig), und `transaction()` hält sie über ihren ganzen
Block. Mehrere Threads dürfen dieselbe Instanz benutzen; ihre Aufrufe
laufen nacheinander, nie ineinander. Eine Transaktion ist damit auch der
Weg, mehrere Aufrufe gegen andere Threads unteilbar zu machen — etwa
`next_id` und `create_node`.

**Nicht geschützt** ist, was `readonly=True` herausgibt: Verweise in den
Speicher. Wer sie später liest, liest ohne Sperre, während ein anderer
Thread schreiben kann.

**Kosten:** rund 0.5 µs je Aufruf (bench Block 1): `get_node` 2.3 →
2.8 µs, `get_node(readonly=True)` 0.35 → 0.8 µs. Bei Aufrufen, die
Millisekunden dauern, fällt es nicht ins Gewicht.

Bis 3.0.0 stand hier „KEINE Threadsicherheit“, und in Abschnitt 8 „keine
Lücke, sondern eine Entscheidung“. Das war aus Sicht eines einzelnen
Anwenders geurteilt, der selbst serialisierte (pDMS, nach einem Lasttest
mit 1195 Fehlern, darunter halb geschriebenes JSON, am 09.09.2026). Für
eine Bibliothek, die man ohne dieses Wissen einsetzt, ist lautloser
Datenverlust keine vertretbare Voreinstellung. Geprüft in
`tests/test_flatgraph_threads.py`.

### 3.2 Ein Bestand, eine offene Instanz

**Ab `3.0.0-entwurf` zugesagt und durchgesetzt.** Beim Öffnen wird der
Bestand gesperrt. Jede weitere Instanz — aus einem anderen Prozess oder
aus demselben — bekommt `BestandBelegt` (ein `RuntimeError`), bis die
erste geschlossen ist. `e.im_selben_prozess` sagt, welcher Fall es ist.

- Freigabe mit `db.close()`, mit `with FlatGraphDB(wurzel) as db:` oder
  wenn die Instanz weggeräumt wird. Stirbt der Prozess, hebt das
  Betriebssystem die Sperre auf; sie überlebt ihn nie.
- Ein Öffnen, das scheitert (`DateiKaputt`, `SpeicherformZuNeu`), gibt die
  Sperre sofort wieder frei.
- Nach `close()` wirft jeder Schreibvorgang `BestandGeschlossen`, bevor
  er den Speicher anfasst.
- **Der Müllsammler ist eine Methode jeder Instanz:**
  `db.run_garbage_collection()`. `MaintenanceEngine` gibt es weiter, aber
  nur noch als einzige Instanz des Bestands, nicht als zweite daneben.
- `file_lock` wird noch angenommen, hat aber keine Wirkung mehr
  (`DeprecationWarning`).

Warum so streng: zwei Instanzen halten je einen eigenen Stand im
Speicher, und was die eine schreibt, sieht die andere nie. Bis 3.0.0
umschloss `file_lock=True` nur den einzelnen Schreibvorgang, und das
Kantenschreiben las vor jedem Schreiben die ganze Kantendatei nach, um
fremde Änderungen mitzunehmen. Die Knoten blieben trotzdem auseinander,
und das Muster „App-Instanz plus `MaintenanceEngine` daneben“ liess nach
dem Aufräumen eine Kante auf einen gelöschten Knoten zurück
(nachgewiesen). Seit der Sperre gibt es nur einen Stand, und das
Nachlesen ist weg.

Geprüft in `tests/test_flatgraph_sperre.py`, zwischen Prozessen mit
einem echten zweiten Prozess.

> Die Sperre benutzt `flock` bzw. unter Windows `msvcrt.locking` auf
> `wurzel/.flatgraph.lock`. Auf Netzlaufwerken (NFS, SMB) sind
> Dateisperren je nach Server und Einstellung unzuverlässig oder fehlen;
> dort schützt sie womöglich nicht. Das ist nicht geprüft. Ein Bestand
> gehört auf eine örtliche Platte.

### 3.3 KEINE Rückwärtskompatibilität des Formats

`SPEICHERFORM` beziffert die Form auf der Platte, getrennt von
`__version__`. Ändert sie sich, kann eine ältere Bibliothek den Bestand
nicht mehr lesen.

**Was zugesagt wird:** Trifft diese Fassung auf einen Bestand in einer
**neueren** Speicherform, verweigert sie das Öffnen mit
`SpeicherformZuNeu` — einem eigenen Fehlertyp, damit der Aufrufer „zu alt"
von „kaputt" unterscheiden kann, ohne in Fehlertexten zu suchen. Das eine
ist ein Update, das andere eine Reparatur.

Die Marke steht in `datenbank/_meta.json` und wird beim Öffnen angelegt,
wenn sie fehlt. Ein Bestand von vor dieser Prüfung öffnet also normal.

**Ein Bestand in einer älteren Form wird beim Öffnen umgezogen**, und zwar
in dieser Reihenfolge: erst alle neuen Dateien schreiben, dann die alten
entfernen, die Marke ZULETZT. Bricht der Vorgang ab, steht die Marke noch
nicht und der nächste Start fängt von vorn an — zurück bleibt ein
unveränderter Bestand, nie ein halb umgezogener. **Einen Rückweg gibt es
nicht:** wer auf eine ältere Bibliothek zurück will, braucht eine Sicherung
von vorher.

### 3.4 Was eine Kennung aushalten muss

Ab Speicherform 3 ist eine Kennung kein Dateiname mehr, sondern ein
Schlüssel in einem Fach. Die beiden Grenzen aus Form 2 entfallen damit:
Kennungen, die sich nur in der Gross-/Kleinschreibung unterscheiden,
kollidieren auf macOS und Windows nicht mehr, und die Länge ist nicht mehr
durch die Namenslänge des Dateisystems begrenzt.

> Bis zum 17.09.2026 gab es diese Prüfung nicht: eine ältere Fassung las
> einen neueren Bestand kommentarlos als **leer**. Genau das ist am 16.09.
> beim Umbau auf „eine Datei je Knoten" aufgefallen und war der Grund, ihn
> zurückzunehmen.

### 3.4 Keine Aussage über sehr grosse Bestände

Alles liegt im Arbeitsspeicher. Es gibt keine gemessene Obergrenze; die
Kosten in Abschnitt 5 wachsen linear und werden irgendwann untragbar,
bevor der Speicher knapp wird.

---

## 4. Verhalten im Fehlerfall

**Nachgemessen.** Seit 2.2 scheitert kein Schreibvorgang mehr still.

| Aufruf | Ergebnis |
|---|---|
| `create_node` auf vorhandene Kennung | `KnotenExistiert` |
| `create_node` mit falschem Feldtyp | `TypeError` |
| `create_node` ohne Pflichtfeld | `ValueError` |
| `create_edge` auf fehlenden Knoten | `ValueError` |
| `update_node` auf fehlenden Knoten | `KnotenFehlt` |
| `update_node` auf fehlende Sammlung | `KnotenFehlt` |
| `soft_delete` auf fehlenden Knoten | `KnotenFehlt` |
| `restore_node` auf fehlenden Knoten | `KnotenFehlt` |
| `loeschfolgen` auf fehlenden Knoten (ab Entwurf) | `KnotenFehlt` |
| `delete_edge` auf fehlende Kante | `KanteFehlt` |
| `get_node` mit kaputter Referenz | `UngueltigeReferenz` |
| `get_node` auf fehlenden Knoten | `None` |
| `get_edge` auf fehlende Kante | `None` |
| `list_nodes` auf fehlende Sammlung | `{}` |
| Öffnen mit beschädigter Datei | `DateiKaputt` |
| Bestand in neuerer Speicherform | `SpeicherformZuNeu` |
| Öffnen, während eine andere Instanz den Bestand offen hat (ab Entwurf) | `BestandBelegt` |
| Schreiben nach `close()` (ab Entwurf) | `BestandGeschlossen` |
| `run_garbage_collection` in einer Transaktion (ab Entwurf) | `NichtInTransaktion` |
| Abschluss einer Transaktion scheitert NACH dem Festschreiben (ab Entwurf) | `AbschlussHaengt` — die Transaktion gilt |
| `FlatGraphDB(..., webhooks=...)` (ab Entwurf) | `TypeError` |
| `create_node`/`update_node`/`create_edge` mit Wert ohne unveränderte JSON-Form (ab Entwurf) | `NichtSpeicherbar` |
| `create_node` mit unzulässigem Sammlungsnamen oder unzulässiger Kennung (ab Entwurf) | `UngueltigerName` |
| `create_edge` mit unzulässiger Kantenart (ab Entwurf) | `UngueltigerName` |

### Die Fehlertypen

Alle erben von `FlatGraphFehler` — wer alles fangen will, fängt die. Und
**zusätzlich von dem Typ, der vorher geworfen wurde**:

    KnotenFehlt, KnotenExistiert, KanteFehlt   auch KeyError
    UngueltigeReferenz                          auch ValueError
    DateiKaputt                                 auch RuntimeError
    AbschlussHaengt                             auch OSError

Deshalb funktioniert bestehender Aufrufcode mit `except KeyError` weiter.
Das ist der Grund, warum 2.2 eigene Fehlertypen einführen konnte, ohne
etwas zu brechen.

Der Sinn: ein Aufrufer soll „den Knoten gibt es nicht" von „die Datei ist
kaputt" unterscheiden können, ohne in Fehlertexten zu suchen. Das eine ist
ein Tippfehler, das andere eine Reparatur.

### Was sich in 2.2 geändert hat

Bis 2.1 gaben `update_node`, `soft_delete`, `restore_node` und
`delete_edge` bei einem fehlenden Ziel **`False`** zurück. Ein
Schreibvorgang, der ins Leere ging, meldete sich also mit einem
Rückgabewert, den Aufrufer routinemässig ignorieren — nachgezählt in
pDMS: **22 Aufrufstellen, keine einzige liest ihn**. Eine vertippte
Kennung hiess damit: nichts passiert, niemand merkt es.

Ebenso lieferte `get_node` bei einer Referenz ohne `/` schlicht `None` —
ein Programmierfehler war von „gibt es nicht" nicht zu unterscheiden.

### Der führende Unterstrich gehört flatgraph

Genau zwei Knotenfelder gehören der Bibliothek: `_deletion_flag` und
`_keep_asset`. Sie setzt `soft_delete`, und nur sie sind von der
Schemaprüfung ausgenommen — damit sich ein Knoten auch dann löschen lässt,
wenn sein Inhalt längst nicht mehr zum Schema passt.

**Jedes andere Feld ist ein Feld des Aufrufers und wird geprüft, mit oder
ohne Unterstrich davor.** Wer `_intern: str` ins Schema schreibt, bekommt
bei `{"_intern": 42}` einen `TypeError`.

Bis 2.1 galt die Ausnahme für jedes Feld mit führendem Unterstrich. Das
hatte zwei nachgemessene Folgen, beide behoben:

- ein Schemafeld mit Unterstrich wurde **nie** geprüft, und
- es machte **jede** gewöhnliche Änderung am Knoten unmöglich: das Feld
  fiel vor der Prüfung heraus und wurde danach als fehlend gemeldet.

Was flatgraph weiterhin NICHT tut: unbekannte Felder ablehnen. Ein Schema
zählt auf, was da sein muss — nicht, was sonst noch da sein darf.
`{"quatsch": 42}` geht durch, mit und ohne Unterstrich.

---

## 5. Was Dinge kosten

**Nachgemessen am 17.09.2026**, Bestand mit 500 Bytes Text je Knoten:

| | 500 Knoten / 2000 Kanten | 2000 Knoten / 8000 Kanten |
|---|---|---|
| `get_node` | 0.002 ms | 0.002 ms |
| `list_nodes` | 0.85 ms | 3.6 ms |
| `get_connected` | 0.002 ms | 0.002 ms |
| `get_connected_edges` | 0.014 ms | 0.014 ms |
| `list_edges` | 6.9 ms | 27.7 ms |
| `traverse`, Tiefe 2 | 0.005 ms | 0.006 ms |

Die drei Nachbarschaftszeilen stehen seit 2.2 still, weil es seither einen
Index Knoten → Kanten gibt. Vorher liefen sie über ALLE Kanten: bei 2000
Knoten / 20 000 Kanten kostete `get_connected` 1.280 ms statt 0.003 ms und
`traverse` in Tiefe 3 3.894 ms statt 0.010 ms.

Und beim Schreiben, je Knoten mit ~6 kB Text, mit Schreiben auf die Platte
nach jeder Änderung:

| Bestand | je Knoten |
|---|---|
| 200 | 6.6 ms |
| 800 | 32.7 ms |
| 2000 | 89.4 ms |

### Was daraus folgt

- **`get_node` ist der einzige Zugriff, der nicht mit dem Bestand
  wächst.** Alles andere ist linear.
- **`list_nodes` und `list_edges` kopieren jeden Eintrag tief.** Das ist
  die Untergrenze ihrer Kosten und der Grund, warum `list_edges` das
  Teuerste in der Tabelle ist. `list_nodes(..., readonly=True)` und
  `get_node(..., readonly=True)` lassen die Kopie weg und geben den
  Zwischenspeicher selbst heraus — nachgemessen 3.37 ms → 0.35 ms bei 2000
  Knoten. **Wer so liest, darf das Ergebnis niemals ändern**: er hielte
  sonst die Datenbank in der Hand, nicht ihr Abbild.
- **Nachbarschaft kostet nichts mehr, was mit dem Bestand wächst.** Der
  Index Knoten → Kanten wird beim Öffnen gebaut und bei jeder Kante
  mitgeführt. Er kostet Hauptspeicher in der Grössenordnung der
  Kantenzahl — das ist der Preis dafür.
- **Schreiben wächst mit der Sammlung**, weil bei jeder Änderung die ganze
  Sammlung neu geschrieben wird. Wer viele Knoten hintereinander ändert,
  braucht `transaction()` — sonst wird aus einem Massenlauf quadratische
  Arbeit.

---

## 6. Die Form auf der Platte

Speicherform 3 (ab `3.0.0-entwurf`, Issue #32):

    wurzel/
      datenbank/
        _meta.json                          Speicherform und schreibende Fassung
        _absicht.json                       nur während des Abschlusses einer Transaktion (2.3)
        nodes/<sammlung>/fach_000001.json   bis zu 25 Knoten je Fach, {id: knoten}
        edges/<kantenart>/fach_000001.json  bis zu 25 Kanten je Fach, {id: kante}
      vault/                                Anhänge
      vault_archive/                        Anhänge gelöschter Knoten
      vault_text/                           ausgelagerte Langtexte (wenn eingeschaltet)

**Fächer werden aufgefüllt, nicht gestreut:** ein neuer Knoten kommt ins
letzte Fach, bis es voll ist; dann beginnt das nächste. Welche Kennung in
welchem Fach liegt, entsteht beim Öffnen im Speicher — beim Öffnen wird
ohnehin jedes Fach gelesen. Jede Änderung schreibt genau ihr Fach neu, mit
Arbeitsdatei, fsync und `os.replace`; keine Datei wird je an Ort und
Stelle verändert. Lücken entstehen nur, wenn der Müllsammler endgültig
löscht, und er legt dünne Fächer (höchstens halb voll) im selben Lauf
wieder zusammen: erst die neuen Fächer schreiben, dann die alten löschen.
Bricht das dazwischen ab, steht ein Eintrag in zwei Fächern mit gleichem
Inhalt; das nächste Öffnen bereinigt das. Stehen dort zwei VERSCHIEDENE
Fassungen, ist es ein Schaden, und das Öffnen wirft `DateiKaputt`, statt
zu raten.

Warum Fächer: Form 2 (eine Datei je Knoten, eine je Kantenart) liess bei
100 000 Knoten und 400 000 Kanten das Öffnen nach einem Neustart 9.2 s
dauern, eine neue Kante 1.4 s, und legte 100 001 Dateien auf 475 MB an —
für USB-Sticks, Sicherungen und Synchronisation eine Last. Mit Fächern zu
25 im Versuch: 0.98 s, 0.9 ms, 8 000 Dateien auf 140 MB; einen Knoten
schreiben blieb unter 1 ms. 25 war im Versuch das Optimum zwischen 6
und 400.

Eine Datei = ein Knoten gilt damit nicht mehr: ohne flatgraph findet man
einen Knoten per `grep` über die Fächer; die Dateien bleiben lesbares JSON.
Eine beschädigte Datei trifft bis zu 25 Einträge statt einen.

**Umzug:** Ein Bestand in Form 1 oder 2 zieht beim Öffnen um (1 → 2 → 3).
Form 2 → 3 baut das Neue zuerst vollständig daneben auf (`nodes_form3/`,
`edges_form3/`), legt dann das Alte per Umbenennen beiseite, bringt das
Neue an seinen Platz, setzt die Marke und löscht erst danach das
Beiseitegelegte. Ein Abbruch an jeder dieser Stellen wird beim nächsten
Öffnen zu Ende geführt — geprüft in `tests/test_flatgraph_speicherform.py`
mit einem Abbruch bei jedem Umbenennen und jedem der ersten Schreibvorgänge.
Einen Rückweg gibt es nicht: eine Fassung mit Form 2 verweigert einen
umgezogenen Bestand mit `SpeicherformZuNeu`.

**Der Feldindex liegt ab `3.0.0-entwurf` nicht mehr auf der Platte.** Bis
3.0.0 stand er unter `datenbank/index/`, gesichert durch eine Prüfsumme
über den Inhalt jedes Knotens. Diese Prüfung kostete bei 10 000 Knoten
76 ms, der Neuaufbau aus dem Speicher 3.6 ms (flatgraph/bench, Block 6).
Heute wird er beim ersten Suchen je Feld aus dem Speicher gebaut und bei
jedem Schreibvorgang für den einen Knoten nachgeführt. Ein `index/`, das
eine ältere Fassung hinterlassen hat, wird nicht gelesen und darf
gelöscht werden.

Frühere Formen: Form 1 (bis 2.2) hatte je Sammlung eine Sammeldatei und
eine Deltadatei, die bei jedem Schreibvorgang ganz neu geschrieben wurde.
Form 2 (3.0.0) hatte eine Datei je Knoten und eine je Kantenart.

---

## 7. Kennungen

- Eine Knotenreferenz ist `sammlung/kennung`. Der erste Schrägstrich
  trennt; Kennungen dürfen weitere enthalten.
- `next_id(sammlung, prefix, padding)` liefert die höchste vorhandene Zahl
  mit diesem Präfix plus eins, aufgefüllt. Auf leerer Sammlung mit
  `padding=4`: `n_0001`. **Nachgemessen.**
- Die Zahl wird nie wiederverwendet, solange die höchste Kennung im
  Bestand bleibt — nach dem Löschen des höchsten Knotens schon.
- **Kennungen** sind ab `3.0.0-entwurf` nicht leere Zeichenketten und
  dürfen sonst alles enthalten, auch Schrägstriche und Leerzeichen, in
  beliebiger Länge. (Bis Speicherform 2 waren sie Dateinamen und deshalb
  auf 246 Bytes kodiert begrenzt.) Eine Zahl ist keine Kennung: sie
  stand vorher im Speicher unter `5` und nach dem Neustart unter `"5"`.
- **Sammlungen und Kantenarten** werden Verzeichnis- bzw. Dateinamen und
  folgen deshalb ab `3.0.0-entwurf` einer Regel: Buchstaben (auch
  Umlaute), Ziffern, `_`, `-`, `.`; am Anfang ein Buchstabe oder eine
  Ziffer; höchstens 100 Zeichen. Der führende Unterstrich gehört
  flatgraph. Vorher gab es keine Regel: `../../x` brach aus dem
  Datenverzeichnis aus, und die Kantenarten `teil/von` und `teil_von`
  teilten sich eine Datei. Bestehende Namen, die der Regel nicht folgen,
  werden weiter gelesen; nur neu angelegt wird unter ihnen nichts mehr.

---

## 8. Bekannte Schwächen

Vollständig in `docs/archiv/ISSUES_bis_2026-09.md`, „Bestandsaufnahme flatgraph".

Fünf Punkte standen hier in 2.1. Vier davon sind in 2.2 erledigt und
stehen jetzt an ihrer Stelle im Vertrag: der Adjazenzindex (Abschnitt 5),
das stille Scheitern und die Fehlertypen (Abschnitt 4), die Formatprüfung
(Abschnitt 3.3) und die Schemalücke (Abschnitt 4).

Was diesen Vertrag weiterhin unmittelbar betrifft:

1. **Schreiben wächst mit der Sammlung** (Abschnitt 5). Bei jeder
   Änderung wird die ganze Sammlung neu geschrieben. Das ist der Grund
   für `transaction()` — und der Grund, warum die nächste Speicherform
   eine Datei je Knoten sein wird.
2. **Kanten überleben das weiche Löschen ihrer Knoten** (Abschnitt 2.5).
   `create_edge` prüft beim Anlegen; wird ein Knoten SPÄTER weich
   gelöscht, bleiben seine Kanten liegen und werden erst beim Lesen
   herausgefiltert. Sichtbar wird das nur über `list_edges`.
3. **Threads, Prozesse, Instanzen:** seit `3.0.0-entwurf` threadsicher
   (Abschnitt 3.1); mehrere Prozesse und mehrere Instanzen sind
   ausgeschlossen statt nur ungeschützt (Abschnitt 3.2). Bis 3.0.0 stand
   hier „keine Lücke, sondern eine Entscheidung“ — das war falsch.
