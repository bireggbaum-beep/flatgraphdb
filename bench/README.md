# flatgraph — Leistungsmessung

Misst flatgraph **ohne pDMS**, nur mit der Standardbibliothek. Die
Messungen unter `tests/bench_*.py` messen pDMS (Server, Oberfläche,
App-Funktionen); diese hier misst die Bibliothek.

Wozu: `VERTRAG.md` Abschnitt 5 nennt Zahlen vom 17.09.2026, gemessen
noch in Speicherform 1 und ohne ein Skript im Repo, das sie wiederholt.
Eine veröffentlichte Bibliothek, die „nachgemessen“ sagt, braucht die
Messung dazu.

    python bench/lauf.py                     alle fertigen Blöcke
    python bench/lauf.py lesen               ein Block
    python bench/lauf.py --groessen 500,2000,10000
    python bench/lauf.py --json ergebnis.json

    FLATGRAPH_DATEI=/pfad/zu/anderer/flatgraph.py python bench/lauf.py

Aus dem Wurzelverzeichnis aufrufen. Gemessen wird wie bei den Prüfsuiten
`flatgraph.py` im Wurzelverzeichnis, sonst die Fassung aus
`FLATGRAPH_DATEI`.

## Aufbau in Blöcken

Jeder Block ist eine Datei `block_<name>.py`, misst **eine Frage** und
läuft für sich. Ein neuer Block wird erst angefangen, wenn der vorige
fertig ist und seine Zahlen besprochen sind.

| # | Block | Frage | Stand |
|---|---|---|---|
| 1 | `lesen` | Wächst `get_node` mit dem Bestand? Was kostet die Kopie bei `list_nodes`? | **fertig** |
| 2 | `oeffnen` | Wie lange dauert das Öffnen, warm und kalt, mit und ohne Kanten, und wie viel davon ist flatgraph selbst? | **fertig** |
| 3 | `schreiben` | Was kostet ein einzelner `create_node`/`update_node`/`create_edge` ohne Transaktion in Form 2? Wächst es noch mit dem Bestand? | **fertig** |
| 4 | `transaktion` | Was kostet `transaction()` fest, was spart sie bei Knoten und Kanten, was kostet der Rollback? | **fertig** |
| 5 | `nachbarschaft` | `get_connected`, `traverse`, `collect_related`, `list_edges` bei wechselnder Kantendichte | **fertig** |
| 6 | `suche` | `find_nodes` in drei Arten; was kostet der Feldindex warm, nach einer Änderung und frisch nach dem Öffnen? | **fertig** |
| 7 | `aufraeumen` | `soft_delete` plus `run_garbage_collection`, mit Kaskade | offen |
| 8 | `umzug` | Umzug eines Bestands von Speicherform 1 auf 2 beim Öffnen | offen |

Gemessen wird vorerst im temporären Verzeichnis dieses Rechners; wo das
liegt und welches Dateisystem es ist, steht als `ablage` in den
Umgebungsangaben. Eine Option `--ort`, um auf einem bestimmten
Datenträger zu messen, kommt, wenn sie gebraucht wird.

## Wie gemessen wird

Festgelegt in `rahmen.py`, für alle Blöcke gleich:

- **Fester Bestand.** Fester Samen, 500 Bytes Text je Knoten wie im
  Vertrag. Gebaut in einer Transaktion und danach **frisch geöffnet**,
  damit gemessen wird, was ein Anwender nach dem Start vorfindet.
- **Gegenprobe vor jeder Messung.** Das Ergebnis des Aufrufs wird einmal
  geprüft, zum Beispiel ob `get_node` wirklich einen Knoten liefert. Ein
  Aufruf, der ins Leere greift, ist verdächtig schnell. Ohne die Prüfung
  stünde eine falsche Zahl in der Tabelle. Dass die Prüfung anschlägt,
  ist ausprobiert: `get_node` auf eine fehlende Kennung bricht mit
  `Messfehler` ab.
- **Wiederholen bis messbar.** Kurze Aufrufe werden je Stichprobe so oft
  wiederholt, bis die Stichprobe mindestens 5 ms dauert. Berichtet wird
  der Median von 15 Stichproben, dazu Min und Max.
