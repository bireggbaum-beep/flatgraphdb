"""
HomeDMS — Persönliches Dokumenten-Management auf FlatGraphDB
Run: pip install flask && python dms_app.py
"""

import os
import json
import shutil
import urllib.request as _urllib
from datetime import datetime, timezone
from flask import (Flask, render_template_string, request, redirect,
                   url_for, flash, send_file, Response)
from flatgraph import FlatGraphDB, MaintenanceEngine

app = Flask(__name__)
app.secret_key = "dms-secret-2026"
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

DB_ROOT   = os.path.join(os.path.dirname(__file__), "_dms_db")
VAULT_DIR = os.path.join(DB_ROOT, "vault")
INBOX_DIR = os.path.join(DB_ROOT, "inbox")
DEFAULT_USER = "User"

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "docx", "xlsx", "txt", "xml", "csv"}

DOC_CATEGORIES = [
    "Versicherung", "Steuer", "Bank", "Rechnung", "Behörde",
    "Gesundheit", "Wohnen", "Fahrzeug", "Arbeit", "Sonstiges",
]

DOC_TYPES = [
    "Rechnung", "Vertrag", "Police", "Bescheid", "Quittung",
    "Korrespondenz", "Ausweis", "Zertifikat", "Kontoauszug", "Sonstiges",
]

DOC_STATUS = {
    "AKTIV":      "#2a9d60",
    "ARCHIVIERT": "#888",
    "ABGELAUFEN": "#e74c3c",
    "STORNIERT":  "#aaa",
}
DOC_STATUS_FLOW = {
    "AKTIV":      "ARCHIVIERT",
    "ARCHIVIERT": None,
    "ABGELAUFEN": "ARCHIVIERT",
    "STORNIERT":  None,
}

CURRENCIES = ["CHF", "EUR", "USD", "GBP"]
LANGUAGES  = ["DE", "EN", "FR", "IT"]

REMINDER_TYPES = {
    "ABLAUF":     "Ablaufdatum",
    "KUENDIGUNG": "Kündigung",
    "ZAHLUNG":    "Zahlung",
    "CUSTOM":     "Individuell",
}

ALL_DOC_FIELDS = {
    "title":             "Titel",
    "issuer":            "Aussteller",
    "issuer_ref":        "Aussteller (Kontakt-Ref)",
    "category":          "Kategorie",
    "doc_type":          "Dokumenttyp",
    "doc_date":          "Dokumentdatum",
    "amount":            "Betrag",
    "currency":          "Währung",
    "due_date":          "Fälligkeit",
    "expires_at":        "Ablaufdatum",
    "cancellable_until": "Kündbar bis",
    "language":          "Sprache",
    "asn":               "Archivnummer (physisch)",
    "tags":              "Tags",
    "notes":             "Notizen",
}

DEFAULT_FIELD_PROFILES = {
    "Versicherung": ["issuer", "amount", "currency", "expires_at", "cancellable_until", "asn", "notes"],
    "Steuer":       ["doc_date", "amount", "currency", "asn", "notes"],
    "Bank":         ["issuer", "amount", "currency", "doc_date", "asn"],
    "Rechnung":     ["issuer", "amount", "currency", "doc_date", "due_date", "notes"],
    "Behörde":      ["issuer", "doc_date", "expires_at", "asn", "notes"],
    "Gesundheit":   ["issuer", "doc_date", "amount", "currency", "asn", "notes"],
    "Wohnen":       ["issuer", "doc_date", "amount", "currency", "cancellable_until", "asn", "notes"],
    "Fahrzeug":     ["issuer", "doc_date", "amount", "currency", "expires_at", "asn", "notes"],
    "Arbeit":       ["issuer", "doc_date", "expires_at", "asn", "notes"],
    "Sonstiges":    ["issuer", "doc_date", "amount", "currency", "asn", "notes"],
}

CONTACT_CATEGORIES = [
    "Versicherung", "Bank", "Behörde", "Telecom", "Versorger",
    "Gesundheit", "Arbeit", "Sonstiges",
]

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

_db = None


def get_db():
    global _db
    if _db is None:
        os.makedirs(VAULT_DIR, exist_ok=True)
        os.makedirs(INBOX_DIR, exist_ok=True)
        _db = FlatGraphDB(root_dir=DB_ROOT)
    return _db


@app.before_request
def _begin():
    get_db()._transaction_depth += 1


@app.teardown_request
def _end(exc):
    db = get_db()
    if exc is None:
        db._transaction_depth -= 1
        db._flush_pending_writes()
    else:
        db._transaction_depth -= 1
        db._dirty_nodes.clear()
        db._dirty_edges.clear()


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def now():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_vault_path(filename):
    safe = os.path.basename(filename)
    if not safe or safe != filename:
        raise ValueError("Ungültiger Dateiname.")
    return os.path.join(VAULT_DIR, safe)


def get_field_profile(category):
    db = get_db()
    profiles = db.list_nodes("field_profiles")
    for nid, p in profiles.items():
        if p.get("category") == category:
            return p.get("fields", DEFAULT_FIELD_PROFILES.get(category, []))
    return DEFAULT_FIELD_PROFILES.get(category, list(ALL_DOC_FIELDS.keys()))


def fire_webhooks(event, payload):
    db = get_db()
    if "webhooks" not in db.list_collections():
        return
    for wh in db.list_nodes("webhooks").values():
        if not wh.get("active"):
            continue
        if wh.get("event") not in (event, "all"):
            continue
        try:
            data = json.dumps(payload).encode()
            req  = _urllib.Request(wh["url"], data=data,
                       headers={"Content-Type": "application/json"}, method="POST")
            _urllib.urlopen(req, timeout=3)
        except Exception:
            pass


def inbox_files():
    if not os.path.isdir(INBOX_DIR):
        return []
    return sorted(f for f in os.listdir(INBOX_DIR)
                  if os.path.isfile(os.path.join(INBOX_DIR, f))
                  and not f.startswith("."))


