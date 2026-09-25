# Graphatlas

Modellierungswerkzeug für Herstellprozesse auf flatgraph: Knoten- und
Kantentypen als erstklassige, konfigurierbare Objekte; Prozesse als
durchquerbare Struktur statt Einzel-Flowcharts. Konzept: `KONZEPT.md`.

Eigenständige Anwendung — nicht pDMS, nicht Teil der Bibliothek. Sie
benutzt flatgraph wie jeder andere Aufrufer, über die öffentliche
Schnittstelle, mit `fach_groesse=EINE_DATEI`.

## Warum dieser Ordner hier liegt

Graphatlas bekommt ein eigenes Repo. Anlegen konnte es die Sitzung vom
25.09.2026 nicht (keine Berechtigung). Bis dahin liegt es hier, und
nichts ausserhalb von `graphatlas/` gehört dazu.

Umzug, mit Geschichte:

    git subtree split --prefix=graphatlas -b graphatlas-export
    # im neuen, leeren Repo:
    git pull <pfad-zu-flatgraphdb> graphatlas-export

Danach `graphatlas/` hier löschen. flatgraph wird dort als Abhängigkeit
eingebunden, nicht kopiert.