- **Speicherbereinigung aus** während einer Stichprobe, wie bei `timeit`.
- **Kalt heisst: Seitencache vor jeder Stichprobe geleert** (`sync`, dann
  `/proc/sys/vm/drop_caches`). Das geht nur unter Linux mit root; sonst
  entfallen die Kalt-Zeilen, und die Ausgabe sagt es. In einer virtuellen
  Maschine leert das nur den Cache des Gastes. Der Wirt kann die Blöcke
  noch halten, dann ist „kalt“ dort günstiger als nach einem echten
  Neustart.
- **Umgebung zu jeder Zahl:** Fassung, Datei, Speicherform, Commit (mit
  `+geändert`, wenn `flatgraph.py` nicht eingecheckt ist),
  Python, System, Kerne, Ablage mit Dateisystem.

Was hier bewusst nicht passiert: Die Messung prüft keine Grenzwerte und
fällt nicht rot aus. Zeiten schwanken je nach Rechner zu stark für ein
Pass/Fail. Ob ein Wächter in der CI später sinnvoll ist, wird entschieden,
wenn es CI gibt.

## Ergebnisse

### Block 1 — lesen

24.09.2026, `3.0.0-entwurf`, Speicherform 2, Linux-Container mit 4 Kernen,
Python 3.11.

| Messung | 500 | 2000 | 10 000 |
|---|---|---|---|
| `get_node` | 2.70 µs | 2.76 µs | 2.82 µs |
| `get_node readonly` | 0.45 µs | 0.45 µs | 0.51 µs |
| `list_nodes` | 1.19 ms | 4.61 ms | 25.67 ms |
| `list_nodes readonly` | 0.08 ms | 0.26 ms | 1.37 ms |

`FLATGRAPH_DATEI=flatgraph/flatgraph.py` (3.0.0) liefert bei 500 dieselben
Werte, im Rahmen der Streuung.

Was daraus folgt:

- **Die Vertragsaussage hält:** `get_node` wächst nicht mit dem Bestand,
  `list_nodes` wächst linear (×4 Knoten ≈ ×4 Zeit, ×20 ≈ ×22).
- **`readonly` spart bei `list_nodes` etwa 94 %** (Faktor 15 bis 19). Der
  Vertrag nennt für 2000 Knoten 3.37 ms → 0.35 ms, also Faktor 10. Heute
  4.61 ms → 0.26 ms. Das passt der Grössenordnung nach, aber nicht auf
  die Stelle. Vermutlich ein anderer Rechner; derselbe Lauf auf dem
  Rechner vom 17.09. würde es klären.
- **Neu gemessen: `get_node readonly` ist sechsmal schneller** als mit
  Kopie. Im Vertrag steht dazu keine Zahl.

### Block 2 — öffnen

24.09.2026, `3.0.0-entwurf`, Speicherform 2, derselbe Container, `/tmp` auf
ext4 (`stat` meldet ext2/ext3), virtuelle Maschine. Median aus 7
Stichproben.

| Messung | 500 | 2000 | 10 000 |
|---|---|---|---|
| Dateien lesen, warm | 3.4 ms | 14.8 ms | 77 ms |
| Dateien + json, warm | 5.3 ms | 21.4 ms | 111 ms |
| **öffnen, warm** | **8.6 ms** | **35.6 ms** | **175 ms** |
| öffnen, warm, +4 Kanten/Knoten | 11.7 ms | 50.8 ms | 281 ms |
| Dateien lesen, kalt | 39 ms | 160 ms | 792 ms |
| Dateien + json, kalt | 41 ms | 176 ms | 876 ms |
| **öffnen, kalt** | **48 ms** | **210 ms** | **1.03 s** |
| öffnen, kalt, +4 Kanten/Knoten | 54 ms | 208 ms | 1.14 s |

Was daraus folgt:

- **Öffnen wächst linear mit der Knotenzahl**, warm etwa 17 µs und kalt
  etwa 100 µs je Knoten. Einen Sprung gibt es bis 10 000 nicht.
