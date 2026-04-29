# FlatGraphDB — Persönliches Handbuch

> Stand: April 2026  
> Für den täglichen Gebrauch mit der PDMS-App (pdms_app.py)

---

## Was ist das hier?

FlatGraphDB ist deine selbst gebaute, filebasierte Graph-Datenbank. Sie lebt in
einem einzigen Python-Modul (`flatgraph.py`) und braucht keine externe Software.
Daten werden als lesbare JSON-Dateien auf der Platte gespeichert. Du kannst sie
mit einem Texteditor öffnen, in Git versionieren, per rsync sichern oder einfach
in einen anderen Ordner kopieren — fertig.

Die PDMS-App (`pdms_app.py`) ist eine Flask-Webanwendung, die auf FlatGraphDB
aufbaut und Equipment, Dokumente, Issues und Logbuch-Einträge verwaltet.

---

## Schnellstart

### App starten

```bash
cd /home/user/flatgraphdb
python pdms_app.py
```

Browser: `http://localhost:5001`

### Testdaten generieren

```bash
python gen_testnodes.py --nodes 5000 --db _test_db --clean
```

Mit GC-Benchmark:

```bash
python gen_testnodes.py --nodes 5000 --db _test_db --clean --gc
```

---

## Datenbankordner verstehen

```
_pdms_db/
├── equipments.json          ← alle Anlagen
├── equipments_temp.json     ← geänderte Nodes (seit letztem Full-Write)
├── documents.json
├── documents_temp.json
├── issues.json
├── logbook.json
├── edges/
│   ├── hat_issue.json       ← Edge-Typ-Datei
│   ├── object_link.json
│   └── hat_logbuch.json
└── index/                   ← optional (future)
```

**Faustregel:** `_temp.json`-Dateien entstehen bei normalen Writes.
Die GC (`MaintenanceEngine`) kompaktiert sie beim Housekeeping zurück in die
Haupt-Collection. Du kannst `_temp.json`-Dateien jederzeit bedenkenlos löschen —
beim nächsten Start fehlen dann die letzten Änderungen, die noch nicht
kompaktiert wurden. Deshalb: regelmäßig GC laufen lassen.

---

## Wichtige Konzepte auf einen Blick

### Nodes und Collections

Jedes Objekt ist ein **Node** in einer **Collection**.

```python
from flatgraph import FlatGraphDB

db = FlatGraphDB("_meine_db")

# Node anlegen
db.create_node("equipments", "EQ-00001", {
    "description": "Pumpe Halle 1",
    "status": "AKTIV",
})

# Node lesen
node = db.get_node("equipments/EQ-00001")
print(node["description"])

# Node updaten (nur geänderte Felder angeben)
db.update_node("equipments", "EQ-00001", {"status": "INAKTIV"})

# Node weich löschen (bleibt in DB, _deleted=True)
db.soft_delete("equipments", "EQ-00001")

# Node suchen
aktive = db.find_nodes("equipments", {"status": "AKTIV"})
```

### Referenz-Format

Nodes werden immer als `"collection/node_id"` referenziert:

```
"equipments/EQ-00001"
"documents/DOC-00042"
"issues/ISS-00007"
"logbook/LOG-00123"
```

Dieses Format ist in Edges und im Feld `linked_ref` zu sehen.

### Edges (Beziehungen)

Beziehungen zwischen Nodes heißen Edges. Sie sind gerichtet und haben einen Typ.

```python
# Equipment → Issue verbinden
db.create_edge("equipments/EQ-00001", "issues/ISS-00001", "hat_issue")

# Dokument → Equipment verknüpfen
db.create_edge("documents/DOC-00001", "equipments/EQ-00001", "object_link")

# Verbundene Nodes lesen
verbundene = db.get_connected("equipments/EQ-00001", direction="out")
# → {"equipments/EQ-00001 → issues/ISS-00001": {"edge_type": "hat_issue"}, ...}
```

`direction="in"`: wer zeigt auf diesen Node  
`direction="out"`: wohin zeigt dieser Node  
`direction="both"`: beides

### Transaktionen (bulk writes)

Ohne Transaktion: jede Änderung schreibt sofort auf die Platte (langsam bei vielen Nodes).  
Mit Transaktion: alles im RAM puffern, einmal am Ende schreiben (25× schneller).

```python
with db.transaction():
    for i in range(1000):
        db.create_node("equipments", f"EQ-{i:05d}", {...})
# Erst hier wird alles auf Platte geschrieben
```

Bei Exception innerhalb des `with`-Blocks: automatischer Rollback, nichts wird gespeichert.

---

## PDMS-App Datenmodell

### Collections

| Collection    | Primärschlüssel | Zweck                         |
|---------------|-----------------|-------------------------------|
| `equipments`  | `EQ-{nr}`       | Anlagen, Maschinen, Geräte    |
| `documents`   | `DOC-{nr}`      | Technische Dokumente          |
| `issues`      | `ISS-{nr}`      | Störungen, Wartungsaufträge   |
| `logbook`     | `LOG-{nr}`      | Freitext-Einträge pro Objekt  |

