# flatgraph — Regeln für die Arbeit an diesem Repo

Eine Graph-Datenbank in einer Python-Datei (`flatgraph.py`), dateibasiert,
ohne fremde Datenbank. Ziel: eine Bibliothek, die jemand **ohne pDMS**
ernsthaft einsetzen kann. pDMS (`bireggbaum-beep/homedms`) ist ein
Anwender, kein Massstab.

- **Führend ist `VERTRAG.md`**: was zugesagt wird und was nicht. Jede
  Verhaltensänderung zieht ihn nach.
- **Offene Punkte sind Issues in `bireggbaum-beep/homedms`**, Liste und
  Reihenfolge in #36.
- **Alles auf Deutsch**: Kommentare, Tests, Commits, Antworten. Kommentare
  sagen warum, nicht was. Vorhandene englische Bezeichner bleiben.

## Arbeitsweise

- **Nachgemessen statt vermutet.** Zahlen zuerst in `VERTRAG.md` §5 und
  `bench/README.md` nachschlagen; gemessen wird nur, wo keine steht.
  Schätzungen heissen Schätzungen.
- **Gegenprobe.** Ein Test muss nachweislich fallen, wenn man die geprüfte
  Eigenschaft entfernt: mutierte Kopie, dann
  `PYTHONDONTWRITEBYTECODE=1 FLATGRAPH_DATEI=kopie.py python tests/…`.
  Ein Test, der nicht fallen kann, wird gelöscht.
- **Ein reiner Fehler wird behoben, nicht bewertet.** Alles andere:
  Einschätzung, Aufwand 1–5, Empfehlung, was dagegen spricht — dann
  entscheidet der Benutzer.
- **Kurz antworten.** Keine Erkundung ohne Ziel, keine ungefragten
  Zusatzarbeiten.

## Harte Regeln

- **Speicher ändert sich erst, wenn die Platte es aufnehmen kann.**
- **Jeder Schreibvorgang** über Arbeitsdatei daneben, `fsync`,
  `os.replace`; keine Datei wird an Ort und Stelle verändert.
- **Speicherform** (`SPEICHERFORM`) hat eine Nummer; ein Umzug hat keinen
  Rückweg und steht im Vertrag.
- Keine Abhängigkeiten ausser der Standardbibliothek.

## Testen

```bash
for t in tests/test_flatgraph_*.py; do python "$t" | tail -1; done
python -m py_compile tests/<datei>.py   # nach jedem Bearbeiten
```

Selbstgeschrieben, kein pytest: jede Suite endet mit „n/n Checks
bestanden“, Exit 1 bei einem Fehlschlag. Neue Prüfnamen sagen, was gelten
soll. `bench/lauf.py` misst; neue Zahlen gehören mit Datum in den Vertrag.