- **Kalt ist etwa sechsmal teurer als warm, und das liegt fast ganz
  am Dateisystem.** Kalt liegt Öffnen nur 15–20 % über der Untergrenze
  „Dateien + json“. Der Preis kalt ist die Datei je Knoten: 10 000 Knoten
  sind 10 000 einzelne Lesezugriffe. Das ist der Preis der Speicherform 2,
  hier zum ersten Mal beziffert.
- **Warm tut flatgraph etwa 60 % zur Untergrenze dazu**, rund 6 µs je
  Knoten (175 ms statt 111 ms bei 10 000). Das ist der einzige Teil, den
  flatgraph selbst in der Hand hat. Der Durchgang je Knoten
  (`os.path.isdir`, `unquote`, Pfad bauen, Laden über
  `_load_json_from_disk` mit eigener Existenzprüfung) wäre die Stelle,
  an der man sucht, falls es je stört. Bei 175 ms für 10 000 Knoten stört
  es nicht.
- **Kanten kosten warm rund 2.6 µs je Kante** (40 000 Kanten: +105 ms),
  fürs Einlesen der einen Datei je Kantenart und den Aufbau des
  Nachbarschaftsindex. Kalt fällt das kaum ins Gewicht, weil es eine
  einzige Datei ist.

### Block 3 — schreiben

24.09.2026, `3.0.0-entwurf`, derselbe Container. Ein Aufruf ohne
Transaktion, Median aus 15 Stichproben. Vor jeder Messung wird geprüft,
dass das Geschriebene wirklich in der Datei steht.

| Messung | 500 | 2000 | 10 000 |
|---|---|---|---|
| Datei dauerhaft schreiben (Untergrenze) | 1.05 ms | 0.96 ms | 0.74 ms |
| `create_node` | 1.02 ms | 1.04 ms | 0.82 ms |
| `update_node` | 1.13 ms | 1.40 ms | 0.63 ms |
| `create_edge`, fast leere Kantenart | 1.87 ms | 1.45 ms | 1.15 ms |
| Kantendatei umschreiben, 4 Kanten/Knoten (Untergrenze) | 11.8 ms | 47.7 ms | 216 ms |
| **`create_edge`, 4 Kanten/Knoten** | **13.9 ms** | **56.7 ms** | **295 ms** |

Was daraus folgt:

- **Knoten schreiben wächst nicht mehr mit dem Bestand.** `create_node`
  und `update_node` liegen bei allen Grössen auf der Untergrenze, rund
  1 ms, und das ist fsync. In Form 1 waren es 6.6 ms bei 200 und 89 ms
  bei 2000 Knoten (VERTRAG.md 5). Die Speicherform 2 hat hier getan,
  wofür sie gebaut wurde. Die Streuung zwischen den Grössen (0.6–1.4 ms)
  ist die Platte, nicht flatgraph; die Untergrenze schwankt genauso.
- **Kanten schreiben wächst linear mit der Zahl der Kanten dieser Art**,
  rund 7 µs je vorhandener Kante: 2000 Kanten 14 ms, 8000 Kanten 57 ms,
  40 000 Kanten 295 ms für EINE neue Kante.
- **Drei Viertel und mehr davon ist das Format** (73–85 %), nicht der Code: Die Kantendatei
  zu lesen und ganz zurückzuschreiben kostet allein schon 216 von 295 ms.
  Das restliche Viertel (rund 2 µs je Kante) ist vor allem der Neuaufbau
  des Nachbarschaftsindex dieser Art in `_flush_edge_type`.
- **Die Speicherform 2 ist damit halb umgesetzt.** Knoten haben je eine
  Datei, Kanten liegen noch gesammelt je Art, mit genau dem Muster, das
  die Knoten in Form 1 hatten. Wer einzeln Kanten anlegt, zahlt, was
  Knoten früher gekostet haben. `transaction()` bündelt das (Block 4).
  Eine Lösung im Format wäre eine Datei je Kante oder ein Anhängeprotokoll
  je Kantenart. Das ist eine Entscheidung zur Speicherform und gehört als
  Issue auf den Tisch, nicht in die Messung.

### Block 4 — transaktion

24.09.2026, `3.0.0-entwurf`, derselbe Container, vier Kanten je Knoten.
Median aus 7 Stichproben, bei „20 Kanten einzeln“ aus 3. Jede Zeile
prüft vorher, dass der letzte Wert auf der Platte steht; der Rollback
prüft, dass der alte Wert im Speicher UND auf der Platte steht.