### Edge-Typen

| Edge-Typ      | Von           | Zu            | Bedeutung                       |
|---------------|---------------|---------------|---------------------------------|
| `hat_issue`   | Equipment/Dok | Issue         | Objekt hat offene Aufgabe       |
| `object_link` | Dokument      | Equipment     | Dokument gehört zu Anlage       |
| `hat_logbuch` | Equipment     | Logbuch-Entry | Equipment hat Logbucheinträge   |

### Felder: Equipment

```
description       Bezeichnung
category          MECHANISCH | ELEKTRISCH | INSTRUMENT | ROHRLEITG | BEHAELTER
status            AKTIV | INAKTIV | STILLGELEGT
manufacturer      Hersteller
model             Typenbezeichnung
serial_nr         Seriennummer
construction_year Baujahr (YYYY)
cost_center       Kostenstelle
created_by        Ersteller
created_at        ISO-Timestamp
changed_at        ISO-Timestamp
```

### Felder: Dokument

```
title             Dokumententitel
doc_type          MAN | WAR | PRO | ZEI | SDB | PRF
doc_part          Teilnummer (z.B. "001")
status            WK (in Arbeit) | FR (freigegeben) | OB (obsolet)
language          DE | EN | ...
file_path         Pfad zur Datei (optional)
```

### Felder: Issue

```
title             Kurzbeschreibung
issue_type        STOERUNG | WARTUNG | AENDERUNG | PRUEFUNG
status            OFFEN | IN_ARBEIT | ERLEDIGT
priority          HOCH | MITTEL | NIEDRIG
description       Detailbeschreibung (optional)
due_date          Fälligkeitsdatum YYYY-MM-DD (optional)
linked_ref        z.B. "equipments/EQ-00001"
created_by        Ersteller
created_at        ISO-Timestamp
changed_at        ISO-Timestamp
```

### Felder: Logbuch

```
entry_date        Datum YYYY-MM-DD
text              Freitext
ref               Referenz-Objekt (z.B. "equipments/EQ-00001")
created_by        Ersteller
created_at        ISO-Timestamp
```

---

## App-Routen Übersicht

| Route                                 | Methode | Funktion                               |
|---------------------------------------|---------|----------------------------------------|
| `/`                                   | GET     | Dashboard                              |
| `/equipments`                         | GET     | Equipment-Liste                        |
| `/equipment/<ref>`                    | GET     | Equipment-Detail                       |
| `/equipment/new`                      | GET/POST| Neue Anlage anlegen                    |
| `/equipment/<ref>/edit`               | GET/POST| Anlage bearbeiten                      |
| `/equipment/<ref>/issue/new`          | POST    | Issue für Anlage anlegen               |
| `/equipment/<ref>/eq-status`          | POST    | Equipment-Status weiterschalten        |
| `/documents`                          | GET     | Dokumentenliste                        |
| `/document/<ref>`                     | GET     | Dokument-Detail                        |
| `/document/new`                       | GET/POST| Neues Dokument anlegen                 |
| `/document/<ref>/issue/new`           | POST    | Issue für Dokument anlegen             |
| `/issues`                             | GET     | Issues-Übersicht (filter möglich)      |
| `/issue/<ref>`                        | GET     | Issue-Detail                           |
| `/issue/<ref>/status`                 | POST    | Issue-Status weiterschalten            |
| `/issue/<ref>/ics`                    | GET     | .ics-Kalender-Download                 |
| `/settings`                           | GET/POST| App-Einstellungen                      |
| `/gc`                                 | POST    | Garbage Collection starten             |

---

## Equipment-Status Workflow

```
AKTIV → INAKTIV → STILLGELEGT
```

Auf der Equipment-Detail-Seite: Button "→ INAKTIV" / "→ STILLGELEGT".  
Nach STILLGELEGT kein weiterer Button (Endstatus).

---

## Issue-Status Workflow

```
OFFEN → IN_ARBEIT → ERLEDIGT
```

Auf der Issue-Detail-Seite: Button "In Arbeit setzen" / "Erledigen".  
Farbcodierung: Rot (OFFEN) → Orange (IN_ARBEIT) → Grün (ERLEDIGT).

---

## Housekeeping (Garbage Collection)

Die GC bereinigt soft-deleted Nodes und kompaktiert `_temp.json`-Dateien zurück
in die Haupt-Collection.

**In der App:** Einstellungen → Wartung → GC starten  
**Per Script:**

```python
from flatgraph import MaintenanceEngine
gc = MaintenanceEngine("_pdms_db")
stats = gc.run_garbage_collection(verbose=True)
print(stats)
```

**Empfehlung:** Nach größeren Bulk-Importen oder Löschaktionen GC einmal laufen lassen.

---

## Daten direkt bearbeiten (Notfall)

Da alles JSON ist, kann man notfalls mit einem Texteditor eingreifen:

1. App stoppen
2. Datei in `_pdms_db/` öffnen und editieren
3. App neu starten

