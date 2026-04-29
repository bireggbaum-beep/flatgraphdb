# HomeDMS — Plan für nächste Session

## Pickup-Info (kalt einsteigen)

- **Repo:** `bireggbaum-beep/flatgraphdb`, Branch `feature/home-dms`
  (wird später nach `homedms` umgezogen — am Laptop)
- **Datei:** `/home/user/flatgraphdb/dms_app.py` (single-file Flask-App,
  ca. 1100 Zeilen)
- **Engine:** `flatgraph.py` (FlatGraphDB) — nicht anfassen, ist stable
- **Start:** `python dms_app.py` → http://127.0.0.1:5001
  (NICHT `localhost` — Windows-IPv6-Problem; `use_reloader=False`)
- **DB-Pfade:** `_dms_db/` neben App, mit `vault/`, `inbox/`, `vault_archive/`

## Was funktioniert (NICHT neu machen)

- 3-Spalten-Workspace-Shell (Sidebar / Liste / Detail) mit CSS-Design-Tokens
- Jinja `DictLoader` + `ChoiceLoader` für Template-Vererbung
- Status-Tabs (Alle / Aktiv / Archiv) mit Live-Counts
- Doc-Liste mit Status-Tabs, kompakte Rows, Selektion
- Detail-Panel: Felder-Grid, Notizen, Datei-Link, Bearbeiten/Löschen
- `/d/new` und `/d/<nid>/edit` mit allen Feldern
- Soft-Delete via FlatGraphDB
- Doc-zu-Doc-Verknüpfungen (Edge-Typ `doc_link`) — Backend-Logik ok,
  UX im Detail-Panel ist aber dürftig (siehe unten)
- Drag&Drop in Inbox (global) UND auf offenes Dokument (kontextabhängig)
- Suche: Enter im Suchfeld navigiert zu `/?q=...`, behält Filter
- Vault-File-Serving via `/vault/<filename>`

## Was kaputt / tot ist

### KRITISCH (Workflow-Fehler)

1. **Inbox → existierendes Dokument anhängen — fehlt komplett.**
   Aktuell: „Erfassen" auf einer Inbox-Datei führt nur zum Anlegen
   eines neuen Dokuments. Häufiger Fall ist aber: Dokument existiert
   bereits, der Scan kommt nachträglich rein. Es muss einen Picker
   geben: „diese Inbox-Datei → an Dokument X anhängen".

2. **Object-Links sind im Detail-Panel als 1-Zeilen-Dropdown.**
   Bei 5–10 Links pro Dokument ist das unbrauchbar. Die Edit-Seite
   (`/d/<nid>/edit`) braucht eine ordentliche Link-Sektion mit:
   - aktuelle Links (Liste mit Titel + Entfernen)
   - Picker für neue Links (suchbares Dropdown / Autocomplete,
     nicht eine Liste mit allen 200 Dokumenten)
   - mehrere Links auf einmal hinzufügen können

### TOTE NAVIGATIONSPUNKTE

3. **`/contacts`** — Sidebar-Link führt nach 404. Constants
   `CONTACT_CATEGORIES` liegen ungenutzt im Code. Nicht implementiert.

4. **`/reminders`** — Sidebar-Link führt nach 404. Constants
   `REMINDER_TYPES` liegen ungenutzt. `nav_counts()` liest schon
   die `reminders`-Collection aber es gibt keine Route.

5. **`/settings`** — Sidebar-Link führt nach 404. Field-Profiles
   und Webhook-Konfiguration sind nicht editierbar.

### LATENT IM CODE

6. **`fire_webhooks()`** ist als Helper definiert, wird aber NIE
   aufgerufen. Müsste bei `doc_create`, `doc_status_change`,
   `reminder_due` feuern.

7. **`get_field_profile(category)`** existiert, wird aber im Form
   nicht benutzt. Aktuell zeigt das Form ALLE Felder. Plan war:
   Form zeigt nur die Felder die im Profil der gewählten Kategorie
   stehen, optional Toggle „alle anzeigen".

## Konkrete Aufgaben (priorisiert)

### Aufgabe 1: Inbox-zu-Doc-Picker

Neue Route oder Erweiterung: jede Inbox-Reihe bekommt zwei Buttons:
- **„Neu erfassen"** (existiert)
- **„An Dokument anhängen"** → öffnet Picker (Modal oder eigene
  Seite `/inbox/attach?file=<fname>`) mit Such-Input und Doc-Liste,
  Klick auf Doc verschiebt Datei aus inbox/ nach vault/, setzt
  `vault_file` am Doc, redirect zurück.

Datenmodell-Änderung: keine. Nur Datei-Move + Update.

### Aufgabe 2: Multi-Link auf Edit-Seite

Im `doc_form`-Template eine neue `form-section` „Verknüpfte Dokumente":
- Liste der aktuellen Links mit ✕-Button (POST `/link/<eid>/delete`)
- Such-Input (JS-basiert, filtert Liste der Doc-Vorschläge live)
- Multi-Select oder „+ Hinzufügen"-Button pro Vorschlag
- Bulk-Add: alle ausgewählten in einem Request

Backend: `POST /d/<nid>/links/bulk` nimmt ein `targets[]`-Array,
legt mehrere Edges an, redirect zurück.

Im Detail-Panel kann das jetzige kleine Dropdown bleiben für den
Schnellfall (1 Link adden ohne Edit-Seite zu öffnen), aber
hauptsächlich passiert die Link-Pflege im Edit.

### Aufgabe 3: /contacts (CRUD)