| Messung | 500 | 2000 | 10 000 |
|---|---|---|---|
| Bestand tief kopieren (Untergrenze) | 6.4 ms | 27.2 ms | 156 ms |
| 1 Änderung ohne Transaktion | 0.76 ms | 0.66 ms | 0.72 ms |
| **1 Änderung in Transaktion** | **8.4 ms** | **34.0 ms** | **181 ms** |
| 100 Änderungen einzeln | 74 ms | 69 ms | 72 ms |
| **100 Änderungen in Transaktion** | **72 ms** | **105 ms** | **259 ms** |
| 20 Kanten einzeln | 262 ms | 1.20 s | 6.32 s |
| **20 Kanten in Transaktion** | **23 ms** | **92 ms** | **498 ms** |
| Rollback nach 1 Änderung | 9.3 ms | 45 ms | 281 ms |

Was daraus folgt:

- **Eine Transaktion hat feste Kosten, die mit dem Bestand wachsen**,
  rund 18 µs je Knoten, auch wenn sie nur einen einzigen berührt. 76 bis
  86 % davon ist die tiefe Kopie des ganzen Bestands beim Betreten.
- **Für Knoten bündelt die Transaktion nichts.** Jeder Knoten bleibt
  eine eigene Datei mit eigenem fsync; 100 Änderungen kosten in der
  Transaktion so viel wie einzeln plus die Kopie. Bei 10 000 Knoten ist
  der Stapel in der Transaktion 3.6-mal LANGSAMER als ohne.
- **Für Kanten spart sie Faktor 11 bis 13**, weil die Kantendatei einmal
  statt zwanzigmal geschrieben wird. Auch dann kosten 20 Kanten bei
  10 000 Knoten eine halbe Sekunde: die Kopie plus ein Umschreiben der
  ganzen Kantendatei.
- **Ein Rollback kostet das 1.5- bis 1.8-fache der Kopie**: Kopie,
  Zurücksetzen und ein vollständiger Neuaufbau des Nachbarschaftsindex
  über alle Kantenarten.

### Block 6 — suche

24.09.2026, `3.0.0-entwurf` (Stand `a51491d`), derselbe Container, ohne
Kanten. Median aus 7 Stichproben. Jede Suche prüft vorher ihr Ergebnis
gegen die Erwartung; nach einer Änderung wird nach dem NEUEN Wert gesucht,
und er muss genau den geänderten Knoten liefern.

| Messung | 500 | 2000 | 10 000 |
|---|---|---|---|
| Index bauen (Untergrenze) | 0.15 ms | 0.74 ms | 3.6 ms |
| Inhalt serialisieren + MD5 (Untergrenze) | 3.3 ms | 13.7 ms | 76 ms |
| Teilstring, warm | 0.019 ms | 0.059 ms | 0.48 ms |
| Platzhalter, warm | 0.023 ms | 0.080 ms | 0.50 ms |
| Prädikat | 0.081 ms | 0.32 ms | 1.8 ms |
| 1 Änderung allein | 0.70 ms | 0.70 ms | 0.80 ms |
| **1 Änderung, dann Teilstring** | **9.6 ms** | **31 ms** | **159 ms** |
| erste Suche nach Öffnen, Indexdatei da | 3.4 ms | 14.4 ms | 79 ms |
| erste Suche nach Öffnen, ohne Indexdatei | 4.6 ms | 16.8 ms | 85 ms |

Was daraus folgt:

- **Die Suche selbst ist schnell**, solange der Index warm ist: unter
  0.5 ms bei 10 000 Knoten. Das Prädikat, das keinen Index benutzt,
  kostet etwa das Vierfache und wächst linear.
- **Eine einzige Änderung macht die nächste Suche 200-mal teurer** als
  die Änderung selbst (159 ms gegen 0.8 ms bei 10 000). Jede Änderung
  verwirft den Index der ganzen Sammlung. Die nächste Suche prüft die
  veraltete Indexdatei über eine Prüfsumme, baut neu, berechnet die
  Prüfsumme für die neue Datei ein zweites Mal und schreibt sie mit
  fsync. Zweimal „Inhalt serialisieren + MD5“ (2 × 76 ms) erklärt fast
  die ganze Zeile.