# ---------------------------------------------------------------------------
# TEMPLATE ENGINE — DictLoader, design tokens, base shell
# ---------------------------------------------------------------------------

from jinja2 import DictLoader, ChoiceLoader
from flask import render_template

TEMPLATES = {}


def tpl(name, source):
    TEMPLATES[name] = source


app.jinja_loader = ChoiceLoader([DictLoader(TEMPLATES), app.jinja_loader])


tpl("base", r"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{% block title %}HomeDMS{% endblock %}</title>
<style>
:root {
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px;
  --sp-5: 20px; --sp-6: 24px; --sp-8: 32px;
  --fz-xs: 11px; --fz-sm: 12px; --fz-md: 13px; --fz-lg: 15px;
  --c-bg: #f0f2f5; --c-surface: #ffffff; --c-surface-2: #f7f8fa;
  --c-border: #d8dde5; --c-border-sub: #e8ebf0;
  --c-ink: #1a1d23; --c-ink-mute: #6b7280; --c-ink-soft: #98a0ab;
  --c-acc: #3b82f6; --c-acc-bg: #eaf2ff;
  --c-warn: #d97706; --c-warn-bg: #fef3c7;
  --c-bad: #dc2626;  --c-bad-bg: #fee2e2;
  --c-ok: #059669;   --c-ok-bg: #d1fae5;
  --c-sb-bg: #1e2330; --c-sb-bg-2: #252b3b;
  --c-sb-active: #2a3350; --c-sb-ink: #c8cfde; --c-sb-mute: #6b7280;
  --w-sidebar: 200px; --w-list: 340px; --h-topbar: 44px;
  --radius: 6px; --radius-sm: 4px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  height: 100vh; overflow: hidden;
  font-family: system-ui, -apple-system, sans-serif;
  font-size: var(--fz-md); color: var(--c-ink);
  background: var(--c-bg);
}
a { color: var(--c-acc); text-decoration: none; }
a:hover { text-decoration: underline; }

.shell { display: flex; height: 100vh; }

.sb {
  width: var(--w-sidebar); flex-shrink: 0;
  background: var(--c-sb-bg); color: var(--c-sb-ink);
  display: flex; flex-direction: column; overflow-y: auto;
}
.sb-logo {
  padding: var(--sp-3) var(--sp-4);
  font-size: var(--fz-md); font-weight: 700; color: #fff;
  border-bottom: 1px solid var(--c-sb-bg-2);
}
.sb-logo .accent { color: var(--c-acc); }
.sb-link {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-4);
  color: var(--c-sb-ink); font-size: var(--fz-sm);
  border-left: 2px solid transparent;
}
.sb-link:hover {
  background: var(--c-sb-bg-2); color: #fff; text-decoration: none;
}
.sb-link.active {
  background: var(--c-sb-active); color: #fff;
  border-left-color: var(--c-acc);
}
.sb-link .badge {
  margin-left: auto; background: var(--c-bad); color: #fff;
  border-radius: 10px; padding: 0 6px;
  font-size: 10px; font-weight: 700;
}
.sb-spacer { flex: 1; }
.sb-foot {
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--c-sb-bg-2);
  font-size: var(--fz-xs); color: var(--c-sb-mute);
}

.main { flex: 1; display: flex; flex-direction: column; min-width: 0; }

.topbar {
  height: var(--h-topbar); flex-shrink: 0;
  background: var(--c-surface);
  border-bottom: 1px solid var(--c-border);
  display: flex; align-items: center;
  padding: 0 var(--sp-4); gap: var(--sp-3);
}
.topbar h1 { font-size: var(--fz-md); font-weight: 600; }
.topbar-search { flex: 1; max-width: 420px; position: relative; }
.topbar-search input {
  width: 100%; padding: 5px 10px 5px 28px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: var(--fz-sm); background: var(--c-surface-2);
}
.topbar-search input:focus {
  outline: none; border-color: var(--c-acc); background: #fff;
}
.topbar-search::before {
  content: "⌕"; position: absolute;
  left: 9px; top: 4px; color: var(--c-ink-soft); font-size: 14px;
}

.ws { flex: 1; display: flex; min-height: 0; }
.ws-list {
  width: var(--w-list); flex-shrink: 0;
  background: var(--c-surface);
  border-right: 1px solid var(--c-border);
  display: flex; flex-direction: column; min-height: 0;
}
.ws-list-head {
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border-sub);
  display: flex; gap: var(--sp-2); align-items: center;
  font-size: var(--fz-xs); color: var(--c-ink-mute);
}
.ws-list-body { overflow-y: auto; flex: 1; }
.ws-detail {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column;
  background: var(--c-bg); overflow: hidden;
}

.drop-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(59,130,246,.1);
  border: 3px dashed var(--c-acc);
  display: none; align-items: center; justify-content: center;
  pointer-events: none;
}
.drop-overlay.active { display: flex; }
.drop-overlay-msg {
  background: #fff; padding: var(--sp-6) var(--sp-8);
  border-radius: var(--radius);
  font-size: var(--fz-lg); font-weight: 600; color: var(--c-acc);
  box-shadow: 0 4px 24px rgba(0,0,0,.15);
}

