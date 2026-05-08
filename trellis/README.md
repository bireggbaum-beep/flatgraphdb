# Trellis

Ein lokales Werkstatt-Tool zur Modellierung zustandsbasierter Abhängigkeitsnetze
in Q&V-Großprojekten — auf flatgraph aufgesetzt.

## Wofür

Q&V-Projekte (Qualifizierung & Validierung) leben von Voraussetzungen: bevor
eine PQ einer Linie starten kann, müssen IQ und OQ aller Equipments
abgeschlossen sein, das Spec-Setting muss einen bestimmten Reifegrad haben,
Trainings müssen erfolgt sein. Diese Vertragsketten werden in der Praxis in
Excel verwaltet und sind mit jeder neuen Iteration brüchiger.

Trellis modelliert sie als **getypten Graph**: Phasen, Equipments, Linien,
Prozesse, Produkte, Dokumente, … sind Knoten; ihre Voraussetzungen sind
Kanten mit `required_status` und `is_blocker`. Was wann von wem in welchem
Zustand verlangt wird, steht im Modell — explizit, durchsuchbar, diff-bar.

## Zwei Axiome

**Wachstums-Axiom.** Alles, was sich verändern kann — Typen, Stati,
Pflicht-Kanten, Templates — ist editierbar zur Laufzeit, niemals hartcodiert.
Das Modell wächst beim Modellieren. Excel würde ein Schema vorgeben; Trellis
folgt dem Denken.

**Robustheits-Axiom.** Was Trellis kann, kann es bulletproof. Atomare Writes
(transaction-basiert), explizite Fehler statt stillem Datenverlust, kein
Halb-Feature. Lieber weniger Funktionen, dafür ohne Haken.

Diese beiden Axiome sind kein Widerspruch, aber sie haben eine klare
Arbeitsteilung: das Tool blockiert nur dort, wo Daten kaputt gehen würden
(unbekannter Typ, gebrochene Referenzen, API-Missbrauch). Alles, was nur
Vokabular ist (Stati, neue Vertragstexte), geht durch — die Liste in
`types.yaml` ist Vorschlags-Korpus für Autocomplete, nicht Gate.

## Datenmodell

Zwei klare Edge-Kategorien:

- **strukturell** — konstitutive Verbindungen ohne Metadaten:
  `Phase --betrifft--> Equipment`, `Linie --besteht_aus--> Equipment`,
  `Prozess --stellt_her--> Produkt`. Im UI als Capacities-artige Pflicht-
  Felder dargestellt.

- **vertrag** — Voraussetzungen mit `required_status` und `is_blocker`:
  `PQ --requires--> IQ-Equipment-X` mit `required_status="Abgeschlossen"`,
  `is_blocker=True`. Im UI als eigener "Voraussetzungen"-Abschnitt.

Jeder Knotentyp deklariert in `types.yaml`:

- erlaubte `statuses` (wachsen)
- `fields` (Datenfelder)
- `pflicht_kanten` — strukturelle Verbindungen, die beim Anlegen verlangt sind

Knoten ohne vollständige Pflicht-Kanten sind **Stubs**: gültig, aber
unfertig. Das Tool markiert sie visuell, damit du sie später nachziehen
kannst, ohne im Modellier-Flow gebremst zu werden.

## Projektstruktur auf der Platte

```
mein_projekt/
  types.yaml          ← Quelle der Wahrheit für Typen (hand-edit oder UI)
  data/               ← flatgraph-Datenbank
    datenbank/...
    vault/...
```

## Schnellstart (Backend)

```python
from trellis import TrellisDB

db = TrellisDB("./mein_projekt")

# Equipment anlegen (kein Pflicht-Kanten)
eq_ref = db.create_node("Equipment", {"name": "Pumpe X-2025"})

# Linie braucht produziert + besteht_aus zur Anlage
prd = db.create_node("Produkt",  {"name": "Tablette 10mg"})
lin = db.create_node("Linie",    {"name": "Linie A"},
                     pflicht_kanten_targets={
                         "produziert":  [prd],
                         "besteht_aus": [eq_ref],
                     })

# Phase mit Bezugsobjekt
pq = db.create_node("Phase",
                    {"name": "PQ Linie A", "phasen_art": "PQ",
                     "current_status": "Geplant"},
                    pflicht_kanten_targets={"betrifft": [lin]})

# IQ als eigene Phase, dann Vertrag PQ → IQ
iq = db.create_node("Phase",
                    {"name": "IQ Pumpe X-2025", "phasen_art": "IQ",
                     "current_status": "Geplant"},
                    pflicht_kanten_targets={"betrifft": [eq_ref]})
db.add_contract(pq, iq, required_status="Abgeschlossen", is_blocker=True)

# Inspector-Daten für die Detailansicht
print(db.inspector(pq))
```

## Demo-Datensatz

Zum Ausprobieren mit realistisch-aussehenden Daten:

```
python3 -m trellis init /tmp/proj
python3 -m trellis seed /tmp/proj
python3 -m trellis serve /tmp/proj
```

Der Seed legt ein vollständiges Q&V-Szenario an (Tablettenlinie mit fünf
Equipments, je vier Phasen, plus eine kombinierte IQ-OQ, Prozess­validierung,
Linie-PQ, Spec-Setting-Meilenstein, Dokumente, Material, Software, Training,
Medium) und deckt alle UI-Fälle ab: Stub-Knoten, ein "Pausiert"-Status der
nicht in `types.yaml` deklariert ist, satisfiable und unsatisfiable Verträge
mit und ohne Blocker, mehrhopfige Readiness-Kaskaden. Re-Seed mit `--force`.

## Status

Backend (`config`, `core`, `readiness`) plus UI (`app` / `routes` / `views` /
`forms` plus Jinja-Templates und HTMX) — drei Etappen, ~3.900 Zeilen.