- **Die Indexdatei spart nichts.** Nach dem Öffnen ist die erste Suche
  mit Datei nur 6 ms schneller als ohne (79 gegen 85 ms), weil ihre
  Gültigkeitsprüfung so viel kostet wie ein Neuaufbau samt Prüfsumme.
  Den Index aus dem Speicher zu bauen kostet 3.6 ms, die Datei zu prüfen
  76 ms: die Datei ist zwanzigmal teurer als das, was sie erspart.

## Umgesetzte Verbesserungen

Jede Zeile hier ist nach dem Umbau mit demselben Block gemessen, alte
Fassung gegen neue im selben Lauf, auf demselben Rechner.

### Hebel 1 — Feldindex nur im Speicher, nachgeführt statt verworfen

24.09.2026. `flatgraph/flatgraph.py` (3.0.0) gegen
`flatgraph/entwurf/flatgraph.py` (3.0.0-entwurf mit Hebel 1), Block 6.

| Messung | Bestand | 3.0.0 | Entwurf | Faktor |
|---|---|---|---|---|
| 1 Änderung, dann Teilstring | 500 | 8.9 ms | 1.1 ms | 8× |
| | 2000 | 31 ms | 1.0 ms | 30× |
| | 10 000 | **155 ms** | **0.67 ms** | **230×** |
| erste Suche nach Öffnen | 500 | 3.4 ms | 0.19 ms | 17× |
| | 2000 | 14.7 ms | 0.86 ms | 17× |
| | 10 000 | **73 ms** | **5.1 ms** | **14×** |
| Teilstring, warm | 10 000 | 0.41 ms | 0.48 ms | unverändert |
| 1 Änderung allein | 10 000 | 0.70 ms | 0.66 ms | unverändert |

- **Eine Suche nach einer Änderung kostet nichts Zusätzliches mehr:**
  „Änderung, dann Teilstring“ ist so teuer wie die Änderung allein, bei
  jeder Grösse. Vorher wuchs sie linear mit der Sammlung.
- **Die erste Suche nach dem Öffnen** kostet jetzt den Aufbau aus dem
  Speicher statt Prüfsumme plus fsync.
- **Nebenbei behoben:** Nach einem Rollback blieb der Index bis 3.0.0
  stehen; eine Suche fand danach den verworfenen Wert und den
  tatsächlichen nicht mehr. Die MD5-Prüfsumme ist mit der Indexdatei
  entfallen, damit auch `hashlib`.
- **Absicherung:** `tests/test_flatgraph_feldindex.py` vergleicht nach
  jedem von 400 zufälligen Schritten (alle Schreibwege, Transaktion mit
  Abschluss und Rollback, Aufräumen) jede Suche mit einem Durchgang über
  alle Knoten. Gegenprobe: ohne Austragen, ohne Eintragen oder ohne
  Verwerfen beim Rollback fällt sie jeweils in sechs bis neun Prüfungen,
  gegen 3.0.0 in sechs.

### H1 — ein Bestand, eine offene Instanz; Kantenschreiben ohne Nachlesen

24.09.2026. `flatgraph/flatgraph.py` (3.0.0) gegen
`flatgraph/entwurf/flatgraph.py` (mit H1), Blöcke 3 und 4, vier Kanten je
Knoten.

| Messung | Bestand | 3.0.0 | Entwurf | Änderung |
|---|---|---|---|---|
| `create_edge` | 2000 (8000 Kanten) | 54 ms | 36 ms | −33 % |
| | 10 000 (40 000 Kanten) | **297 ms** | **168 ms** | **−43 %** |
| 20 Kanten einzeln | 10 000 | 6.17 s | 3.47 s | −44 % |
| 20 Kanten in Transaktion | 10 000 | 480 ms | 339 ms | −29 % |
| Rollback nach 1 Änderung | 10 000 | 262 ms | 232 ms | −11 % |
| `create_node`, 1 Änderung in Transaktion | alle | | | unverändert |