.btn {
  display: inline-flex; align-items: center; gap: var(--sp-1);
  padding: 5px 10px; border: 1px solid var(--c-border);
  border-radius: var(--radius-sm); background: var(--c-surface);
  font-size: var(--fz-sm); color: var(--c-ink);
  cursor: pointer; line-height: 1.4;
}
.btn:hover { background: var(--c-surface-2); text-decoration: none; }
.btn.primary {
  background: var(--c-acc); color: #fff; border-color: var(--c-acc);
}
.btn.primary:hover { background: #2563eb; border-color: #2563eb; }
.btn.danger { color: var(--c-bad); }
.btn.danger:hover { background: var(--c-bad-bg); }
.btn.sm { padding: 3px 8px; font-size: var(--fz-xs); }

.empty {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  flex: 1; text-align: center;
  color: var(--c-ink-mute);
  padding: var(--sp-8);
}
.empty .icon { font-size: 36px; margin-bottom: var(--sp-3); opacity: .4; }
.empty .title { font-size: var(--fz-md); margin-bottom: var(--sp-1); }
.empty .hint { font-size: var(--fz-sm); color: var(--c-ink-soft); }

.flash {
  padding: var(--sp-2) var(--sp-3);
  background: var(--c-ok-bg); color: var(--c-ok);
  border-bottom: 1px solid var(--c-ok);
  font-size: var(--fz-sm);
}
.flash.err { background: var(--c-bad-bg); color: var(--c-bad);
             border-bottom-color: var(--c-bad); }

/* TABS */
.tabs {
  display: flex; border-bottom: 1px solid var(--c-border-sub);
  padding: 0 var(--sp-2); flex-shrink: 0;
}
.tab {
  padding: 7px var(--sp-3); font-size: var(--fz-xs);
  color: var(--c-ink-mute); border-bottom: 2px solid transparent;
  white-space: nowrap; cursor: pointer;
}
.tab:hover { color: var(--c-ink); text-decoration: none; }
.tab.active { color: var(--c-acc); border-bottom-color: var(--c-acc); }
.tab .cnt { opacity: .55; font-size: 10px; margin-left: 2px; }

/* DOC ROWS */
.doc-row {
  display: flex; align-items: flex-start; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid var(--c-border-sub);
  cursor: pointer; color: inherit;
}
.doc-row:hover { background: var(--c-surface-2); text-decoration: none; }
.doc-row.sel { background: var(--c-acc-bg); }
.doc-row-icon {
  flex-shrink: 0; width: 26px; height: 26px;
  border-radius: var(--radius-sm); background: var(--c-surface-2);
  display: flex; align-items: center; justify-content: center;
  font-size: 13px; margin-top: 1px;
}
.doc-row-body { flex: 1; min-width: 0; }
.doc-row-title {
  font-size: var(--fz-sm); font-weight: 500;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.doc-row-meta {
  font-size: var(--fz-xs); color: var(--c-ink-soft); margin-top: 2px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.doc-row-right {
  flex-shrink: 0; display: flex; flex-direction: column;
  align-items: flex-end; gap: 3px; padding-top: 1px;
}
.cat-badge {
  font-size: 10px; padding: 1px 5px; border-radius: 3px;
  background: var(--c-surface-2); color: var(--c-ink-soft);
  white-space: nowrap; border: 1px solid var(--c-border-sub);
}
.sdot {
  width: 6px; height: 6px; border-radius: 50%; display: inline-block;
}
.inbox-row {
  display: flex; align-items: center; gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border-bottom: 1px solid #f5d89a;
  background: var(--c-warn-bg); font-size: var(--fz-sm);
  color: var(--c-warn);
}
.inbox-row .fn {
  flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}

/* DETAIL */
.ws-detail-scroll {
  flex: 1; overflow-y: auto; padding: var(--sp-5);
  display: flex; flex-direction: column; gap: var(--sp-4);
}
.det-header {
  display: flex; align-items: flex-start; gap: var(--sp-3);
  padding-bottom: var(--sp-3);
  border-bottom: 1px solid var(--c-border);
}
.det-title { flex: 1; font-size: var(--fz-lg); font-weight: 600; }
.det-actions { display: flex; gap: var(--sp-2); flex-shrink: 0; flex-wrap: wrap; }
.det-grid {
  display: grid; grid-template-columns: 1fr 1fr;
  gap: var(--sp-2) var(--sp-5);
}
.det-field {}
.det-label {
  font-size: var(--fz-xs); color: var(--c-ink-soft);
  text-transform: uppercase; letter-spacing: .4px; margin-bottom: 2px;
}
.det-value { font-size: var(--fz-sm); }
.det-section {
  border-top: 1px solid var(--c-border-sub); padding-top: var(--sp-3);
}
.det-sec-title {
  font-size: var(--fz-xs); font-weight: 600; color: var(--c-ink-mute);
  text-transform: uppercase; letter-spacing: .5px; margin-bottom: var(--sp-2);
}
.det-footer {
  font-size: var(--fz-xs); color: var(--c-ink-soft);
  border-top: 1px solid var(--c-border-sub); padding-top: var(--sp-3);
}
.det-table { width: 100%; font-size: var(--fz-sm); border-collapse: collapse; }
.det-table td {
  padding: 4px 6px; border-bottom: 1px solid var(--c-border-sub);
  vertical-align: middle;
}
.det-table tr:last-child td { border-bottom: none; }
.det-table td.nid {
  width: 1%; white-space: nowrap; font-family: ui-monospace, monospace;
  font-size: var(--fz-xs); color: var(--c-ink-mute);
}
.det-table td.act { width: 1%; white-space: nowrap; text-align: right; }
.det-table .dir-in { color: var(--c-ink-soft); font-size: 11px; }
.il { display: inline; margin: 0; }
.link-form {
  display: flex; gap: var(--sp-2); margin-top: var(--sp-2); align-items: center;
}
.link-form select {
  flex: 1; padding: 5px 8px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: var(--fz-sm); background: var(--c-surface);
}

/* FORM */
.form-page {
  flex: 1; overflow-y: auto;
  padding: var(--sp-5) var(--sp-6);
  background: var(--c-surface);
}
.form-inner { max-width: 720px; margin: 0 auto; }
.form-row { margin-bottom: var(--sp-3); }
.form-row > label {
  display: block; font-size: var(--fz-xs); color: var(--c-ink-mute);
  text-transform: uppercase; letter-spacing: .4px; margin-bottom: 3px;
}
.form-row input[type=text], .form-row input[type=date],
.form-row input:not([type]), .form-row select, .form-row textarea {
  width: 100%; padding: 6px 9px;
  border: 1px solid var(--c-border); border-radius: var(--radius-sm);
  font-size: var(--fz-sm); font-family: inherit;
  background: var(--c-surface); color: var(--c-ink);
}
.form-row input:focus, .form-row select:focus, .form-row textarea:focus {
  outline: none; border-color: var(--c-acc);
}
.form-row textarea { min-height: 80px; resize: vertical; }
.form-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: var(--sp-3);
}
.form-actions {
  display: flex; gap: var(--sp-2); margin-top: var(--sp-5);
  padding-top: var(--sp-4); border-top: 1px solid var(--c-border);
}
.form-section {
  margin-top: var(--sp-5); padding-top: var(--sp-4);
  border-top: 1px solid var(--c-border-sub);
}
.form-section h3 {
  font-size: var(--fz-xs); color: var(--c-ink-mute); font-weight: 600;
  text-transform: uppercase; letter-spacing: .5px; margin-bottom: var(--sp-3);
}
.attached-file {
  background: var(--c-acc-bg); padding: var(--sp-2) var(--sp-3);
  border-radius: var(--radius-sm); font-size: var(--fz-sm);
  color: var(--c-acc); margin-bottom: var(--sp-3);
}

@media print {
  .sb, .topbar, .ws-list, .btn, form { display: none !important; }
  .ws { display: block; }
  .ws-detail { background: #fff; }
}
</style>
{% block head %}{% endblock %}
</head>
<body>

<div class="shell">

<aside class="sb">
  <div class="sb-logo">&#9632; Home<span class="accent">DMS</span></div>
  <a class="sb-link {% if nav=='workspace' %}active{% endif %}" href="/">
    <span>&#9632;</span> Dokumente
    {% if inbox_count %}<span class="badge">{{inbox_count}}</span>{% endif %}
  </a>
  <a class="sb-link {% if nav=='reminders' %}active{% endif %}" href="/reminders">
    <span>&#9675;</span> Reminder
    {% if reminder_count %}<span class="badge">{{reminder_count}}</span>{% endif %}
  </a>
  <a class="sb-link {% if nav=='contacts' %}active{% endif %}" href="/contacts">
    <span>&#9678;</span> Kontakte
  </a>
  <div class="sb-spacer"></div>
  <a class="sb-link {% if nav=='settings' %}active{% endif %}" href="/settings">
    <span>&#9881;</span> Einstellungen
  </a>
  <div class="sb-foot">v0.1</div>
</aside>

<div class="main">
  <div class="topbar">
    <h1>{% block topbar_title %}HomeDMS{% endblock %}</h1>
    <div class="topbar-search">
      <input type="text" placeholder="Suchen — / fokussiert"
             value="{{q or ''}}" id="search-input">
    </div>
    <div style="margin-left:auto;display:flex;gap:6px">
      {% block topbar_actions %}{% endblock %}
    </div>
  </div>
  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in msgs %}
      <div class="flash {% if cat=='err' %}err{% endif %}">{{msg}}</div>
    {% endfor %}
  {% endwith %}
  {% block main %}{% endblock %}
</div>

</div>

<div class="drop-overlay" id="drop-overlay">
  <div class="drop-overlay-msg">📎 PDF hier ablegen</div>
</div>

<script>
// Suche: Enter submittet, live-filter über bestehende Rows
(function(){
  const inp = document.getElementById('search-input');
  if (!inp) return;
  inp.addEventListener('keydown', function(e){
    if (e.key === 'Enter') {
      e.preventDefault();
      const params = new URLSearchParams(window.location.search);
      params.set('q', inp.value.trim());
      params.delete('doc');
      window.location.href = '/?' + params.toString();
    }
    if (e.key === 'Escape') { inp.value = ''; inp.blur(); }
  });
})();

document.addEventListener('keydown', function(e){
  if (e.key === '/' && !['INPUT','TEXTAREA'].includes(document.activeElement.tagName)) {
    e.preventDefault();
    document.getElementById('search-input').focus();
  }
});

(function(){
  let counter = 0;
  const ov = document.getElementById('drop-overlay');
  const ovMsg = ov.querySelector('.drop-overlay-msg');

  document.addEventListener('dragenter', function(e){
    if (e.dataTransfer && e.dataTransfer.types.includes('Files')) {
      counter++;
      const sel = document.getElementById('_sel_doc');
      const nid = sel ? sel.value : '';
      ovMsg.textContent = nid ? '📎 Datei zu ' + nid + ' hinzufügen' : '📥 In Inbox ablegen';
      ov.classList.add('active');
    }
  });
  document.addEventListener('dragleave', function(){
    counter--; if (counter <= 0) { counter = 0; ov.classList.remove('active'); }
  });
  document.addEventListener('dragover', function(e){ e.preventDefault(); });
  document.addEventListener('drop', function(e){
    e.preventDefault(); counter = 0; ov.classList.remove('active');
    if (!e.dataTransfer.files.length) return;
    const fd = new FormData();
    for (const f of e.dataTransfer.files) fd.append('file', f);
    const sel = document.getElementById('_sel_doc');
    const nid = sel ? sel.value : '';
    if (nid) fd.append('attach_to', nid);
    fetch('/drop', {method:'POST', body:fd}).then(r => {
      if (r.ok) r.text().then(loc => { window.location.href = loc; });
      else alert('Upload fehlgeschlagen');
    });
  });
})();
</script>

</body></html>
""")


tpl("workspace", r"""{% extends "base" %}
{% block topbar_title %}Dokumente{% endblock %}
{% block topbar_actions %}
  <a class="btn primary sm" href="/d/new">+ Neu</a>
{% endblock %}
{% block main %}
<input type="hidden" id="_sel_doc" value="{{sel.nid if sel else ''}}">
<div class="ws">

  <div class="ws-list">
    <div class="tabs">
      <a class="tab {% if not sf %}active{% endif %}" href="/?q={{q}}">Alle <span class="cnt">{{tc.alle}}</span></a>
      <a class="tab {% if sf=='AKTIV' %}active{% endif %}" href="/?status=AKTIV&q={{q}}">Aktiv <span class="cnt">{{tc.aktiv}}</span></a>
      <a class="tab {% if sf=='ARCHIVIERT' %}active{% endif %}" href="/?status=ARCHIVIERT&q={{q}}">Archiv <span class="cnt">{{tc.archiviert}}</span></a>
    </div>
    <div class="ws-list-body">
      {% for fn in ifiles %}
      <div class="inbox-row">
        <span>📥</span><span class="fn">{{fn}}</span>
        <a class="btn sm" href="/inbox/attach?file={{fn|urlencode}}">Anhängen</a>
        <a class="btn sm" href="/inbox/create?file={{fn|urlencode}}">Erfassen</a>
      </div>
      {% endfor %}
      {% if not doc_list and not ifiles %}
      <div class="empty">
        <div class="icon">📂</div>
        <div class="title">Keine Dokumente</div>
        <div class="hint">Zieh ein PDF hierher<br>oder klicke „+ Neu"</div>
      </div>
      {% endif %}
      {% for d in doc_list %}
      <a class="doc-row {% if sel and sel.nid == d.nid %}sel{% endif %}"
         href="/?doc={{d.nid}}&status={{sf}}&q={{q}}">
        <div class="doc-row-icon">{% if d.has_file %}📄{% else %}📝{% endif %}</div>
        <div class="doc-row-body">
          <div class="doc-row-title">{{d.title or '(kein Titel)'}}</div>
          <div class="doc-row-meta">
            {{d.issuer or ''}}{% if d.issuer and d.doc_date %} · {% endif %}{{d.doc_date or ''}}
          </div>
        </div>
        <div class="doc-row-right">
          <span class="cat-badge">{{d.category or '—'}}</span>
          <span class="sdot" style="background:{{sc.get(d.status,'#ccc')}}"></span>
        </div>
      </a>
      {% endfor %}
    </div>
  </div>

  <div class="ws-detail">
    {% if sel %}
    <div class="ws-detail-scroll">
      <div class="det-header">
        <div class="det-title">{{sel.title or '(kein Titel)'}}</div>
        <div class="det-actions">
          {% if sel.vault_file %}<a class="btn sm" href="/vault/{{sel.vault_file}}" target="_blank">📎 Datei</a>{% endif %}
          <a class="btn sm" href="/d/{{sel.nid}}/edit">✏ Bearbeiten</a>
          <form method="post" action="/d/{{sel.nid}}/delete" onsubmit="return confirm('Löschen?')">
            <button class="btn sm danger">Löschen</button>
          </form>
        </div>
      </div>
      <div class="det-grid">
        {% set fields = [
          ('Kategorie', sel.category), ('Typ', sel.doc_type),
          ('Status', sel.status), ('Aussteller', sel.issuer),
          ('Datum', sel.doc_date),
          ('Betrag', ((sel.amount|string) ~ ' ' ~ (sel.currency or '')) if sel.amount else none),
          ('Fälligkeit', sel.due_date), ('Ablauf', sel.expires_at),
          ('Kündbar bis', sel.cancellable_until),
          ('Sprache', sel.language), ('Archivnr.', sel.asn), ('Tags', sel.tags),
        ] %}
        {% for label, val in fields %}
          {% if val %}
          <div class="det-field">
            <div class="det-label">{{label}}</div>
            <div class="det-value">{{val}}</div>
          </div>
          {% endif %}
        {% endfor %}
      </div>
      {% if sel.notes %}
      <div class="det-section">
        <div class="det-sec-title">Notizen</div>
        <div style="font-size:var(--fz-sm);white-space:pre-wrap;color:var(--c-ink)">{{sel.notes}}</div>
      </div>
      {% endif %}

      <div class="det-section">
        <div class="det-sec-title">Verknüpft ({{links|length}})</div>
        {% if links %}
        <table class="det-table">
          {% for l in links %}
          <tr>
            <td class="nid">{{l.nid}}{% if l.dir=='in' %} <span class="dir-in">←</span>{% endif %}</td>
            <td><a href="/?doc={{l.nid}}">{{l.title or '(kein Titel)'}}</a></td>
            <td class="act">
              <form class="il" method="post" action="/link/{{l.eid}}/delete?back=/?doc={{sel.nid}}"
                    onsubmit="return confirm('Verknüpfung entfernen?')">
                <button class="btn sm danger">✕</button>
              </form>
            </td>
          </tr>
          {% endfor %}
        </table>
        {% endif %}
        {% if link_targets %}
        <form class="link-form" method="post" action="/d/{{sel.nid}}/link">
          <select name="target">
            {% for t in link_targets %}
            <option value="{{t.nid}}">{{t.nid}} — {{t.title or '(kein Titel)'}}</option>
            {% endfor %}
          </select>
          <button class="btn sm" type="submit">+ Verknüpfen</button>
        </form>
        {% endif %}
      </div>

      <div class="det-footer">
        {{sel.nid}} · Erstellt {{(sel.created_at or '')[:10]}} · Geändert {{(sel.changed_at or '')[:10]}}
      </div>
    </div>
    {% else %}
    <div class="empty">
      <div class="icon">←</div>
      <div class="title">Wähle ein Dokument</div>
      <div class="hint">oder zieh ein PDF in dieses Fenster</div>
    </div>
    {% endif %}
  </div>

</div>
{% endblock %}
""")


tpl("doc_form", r"""{% extends "base" %}
{% block topbar_title %}{% if doc %}Bearbeiten — {{doc.title or doc.nid}}{% else %}Neues Dokument{% endif %}{% endblock %}
{% block topbar_actions %}
  <a class="btn sm" href="{% if doc %}/?doc={{doc.nid}}{% else %}/{% endif %}">Abbrechen</a>
{% endblock %}
{% block main %}
<div class="form-page"><div class="form-inner">
<form method="post" enctype="multipart/form-data">
  {% if attached_file %}
  <div class="attached-file">📎 Datei aus Inbox: <b>{{attached_file}}</b></div>
  <input type="hidden" name="from_inbox" value="{{attached_file}}">
  {% endif %}
  {% if doc and doc.vault_file %}
  <div class="attached-file">📎 Datei: <b>{{doc.vault_file}}</b>
    <a href="/vault/{{doc.vault_file}}" target="_blank" style="margin-left:8px">öffnen</a>
  </div>
  {% endif %}

  <div class="form-row">
    <label>Titel *</label>
    <input name="title" value="{{ (doc.title if doc else '') or '' }}" required autofocus>
  </div>

  <div class="form-grid">
    <div class="form-row"><label>Kategorie</label>
      <select name="category"><option value="">—</option>
      {% for c in categories %}<option value="{{c}}" {% if doc and doc.category==c %}selected{% endif %}>{{c}}</option>{% endfor %}
      </select></div>
    <div class="form-row"><label>Typ</label>
      <select name="doc_type"><option value="">—</option>
      {% for t in types %}<option value="{{t}}" {% if doc and doc.doc_type==t %}selected{% endif %}>{{t}}</option>{% endfor %}
      </select></div>
  </div>

  <div class="form-grid">
    <div class="form-row"><label>Aussteller</label>
      <input name="issuer" value="{{ (doc.issuer if doc else '') or '' }}"></div>
    <div class="form-row"><label>Dokumentdatum</label>
      <input type="date" name="doc_date" value="{{ (doc.doc_date if doc else '') or '' }}"></div>
  </div>

  <div class="form-grid">
    <div class="form-row"><label>Betrag</label>
      <input name="amount" value="{{ (doc.amount if doc else '') or '' }}"></div>
    <div class="form-row"><label>Währung</label>
      <select name="currency"><option value="">—</option>
      {% for c in currencies %}<option value="{{c}}" {% if doc and doc.currency==c %}selected{% endif %}>{{c}}</option>{% endfor %}
      </select></div>
  </div>

  <div class="form-grid">
    <div class="form-row"><label>Fälligkeit</label>
      <input type="date" name="due_date" value="{{ (doc.due_date if doc else '') or '' }}"></div>
    <div class="form-row"><label>Ablaufdatum</label>
      <input type="date" name="expires_at" value="{{ (doc.expires_at if doc else '') or '' }}"></div>
  </div>

  <div class="form-grid">
    <div class="form-row"><label>Kündbar bis</label>
      <input type="date" name="cancellable_until" value="{{ (doc.cancellable_until if doc else '') or '' }}"></div>
    <div class="form-row"><label>Sprache</label>
      <select name="language"><option value="">—</option>
      {% for l in languages %}<option value="{{l}}" {% if doc and doc.language==l %}selected{% endif %}>{{l}}</option>{% endfor %}
      </select></div>
  </div>

  <div class="form-grid">
    <div class="form-row"><label>Archivnummer</label>
      <input name="asn" value="{{ (doc.asn if doc else '') or '' }}"></div>
    <div class="form-row"><label>Tags (komma-getrennt)</label>
      <input name="tags" value="{{ (doc.tags if doc else '') or '' }}"></div>
  </div>

  <div class="form-row"><label>Notizen</label>
    <textarea name="notes">{{ (doc.notes if doc else '') or '' }}</textarea></div>

  {% if doc %}
  <div class="form-row"><label>Status</label>
    <select name="status">
    {% for s in doc_statuses %}<option value="{{s}}" {% if doc.status==s %}selected{% endif %}>{{s}}</option>{% endfor %}
    </select></div>
  {% endif %}

  {% if not doc and not attached_file %}
  <div class="form-section">
    <h3>Datei (optional)</h3>
    <input type="file" name="file">
  </div>
  {% endif %}

  <div class="form-actions">
    <button class="btn primary" type="submit">{% if doc %}Speichern{% else %}Anlegen{% endif %}</button>
    <a class="btn" href="{% if doc %}/?doc={{doc.nid}}{% else %}/{% endif %}">Abbrechen</a>
  </div>
</form>
</div></div>
{% endblock %}
""")


tpl("inbox_attach", r"""{% extends "base" %}
{% block topbar_title %}Inbox anhängen{% endblock %}
{% block topbar_actions %}
  <a class="btn sm" href="/">Abbrechen</a>
{% endblock %}
{% block main %}
<div class="form-page"><div class="form-inner">
  <p style="font-size:var(--fz-md);margin-bottom:var(--sp-4)">
    📎 <b>{{fname}}</b> an bestehendes Dokument anhängen
  </p>
  <form method="post" action="/inbox/attach">
    <input type="hidden" name="file" value="{{fname}}">
    <div class="form-row">
      <label>Dokument suchen</label>
      <input type="text" id="attach-search" placeholder="Titel, ID, Aussteller…" autocomplete="off">
    </div>
    <div id="attach-list" style="margin-top:var(--sp-3);max-height:420px;overflow-y:auto">
      {% for d in all_docs %}
      <label class="att-row" data-s="{{(d.nid ~ ' ' ~ d.title ~ ' ' ~ d.issuer)|lower}}">
        <input type="radio" name="target_nid" value="{{d.nid}}" required>
        <span class="att-nid">{{d.nid}}</span>
        <span class="att-title">{{d.title or '(kein Titel)'}}</span>
        {% if d.issuer %}<span class="att-issuer">{{d.issuer}}</span>{% endif %}
      </label>
      {% else %}
      <p style="color:var(--c-ink-mute);font-size:var(--fz-sm)">Keine Dokumente vorhanden.</p>
      {% endfor %}
    </div>
    <div class="form-actions">
      <button class="btn primary" type="submit">Anhängen</button>
      <a class="btn" href="/">Abbrechen</a>
    </div>
  </form>
</div></div>
<style>
.att-row {
  display:flex; align-items:center; gap:var(--sp-3);
  padding:var(--sp-2) var(--sp-3);
  border:1px solid var(--c-border); border-radius:var(--radius-sm);
  margin-bottom:3px; cursor:pointer;
}
.att-row:hover { background:var(--c-acc-bg); }
.att-row input[type=radio] { flex-shrink:0; }
.att-nid { font-family:ui-monospace,monospace; font-size:var(--fz-xs); color:var(--c-ink-mute); min-width:90px; }
.att-title { font-size:var(--fz-sm); flex:1; }
.att-issuer { font-size:var(--fz-xs); color:var(--c-ink-soft); }
.att-row.hidden { display:none; }
</style>
<script>
(function(){
  var inp = document.getElementById('attach-search');
  var rows = document.querySelectorAll('.att-row');
  inp.addEventListener('input', function(){
    var q = inp.value.toLowerCase();
    rows.forEach(function(r){ r.classList.toggle('hidden', q !== '' && r.dataset.s.indexOf(q) === -1); });
  });
  inp.focus();
})();
</script>
{% endblock %}
""")


def nav_counts():
    db = get_db()
    ic = len(inbox_files())
    rc = 0
    if "reminders" in db.list_collections():
        rc = sum(1 for r in db.list_nodes("reminders").values()
                 if not r.get("fired") and r.get("remind_at", "9999") <= today())
    return {"inbox_count": ic, "reminder_count": rc}


# ---------------------------------------------------------------------------
# WORKSPACE
# ---------------------------------------------------------------------------

@app.route("/")
def workspace():
    db  = get_db()
    sf  = request.args.get("status", "")
    q   = request.args.get("q", "").lower().strip()
    sel_nid = request.args.get("doc", "")

    all_docs = db.list_nodes("documents") if "documents" in db.list_collections() else {}

    tc = {
        "alle":       len(all_docs),
        "aktiv":      sum(1 for d in all_docs.values() if d.get("status") == "AKTIV"),
        "archiviert": sum(1 for d in all_docs.values() if d.get("status") == "ARCHIVIERT"),
    }

    def matches(nid, d):
        if sf and d.get("status") != sf:
            return False
        if q:
            hay = " ".join([d.get("title",""), d.get("issuer",""),
                            d.get("category",""), d.get("tags",""), nid]).lower()
            if q not in hay:
                return False
        return True

    doc_list = []
    for nid, d in sorted(all_docs.items(),
                         key=lambda x: x[1].get("doc_date", ""), reverse=True):
        if matches(nid, d):
            row = dict(d); row["nid"] = nid
            row["has_file"] = bool(d.get("vault_file"))
            doc_list.append(row)

    sel = None
    if sel_nid and sel_nid in all_docs:
        sel = dict(all_docs[sel_nid]); sel["nid"] = sel_nid
    elif doc_list:
        sel = doc_list[0]; sel_nid = sel["nid"]

    links, link_targets = [], []
    if sel:
        ref = f"documents/{sel['nid']}"
        linked_nids = set()
        for eid, e in db.get_connected_edges(ref, direction="out", rel_type="doc_link"):
            tnid = e["target"].split("/", 1)[-1]
            t = all_docs.get(tnid)
            if t:
                links.append({"eid": eid, "nid": tnid, "title": t.get("title", ""), "dir": "out"})
                linked_nids.add(tnid)
        for eid, e in db.get_connected_edges(ref, direction="in", rel_type="doc_link"):
            snid = e["source"].split("/", 1)[-1]
            s = all_docs.get(snid)
            if s:
                links.append({"eid": eid, "nid": snid, "title": s.get("title", ""), "dir": "in"})
                linked_nids.add(snid)
        for nid, d in sorted(all_docs.items()):
            if nid == sel["nid"] or nid in linked_nids:
                continue
            link_targets.append({"nid": nid, "title": d.get("title", "")})

    ifiles = inbox_files() if not sf else []

    return render_template("workspace",
        nav="workspace", q=q, sf=sf,
        doc_list=doc_list, tc=tc,
        sel=sel, sel_nid=sel_nid,
        links=links, link_targets=link_targets,
        ifiles=ifiles,
        sc=DOC_STATUS,
        **nav_counts())


@app.route("/drop", methods=["POST"])
def drop():
    f = request.files.get("file")
    if not f or not f.filename or not allowed_file(f.filename):
        return "ungültig", 400
    attach_to = request.form.get("attach_to", "").strip()
    db = get_db()
    if attach_to and db.get_node(f"documents/{attach_to}"):
        vault_file = _unique_vault_name(f.filename)
        f.save(os.path.join(VAULT_DIR, vault_file))
        db.update_node("documents", attach_to,
                       {"vault_file": vault_file, "changed_at": now()})
        return url_for("workspace", doc=attach_to), 200
    safe = os.path.basename(f.filename)
    f.save(os.path.join(INBOX_DIR, safe))
    return url_for("workspace"), 200


# ---------------------------------------------------------------------------
# DOCUMENT CRUD
# ---------------------------------------------------------------------------

DOC_FORM_FIELDS = (
    "title", "issuer", "category", "doc_type", "doc_date",
    "amount", "currency", "due_date", "expires_at", "cancellable_until",
    "language", "asn", "tags", "notes",
)


def _unique_vault_name(filename):
    base = os.path.basename(filename)
    if not os.path.exists(os.path.join(VAULT_DIR, base)):
        return base
    name, ext = os.path.splitext(base)
    i = 1
    while os.path.exists(os.path.join(VAULT_DIR, f"{name}_{i}{ext}")):
        i += 1
    return f"{name}_{i}{ext}"


def _form_context(doc=None, attached_file=""):
    return dict(
        nav="workspace", q="", doc=doc, attached_file=attached_file,
        categories=DOC_CATEGORIES, types=DOC_TYPES,
        currencies=CURRENCIES, languages=LANGUAGES,
        doc_statuses=list(DOC_STATUS.keys()),
        **nav_counts(),
    )


@app.route("/d/new", methods=["GET", "POST"])
def doc_new():
    db = get_db()
    if request.method == "POST":
        data = {}
        for k in DOC_FORM_FIELDS:
            v = request.form.get(k, "").strip()
            if v:
                data[k] = v
        if not data.get("title"):
            flash("Titel ist erforderlich.", "err")
            return redirect(url_for("doc_new"))
        data["status"]     = "AKTIV"
        data["created_by"] = DEFAULT_USER
        data["created_at"] = now()
        data["changed_at"] = now()

        vault_file = None
        from_inbox = request.form.get("from_inbox", "").strip()
        if from_inbox:
            src = os.path.join(INBOX_DIR, os.path.basename(from_inbox))
            if os.path.exists(src):
                vault_file = _unique_vault_name(from_inbox)
                shutil.move(src, os.path.join(VAULT_DIR, vault_file))
        else:
            f = request.files.get("file")
            if f and f.filename and allowed_file(f.filename):
                vault_file = _unique_vault_name(f.filename)
                f.save(os.path.join(VAULT_DIR, vault_file))
        if vault_file:
            data["vault_file"] = vault_file

        nid = db.next_id("documents", prefix="DOC-", padding=5)
        db.create_node("documents", nid, data)
        flash(f"{nid} angelegt.")
        return redirect(url_for("workspace", doc=nid))

    return render_template("doc_form",
        **_form_context(attached_file=request.args.get("file", "")))


@app.route("/d/<nid>/edit", methods=["GET", "POST"])
def doc_edit(nid):
    db = get_db()
    doc = db.get_node(f"documents/{nid}")
    if not doc:
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("workspace"))
    if request.method == "POST":
        upd = {}
        for k in DOC_FORM_FIELDS:
            upd[k] = request.form.get(k, "").strip()
        st = request.form.get("status", "").strip()
        if st in DOC_STATUS:
            upd["status"] = st
        upd["changed_at"] = now()
        db.update_node("documents", nid, upd)
        flash(f"{nid} gespeichert.")
        return redirect(url_for("workspace", doc=nid))
    doc["nid"] = nid
    return render_template("doc_form", **_form_context(doc=doc))


@app.route("/d/<nid>/delete", methods=["POST"])
def doc_delete(nid):
    db = get_db()
    doc = db.get_node(f"documents/{nid}")
    if doc:
        db.soft_delete("documents", nid, keep_asset=False)
        flash(f"{nid} gelöscht.")
    return redirect(url_for("workspace"))


@app.route("/d/<nid>/link", methods=["POST"])
def doc_link(nid):
    db     = get_db()
    target = request.form.get("target", "").strip()
    back   = request.referrer or url_for("workspace", doc=nid)
    src    = f"documents/{nid}"
    tgt    = f"documents/{target}"
    if not db.get_node(src) or not target or not db.get_node(tgt):
        flash("Dokument nicht gefunden.", "err")
        return redirect(back)
    if target == nid:
        flash("Kein Selbst-Link möglich.", "err")
        return redirect(back)
    existing = (
        [e for _, e in db.get_connected_edges(src, direction="out", rel_type="doc_link") if e["target"] == tgt] +
        [e for _, e in db.get_connected_edges(src, direction="in",  rel_type="doc_link") if e["source"] == tgt]
    )
    if existing:
        flash("Verknüpfung existiert bereits.", "err")
        return redirect(back)
    db.create_edge(src, tgt, "doc_link",
                   meta={"linked_at": now(), "linked_by": DEFAULT_USER})
    flash(f"Mit {target} verknüpft.")
    return redirect(back)


@app.route("/link/<edge_id>/delete", methods=["POST"])
def link_delete(edge_id):
    db   = get_db()
    back = request.referrer or url_for("workspace")
    db.delete_edge(edge_id)
    flash("Verknüpfung entfernt.")
    return redirect(back)


@app.route("/inbox/create")
def inbox_create():
    fn = request.args.get("file", "").strip()
    return redirect(url_for("doc_new", file=fn))


@app.route("/inbox/attach", methods=["GET"])
def inbox_attach_get():
    fname = request.args.get("file", "").strip()
    if not fname or "/" in fname or ".." in fname:
        flash("Ungültige Datei.", "err")
        return redirect(url_for("workspace"))
    db = get_db()
    raw = db.list_nodes("documents") if "documents" in db.list_collections() else {}
    all_docs = [
        {"nid": nid, "title": d.get("title", ""), "issuer": d.get("issuer", "")}
        for nid, d in sorted(raw.items(), key=lambda x: x[1].get("title", "").lower())
    ]
    return render_template("inbox_attach",
        nav="workspace", fname=fname, all_docs=all_docs,
        **nav_counts())


@app.route("/inbox/attach", methods=["POST"])
def inbox_attach_post():
    fname = request.form.get("file", "").strip()
    target_nid = request.form.get("target_nid", "").strip()
    if not fname or "/" in fname or ".." in fname:
        flash("Ungültige Datei.", "err")
        return redirect(url_for("workspace"))
    db = get_db()
    if not target_nid or not db.get_node(f"documents/{target_nid}"):
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("inbox_attach_get", file=fname))
    src = os.path.join(INBOX_DIR, os.path.basename(fname))
    if not os.path.exists(src):
        flash("Datei nicht mehr in Inbox.", "err")
        return redirect(url_for("workspace"))
    vault_file = _unique_vault_name(fname)
    shutil.move(src, os.path.join(VAULT_DIR, vault_file))
    db.update_node("documents", target_nid, {"vault_file": vault_file, "changed_at": now()})
    flash(f"Datei an {target_nid} angehängt.")
    return redirect(url_for("workspace", doc=target_nid))


@app.route("/vault/<path:filename>")
def vault_file(filename):
    if "/" in filename or ".." in filename:
        return "ungültig", 400
    path = os.path.join(VAULT_DIR, os.path.basename(filename))
    if not os.path.isfile(path):
        return "nicht gefunden", 404
    return send_file(path)


if __name__ == "__main__":
    print("HomeDMS läuft auf http://127.0.0.1:5001", flush=True)
    app.run(host="127.0.0.1", port=5001, debug=True, use_reloader=False)