**Achtung:** `_temp.json`-Dateien haben Vorrang vor der Haupt-Collection.
Wenn du `equipments.json` editierst, aber `equipments_temp.json` existiert,
werden deine manuellen Änderungen an der Haupt-Datei beim nächsten Reload
**überschrieben** durch die Einträge in `_temp.json`. Deshalb:

- Entweder beide Dateien editieren
- Oder `_temp.json` löschen (verliert letzte Änderungen!)
- Oder GC laufen lassen, dann nur `equipments.json` editieren (kein `_temp.json` mehr)

---

## Backup

```bash
cp -r _pdms_db _pdms_db_backup_$(date +%Y%m%d)
```

Oder per Git (empfohlen):

```bash
cd _pdms_db && git init && git add . && git commit -m "backup $(date)"
```

---

## Performance-Richtwerte

| Szenario                        | Wert (Messung mit 5k Nodes) |
|---------------------------------|-----------------------------|
| Kaltstart (DB von Disk laden)   | ~0.06s                      |
| list_nodes() (alle Equipment)   | ~0.001s                     |
| find_nodes() mit 1 Filter       | ~0.001s                     |
| find_nodes() mit 2 Filtern      | ~0.001s                     |
| get_node() einzeln              | < 0.1ms                     |
| update_node() ohne Transaktion  | ~80ms                       |
| update_node() in Transaktion    | ~2ms (40× schneller)        |
| RAM für 5k Nodes                | ~8 MB                       |
| RAM für 25k Nodes               | ~31 MB                      |
| Praktische Obergrenze           | ~25.000 Nodes total         |

---

## Grenzen die du kennen solltest

**Multi-Process Writes:** Wenn zwei Prozesse gleichzeitig schreiben, können
Daten verloren gehen. Die App ist für einen einzelnen Nutzer ausgelegt — kein Problem.
Für Mehrbenutzerbetrieb müsste ein TOCTOU-sicherer FileLocker implementiert werden (Issue #1).

**Keine echte Query-Sprache:** Suche läuft über `find_nodes()` mit einem Felder-Dict.
Keine JOINs, keine komplexen WHERE-Klauseln. Für komplexe Auswertungen: Python drüber schreiben.

**Keine automatische Referenzintegrität:** Wenn ein Node gelöscht wird, bleiben
Edges zu ihm bestehen. GC räumt dangling Edges auf.

**Sortierung in Listen:** Die App sortiert aktuell nicht dynamisch per Klick —
nur die Standardreihenfolge (Einlagereihenfolge). Geplant in Issue #5.

---

## Häufige Aufgaben

### Neue Collection hinzufügen

Einfach `create_node` mit dem neuen Collection-Namen aufrufen — die Collection
entsteht automatisch:

```python
db.create_node("spare_parts", "SP-00001", {"description": "Dichtung DN50"})
```

### Node-IDs vergeben (automatisch)

```python
from flatgraph import FlatGraphDB

db = FlatGraphDB("_pdms_db")
next_id = db.next_id("equipments", prefix="EQ-", padding=5)
# → "EQ-00042" (wenn schon 41 Equipments existieren)
```

### Alle Issues zu einem Equipment finden

```python
db = FlatGraphDB("_pdms_db")
edges = db.get_connected("equipments/EQ-00001", direction="out", edge_type="hat_issue")
issue_refs = [ref.split(" → ")[1] for ref in edges]
issues = [db.get_node(ref) for ref in issue_refs]
```

### Issues nach Priorität filtern

```python
offene_hohe = db.find_nodes("issues", {"status": "OFFEN", "priority": "HOCH"})
```

### Logbuch-Einträge zu einem Equipment

```python
edges = db.get_connected("equipments/EQ-00001", direction="out", edge_type="hat_logbuch")
log_refs = [ref.split(" → ")[1] for ref in edges]
entries = [db.get_node(ref) for ref in log_refs]
entries.sort(key=lambda e: e.get("entry_date", ""), reverse=True)
```

---

## Geplante Features (Roadmap)

| Issue | Titel                                      | Prio  |
|-------|--------------------------------------------|-------|
| #1    | TOCTOU-sicherer FileLocker                 | Hoch  |
| #2    | Nested Transactions                        | Mittel|
| #5    | Tabellensortierung per Klick               | Mittel|
| #10   | Objektverknüpfungen in Detailansichten     | Hoch  |
| #14   | vault_text — Longtext-Felder auslagern     | Mittel|
| #15   | Read-only Views (readonly=True)            | Niedrig|

---

## Datei-Checkliste

```
flatgraphdb/
├── flatgraph.py         ← Datenbank-Engine (nicht anfassen, außer bewusst)
├── pdms_app.py          ← Flask-App (hier neue Routes/Features)
├── gen_testnodes.py     ← Performance-Tests und Testdaten
├── readme               ← Technische Dokumentation
├── manual.md            ← Dieses Handbuch
├── .gitignore           ← _test_db/, _pdms_db/ etc. ignoriert
└── _pdms_db/            ← Deine Datenbankdateien (nicht in Git!)
```

---

*Erstellt April 2026*