- **Kantenschreiben liest nicht mehr nach** und baut den
  Nachbarschaftsindex nicht mehr neu. Beides diente nur dazu, Änderungen
  anderer Instanzen mitzunehmen, und die gibt es seit der Sperre nicht
  mehr.
- **Der Entwurf liegt jetzt unter der Untergrenze „Kantendatei
  umschreiben“** (168 gegen 222 ms bei 10 000), weil diese das Lesen
  enthält. Was bleibt, ist das Schreiben der ganzen Datei je Kantenart:
  das Format. Das Wachstum mit der Kantenzahl ist damit halbiert, aber
  nicht weg; weg ist es erst mit einem Journal (Hebel 3).
- **Der Rollback ist etwas billiger**, weil die Buchführung für fremde
  Änderungen nicht mehr mitkopiert und zurückgesetzt wird.

### Hebel 2 — Transaktion mit Undo-Log statt Kopie des Bestands

24.09.2026. `flatgraph/flatgraph.py` (3.0.0) gegen
`flatgraph/entwurf/flatgraph.py` (mit Hebel 2), Block 4, vier Kanten je
Knoten.

| Messung | Bestand | 3.0.0 | Entwurf | Faktor |
|---|---|---|---|---|
| 1 Änderung in Transaktion | 2000 | 23 ms | 1.1 ms | 20× |
| | 10 000 | **125 ms** | **0.55 ms** | **230×** |
| 100 Änderungen in Transaktion | 10 000 | 179 ms | 54 ms | 3.3× |
| 20 Kanten in Transaktion | 10 000 | 333 ms | 146 ms | 2.3× |
| Rollback nach 1 Änderung | 2000 | 29 ms | 0.015 ms | ~2000× |
| | 10 000 | **182 ms** | **0.014 ms** | **~13 000×** |

- **Eine Transaktion kostet nichts mehr, was mit dem Bestand wächst.**
  Im selben Lauf gemessen: 1 Änderung in Transaktion 0.55 ms, ohne
  Transaktion 0.61 ms; 100 Änderungen 54 ms in, 57 ms ohne Transaktion.
  Vorher war die Transaktion bei 10 000 Knoten dreimal langsamer als
  dieselben Änderungen einzeln.
- **Ein Rollback kostet Mikrosekunden**, weil er nur zurücksetzt, was die
  Transaktion berührt hat, statt den Bestand zurückzukopieren und den
  Nachbarschaftsindex über alle Kanten neu zu bauen.
- **Die Zeilen, die nur fsync messen, schwanken zwischen zwei Läufen**
  stark (1 Änderung ohne Transaktion bei 500 Knoten: 0.60 ms im einen,
  1.27 ms im anderen Lauf, derselbe Code). Verglichen wird deshalb jede
  Transaktionszeile mit „ohne Transaktion“ aus demselben Lauf; die
  Faktoren oben liegen weit über dieser Streuung.
- **Der Müllsammler wird in einer Transaktion abgewiesen**
  (`NichtInTransaktion`). Er schreibt sofort und verschiebt Anhänge, ein
  Rollback konnte das nie zurücknehmen.
- **Absicherung:** `tests/test_flatgraph_transaktion.py` vergleicht nach
  jedem Rollback den vollständigen Zustand über die öffentliche
  Schnittstelle, im Speicher und nach einem Neustart: jede Schreibmethode
  einzeln, verschachtelt, mit Protokoll, und 60 zufällige Folgen.
  Gegenprobe: jede der sechs Vormerk-Stellen einzeln entfernt lässt 1 bis
  7 Prüfungen fallen. Gegen 3.0.0 fällt dort der Zufallstest an der Suche
  — der Index-Fehler nach Rollback, den Hebel 1 behoben hat.

### H2 — eingebaute Threadsperre

24.09.2026. Was die Sperre kostet, gemessen mit Block 1 (die kürzesten
Aufrufe), 3.0.0 gegen Entwurf.

| Messung | Bestand | 3.0.0 | Entwurf | Aufschlag |
|---|---|---|---|---|
| `get_node` | 10 000 | 2.33 µs | 2.93 µs | +0.6 µs |
| `get_node readonly` | 10 000 | 0.36 µs | 0.80 µs | +0.44 µs (2.2×) |
| `list_nodes` | 10 000 | 18.0 ms | 18.4 ms | im Rauschen |
| `list_nodes readonly` | 10 000 | 1.33 ms | 1.34 ms | im Rauschen |