Standard-Workflow analog zu Documents:
- Collection `contacts`, NID-Format `KON-{nr}` padding=5
- Felder: `name`, `category` (aus `CONTACT_CATEGORIES`), `email`,
  `phone`, `address`, `website`, `notes`, `created_at`, `changed_at`
- Routes: `/contacts` (Liste mit Filter), `/k/<nid>` (Detail),
  `/k/new`, `/k/<nid>/edit`, `/k/<nid>/delete`
- Im Doc-Form: `issuer` als Free-Text PLUS `issuer_ref` als
  Dropdown auf Kontakte (das Feld `issuer_ref` ist in
  `ALL_DOC_FIELDS` schon vorgesehen)
- Auf Kontakt-Detail: Liste aller Dokumente mit `issuer_ref` =
  diesem Kontakt

### Aufgabe 4: /reminders mit Webhook-Feuern

- Collection `reminders`, NID-Format `REM-{nr}`
- Felder: `title`, `remind_at` (YYYY-MM-DD), `reminder_type`
  (`ABLAUF` / `KUENDIGUNG` / `ZAHLUNG` / `CUSTOM`), `linked_doc`
  (DOC-Ref optional), `notes`, `fired` (bool), `fired_at`
- Routes: `/reminders` (Liste, Filter offen/erledigt),
  `/r/new`, `/r/<nid>/edit`, `/r/<nid>/delete`,
  `/r/<nid>/fire` (manuell Webhook auslösen + `fired=True`)
- **Auto-Fire:** beim GET auf `/` oder `/reminders` werden alle
  Reminders mit `remind_at <= today() and not fired` durchgegangen,
  `fire_webhooks("reminder_due", {...})` aufgerufen, dann
  `fired=True` gesetzt. (Kein Background-Thread — Polling reicht.)
- Auf Doc-Detail: Sektion „Reminders" mit Inline-Form zum Anlegen

### Aufgabe 5: /settings

Tab-Layout (oder Sektionen) auf einer Seite:

**a) Webhooks** — Collection `webhooks`, Felder:
   `url`, `event` (`doc_created` / `doc_status_changed` /
   `reminder_due` / `all`), `active` (bool), `description`
   - Liste, neu/edit/löschen
   - Test-Button: sendet Dummy-Payload an die URL

**b) Field-Profiles** — Collection `field_profiles`, Felder:
   `category` (aus `DOC_CATEGORIES`), `fields` (Liste von Feld-Keys
   aus `ALL_DOC_FIELDS`)
   - Pro Kategorie eine Reihe mit Checkbox-Liste aller Felder
   - Speichern aktualisiert die Collection
   - Im Doc-Form (`doc_form`-Template) wird `get_field_profile()`
     benutzt um nur relevante Felder zu zeigen
   - Toggle „Alle Felder anzeigen" als Override

### Aufgabe 6: Webhook-Calls einbauen

Nach Aufgaben 4 und 5 sind alle Bausteine da. Dann:
- In `doc_new()`: `fire_webhooks("doc_created", {nid, title, ...})`
- In `doc_edit()` bei Status-Wechsel: `fire_webhooks("doc_status_changed", ...)`
- In `/reminders` Auto-Fire: `fire_webhooks("reminder_due", ...)`

## Nicht-Ziele (NICHT machen)

- Keine Field-Profile-UI im Doc-Form bevor Settings-Page steht
- Kein Inline-Edit (Klick auf Feld → Input). Edit-Page reicht.
- Kein PDF-Viewer im Detail-Panel — `/vault/<file>` öffnen reicht.
- Keine Webhook-Retry-Logik. `try/except: pass` wie aktuell ist ok.
- Keine Layout-Änderungen am Workspace ohne expliziten Request.
- Keine Tests-Dateien, kein CI, keine README.

## Stil-Regeln

- Deutsche UI-Texte (alles bisher ist DE)
- Code-Kommentare sparsam — nur wo das WHY nicht offensichtlich ist
- Keine Emojis im Code, ausser im UI (📎 📥 📄)
- Kleine Edits, viele Commits — nicht 500 Zeilen am Stück
- Smoke-Tests via `app.test_client()` nach jedem Feature
- Bei jedem Push: `git push origin feature/home-dms`

## Verifikation pro Aufgabe

1. **Inbox-Attach:** PDF in Inbox legen → „An Dokument anhängen"
   → Doc auswählen → Datei ist im Vault, am Doc verlinkt, aus
   Inbox verschwunden.

2. **Multi-Link:** Edit-Seite eines Docs → 5 verschiedene Docs
   verlinken in einem Vorgang → Detail-Panel zeigt alle 5.

3. **Contacts:** Kontakt anlegen → in Doc-Form als `issuer_ref`
   wählen → auf Kontakt-Detail erscheint das Doc.

4. **Reminders:** Reminder mit `remind_at = today()` anlegen →
   Workspace neu laden → Webhook-URL sieht POST mit Payload →
   Reminder ist `fired=True`.

5. **Settings/Webhooks:** Webhook anlegen, aktiv, Test-Button →
   Ziel-URL bekommt Dummy-POST.

6. **Field-Profiles:** Profil für Kategorie „Versicherung" auf
   nur `[issuer, expires_at, cancellable_until]` setzen → Doc-Form
   für Versicherung zeigt nur diese drei plus immer-an-Felder.

## Reihenfolge-Empfehlung

Aufgabe 1 (Inbox-Attach) zuerst — kleinstes Stück, kritischster
Workflow-Fix. Dann 5b (Field-Profiles), weil sich danach die
Form-Struktur stabilisiert. Dann 3 (Contacts), 4 (Reminders),
5a (Webhooks), 6 (Webhook-Calls). Aufgabe 2 (Multi-Link) ist
unabhängig und kann jederzeit dazwischen.