- **Rund 0.5 µs je Aufruf**, fast ganz für den zusätzlichen Python-Aufruf
  der Hülle, nicht für die Sperre selbst. Vorab geschätzt hatte ich unter
  0.1 µs — das war um den Faktor fünf zu niedrig.
- **Spürbar nur bei sehr vielen sehr kurzen Aufrufen**, etwa `get_node`
  mit `readonly=True` in einer engen Schleife. Wer das braucht, holt sich
  besser einmal `list_nodes(readonly=True)`. Ein Schalter, der die Sperre
  für reine Einzel-Thread-Anwender abschaltet, wäre möglich, ist aber
  nicht gebaut: er brächte die lautlose Voreinstellung zurück, sobald ihn
  jemand falsch setzt.
- **Absicherung:** `tests/test_flatgraph_threads.py`, 12 Prüfungen. Gegen
  3.0.0 fallen 8 in drei von drei Läufen: von 240 geschriebenen Knoten
  waren rund 68 da, Leser bekamen „dictionary changed size during
  iteration“, und ein Schreibvorgang während einer fremden Transaktion
  verschwand mit deren Rollback. Im Entwurf ohne die Sperre um die
  Methoden fallen dieselben 8; ohne die Sperre um die Transaktion fallen
  4 — zwei zum verschwundenen fremden Schreibvorgang, zwei zu doppelt
  vergebenen Nummern —, jeweils in allen Läufen.

### Block 5 — nachbarschaft

24.09.2026, `3.0.0-entwurf` (mit H1, H2, Hebel 1 und 2), derselbe Container.
Eine Kantenart, 1 / 4 / 16 Kanten je Knoten zu zufälligen Zielen, Median
aus 7 Stichproben. Jedes Ergebnis wird vorher gegen eine eigene Rechnung
aus `list_edges` geprüft, ohne flatgraphs Index.

| Messung | Dichte | 2000 Knoten | 10 000 Knoten |
|---|---|---|---|
| `get_connected` | 1 / 4 / 16 | 0.9 / 1.6 / 4.7 µs | 1.3 / 1.6 / 4.8 µs |
| `get_connected_edges` | 1 / 4 / 16 | 3.3 / 9.2 / 33 µs | 3.8 / 8.7 / 32 µs |
| `traverse`, Tiefe 2 | 1 / 4 / 16 | 3.4 / 12 / 105 µs | 2.9 / 12 / 111 µs |
| `traverse`, Tiefe 3 | 1 / 4 / 16 | 4.2 / 48 µs / 2.1 ms | 3.9 / 49 µs / 3.2 ms |
| `traverse`, unbegrenzt | 1 / 4 / 16 | 0.07 / 6.2 / 17.6 ms | 0.2 / 47 / 146 ms |
| `collect_related`, 2 Stufen | 1 / 4 / 16 | 3.4 / 12 / 110 µs | 3.5 / 12 / 114 µs |
| `list_edges` | 1 / 4 / 16 | 4.5 / 15 / 67 ms | 20 / 85 / 380 ms |

Was daraus folgt:

- **Nachbarschaft hängt nicht von der Grösse des Bestands ab**, nur davon,
  wie viel man erreicht. `get_connected`, `traverse` mit fester Tiefe und
  `collect_related` kosten bei 2000 und 10 000 Knoten dasselbe. Sie wachsen
  mit der Zahl der erreichten Knoten: bei 16 Kanten je Knoten sind das in
  Tiefe 3 bis zu 4096, und dann kostet es Millisekunden.
- **Unbegrenzt traversieren heisst: alles Erreichbare anfassen.** Bei einem
  dicht verbundenen Bestand ist das fast der ganze Graph (146 ms bei
  160 000 Kanten).
- **`list_edges` ist die teuerste Operation**, rund 2.4 µs je Kante, weil
  jede Kante tief kopiert wird. Wer den Graphen durchsuchen will, darf
  nicht über `list_edges` gehen.
- **Mit Kanten kostet die Nachbarschaft sechs- bis siebenmal mehr**
  (`get_connected_edges`), ebenfalls wegen der Kopie jeder Kante.

**Für die geplante Mustersuche (Issue #33) heisst das:** Sie muss über den
Nachbarschaftsindex laufen, nie über `list_edges`, und sie muss bei dem
Knoten anfangen, der am wenigsten erreicht — einem festen Knoten wie
`raeume/R1` statt einer ganzen Sammlung. Dann kostet sie, was sie findet,
und nicht, was im Bestand steht.

### Speicherform 3 — Fächer statt einer Datei je Knoten (Issue #32)

24.09.2026. Entwurf vor den Fächern (Speicherform 2) gegen Entwurf mit
Fächern zu 25 (Speicherform 3), Blöcke 2 und 3, im selben Lauf.

| Messung | Bestand | Form 2 | Form 3 | |
|---|---|---|---|---|
| öffnen, warm | 10 000 | 140 ms | **19 ms** | 7× |
| öffnen, kalt | 10 000 | 776 ms | **55 ms** | 14× |
| öffnen, kalt, 4 Kanten/Knoten | 10 000 | 871 ms | **343 ms** | 2.5× |
| **`create_edge`, 40 000 Kanten** | 10 000 | **145 ms** | **0.97 ms** | **150×** |
| `create_node` / `update_node` | 10 000 | 0.97 / 0.85 ms | 0.99 / 0.89 ms | unverändert |

Und erstmals echt bei **100 000 Knoten** (Form 3): öffnen kalt 0.61 s ohne
Kanten; mit 400 000 Kanten 2.4 s warm, 4.8 s kalt. Die Hochrechnung für
Form 2 lag bei ~11 s kalt.

- **Kante anlegen hängt nicht mehr von der Kantenzahl ab** — sie schreibt
  nur ihr Fach. Das war der grösste verbliebene Engpass.
- **Öffnen liest 25-mal weniger Dateien.** Ohne Kanten fällt es dadurch
  auf ein Vierzehntel.
- **Mit vielen Kanten dominiert jetzt flatgraphs eigene Arbeit beim
  Öffnen**, nicht mehr das Dateisystem: im Dateiversuch dauerte das reine
  Lesen derselben Kanten unter einer Sekunde. Der Rest ist der Aufbau des
  Nachbarschaftsindex und eine Übersetzung alter Feldnamen
  (`_translate_legacy_edge`), die bei JEDEM Öffnen für jede Kante läuft,
  obwohl sie nur beim Umzug nötig wäre. Naheliegender nächster Schritt.

### Absichtsdatei — Transaktion auch auf der Platte alles oder nichts (Issue #32)

24.09.2026. Entwurf vor gegen Entwurf mit Absichtsdatei, Block 4, 4 Kanten
je Knoten, im selben Lauf nacheinander.

| Messung | Bestand | vorher | nachher |
|---|---|---|---|
| 1 Änderung in Transaktion | 2 000 / 10 000 | 1.26 / 1.21 ms | 1.05 / 1.46 ms |
| 100 Änderungen in Transaktion | 2 000 / 10 000 | 58.5 / 97.7 ms | 50.2 / 78.0 ms |
| 20 Kanten in Transaktion | 2 000 / 10 000 | 1.77 / 2.39 ms | 2.66 / 2.69 ms |
| Rollback nach 1 Änderung | 2 000 / 10 000 | 12.4 / 13.5 µs | 16.1 / 15.6 µs |

- **Keine messbaren Mehrkosten.** Die Unterschiede in beide Richtungen
  liegen in der Streuung dieser Platte (Max bis doppelt so hoch wie der
  Median). Eine Transaktion über ein Fach schreibt wie vorher direkt.
- **Über mehrere Fächer eher billiger:** die `.neu`-Dateien bekommen je
  ein fsync, das Verzeichnis aber nur einmal statt einmal je Fach. Dazu
  kommen die Absichtsdatei und ihr Löschen — zusammen weniger, als die
  gesparten Verzeichnis-fsyncs kosten.
- Rollback: +3 µs, absolut bedeutungslos; nicht weiter verfolgt.

