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
# BASE TEMPLATE
# ---------------------------------------------------------------------------

BASE = """<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>HomeDMS</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;font-size:13px;background:#eef0f4;
     color:#1a1d23;height:100vh;overflow:hidden}
a{color:#3b82f6;text-decoration:none}
a:hover{text-decoration:underline}

/* ---- Shell ---- */
.shell{display:flex;height:100vh}

/* ---- Sidebar ---- */
.sb{width:210px;min-width:210px;background:#1e2330;display:flex;
    flex-direction:column;overflow-y:auto}
.sb-logo{padding:14px 16px 10px;font-size:14px;font-weight:700;
         color:#fff;letter-spacing:.3px;border-bottom:1px solid #2d3347}
.sb-logo span{color:#3b82f6}
.sb-section{padding:8px 10px 2px;font-size:10px;font-weight:700;
            text-transform:uppercase;letter-spacing:.8px;color:#4a5270}
.sb a{display:flex;align-items:center;gap:8px;padding:6px 16px;
      color:#8b95b0;font-size:12.5px;border-radius:0;transition:background .1s}
.sb a:hover{background:#252b3b;color:#c8cfde;text-decoration:none}
.sb a.active{background:#2a3350;color:#fff;border-left:2px solid #3b82f6;
             padding-left:14px}
.sb-badge{margin-left:auto;background:#e74c3c;color:#fff;border-radius:10px;
          padding:1px 6px;font-size:10px;font-weight:700}
.sb-footer{margin-top:auto;padding:10px 16px;border-top:1px solid #2d3347}
.sb-footer a{color:#4a5270;font-size:11px;padding:4px 0}

/* ---- Main ---- */
.main{flex:1;overflow-y:auto;display:flex;flex-direction:column}
.topbar{background:#fff;border-bottom:1px solid #dde1ea;padding:0 20px;
        height:44px;display:flex;align-items:center;gap:12px;
        flex-shrink:0}
.topbar h1{font-size:14px;font-weight:600;color:#1a1d23;margin:0}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:8px}
.content{padding:16px 20px;flex:1}

/* ---- Search bar ---- */
.search-bar{flex:1;max-width:360px}
.search-bar input{width:100%;padding:5px 10px;border:1px solid #d1d5db;
                  border-radius:5px;font-size:12.5px;background:#f9fafb;
                  margin:0}
.search-bar input:focus{outline:none;border-color:#3b82f6;background:#fff}

/* ---- Cards ---- */
.card{background:#fff;border:1px solid #dde1ea;border-radius:6px;
      padding:12px 14px;margin-bottom:10px}
.card h2{font-size:12px;font-weight:700;text-transform:uppercase;
         letter-spacing:.5px;color:#6b7280;margin-bottom:8px}

/* ---- Tables ---- */
table{width:100%;border-collapse:collapse;background:#fff;
      border:1px solid #dde1ea;border-radius:6px;overflow:hidden;
      font-size:12.5px}
th{background:#f5f6fa;text-align:left;padding:5px 10px;font-size:11px;
   text-transform:uppercase;letter-spacing:.4px;color:#6b7280;
   font-weight:700;border-bottom:1px solid #dde1ea;white-space:nowrap}
td{padding:5px 10px;border-bottom:1px solid #f0f2f5;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#f8f9fd}
th.srt{cursor:pointer;user-select:none}
th.srt::after{content:' ⇅';opacity:.3;font-size:.75em}
th.srt.asc::after{content:' ↑';opacity:.8}
th.srt.dsc::after{content:' ↓';opacity:.8}

/* ---- Badges ---- */
.badge{display:inline-block;padding:2px 7px;border-radius:10px;
       font-size:10.5px;font-weight:600;background:#e8ecff;color:#3b82f6;
       white-space:nowrap}
.badge.green{background:#d1fae5;color:#065f46}
.badge.red{background:#fee2e2;color:#991b1b}
.badge.gray{background:#f3f4f6;color:#6b7280}
.badge.orange{background:#fef3c7;color:#92400e}

/* ---- Buttons ---- */
.btn{display:inline-block;padding:5px 12px;border-radius:5px;font-size:12px;
     font-weight:600;cursor:pointer;border:none;background:#3b82f6;
     color:#fff;line-height:1.4}
.btn:hover{background:#2563eb;text-decoration:none}
.btn.sm{padding:3px 8px;font-size:11px}
.btn.sec{background:#f3f4f6;color:#374151;border:1px solid #d1d5db}
.btn.sec:hover{background:#e5e7eb}
.btn.danger{background:#ef4444}.btn.danger:hover{background:#dc2626}
.btn.warn{background:#f59e0b;color:#fff}
.btn.ghost{background:transparent;color:#6b7280;border:1px solid #d1d5db}
.btn.ghost:hover{background:#f3f4f6}
form.il{display:inline}

/* ---- Forms ---- */
label{display:block;font-size:11.5px;font-weight:600;
      margin-bottom:3px;color:#374151}
input[type=text],input[type=number],input[type=date],input[type=email],
select,textarea{width:100%;padding:5px 9px;border:1px solid #d1d5db;
  border-radius:5px;font-size:12.5px;margin-bottom:0;background:#fff}
input:focus,select:focus,textarea:focus{outline:none;border-color:#3b82f6}
textarea{font-family:monospace;min-height:70px;resize:vertical}
.form-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));
           gap:10px;margin-bottom:10px}
.form-row{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;
          margin-bottom:8px}

/* ---- KV pairs ---- */
.kv{display:grid;grid-template-columns:140px 1fr;gap:3px 8px;font-size:12.5px}
.kv .k{color:#6b7280;font-weight:600}
.kv .v{word-break:break-word;color:#1a1d23}

/* ---- Flash ---- */
.flash{padding:7px 12px;border-radius:5px;margin-bottom:10px;font-size:12px;
       background:#d1fae5;color:#065f46;border:1px solid #a7f3d0}
.flash.err{background:#fee2e2;color:#991b1b;border-color:#fca5a5}

/* ---- Stat tiles ---- */
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));
       gap:8px;margin-bottom:12px}
.tile{background:#fff;border:1px solid #dde1ea;border-radius:6px;
      padding:10px 12px}
.tile .num{font-size:22px;font-weight:700;color:#3b82f6;line-height:1}
.tile .lbl{font-size:11px;color:#6b7280;margin-top:3px}

/* ---- Filter bar ---- */
.filterbar{background:#fff;border:1px solid #dde1ea;border-radius:6px;
           padding:8px 12px;margin-bottom:10px;display:flex;
           gap:8px;align-items:flex-end;flex-wrap:wrap}
.filterbar select,.filterbar input{width:auto;min-width:120px;margin:0}
</style></head><body>
<div class="shell">

<!-- Sidebar -->
<aside class="sb">
  <div class="sb-logo">&#9632; Home<span>DMS</span></div>
  <div style="padding:8px 0">
    <div class="sb-section">Übersicht</div>
    <a href="/" class="{% if active=='dashboard' %}active{% endif %}">
      &#9632; Dashboard</a>
    <a href="/inbox" class="{% if active=='inbox' %}active{% endif %}">
      &#9744; Inbox
      {% if inbox_count %}<span class="sb-badge">{{inbox_count}}</span>{% endif %}
    </a>
    <a href="/reminders" class="{% if active=='reminders' %}active{% endif %}">
      &#9675; Reminder
      {% if reminder_count %}<span class="sb-badge">{{reminder_count}}</span>{% endif %}
    </a>
    <div class="sb-section">Archiv</div>
    <a href="/documents" class="{% if active=='documents' %}active{% endif %}">
      &#9723; Dokumente</a>
    <a href="/contacts" class="{% if active=='contacts' %}active{% endif %}">
      &#9675; Kontakte</a>
    <div class="sb-section">System</div>
    <a href="/search" class="{% if active=='search' %}active{% endif %}">
      &#9998; Suche</a>
    <a href="/settings" class="{% if active=='settings' %}active{% endif %}">
      &#9881; Einstellungen</a>
  </div>
  <div class="sb-footer">
    <a href="/gc">GC ausführen</a>
  </div>
</aside>

<!-- Main -->
<div class="main">
  <div class="topbar">
    <h1>{% block title %}HomeDMS{% endblock %}</h1>
    <form class="search-bar" method="get" action="/search">
      <input type="text" name="q" placeholder="Dokumente suchen…"
             value="{% block search_val %}{% endblock %}">
    </form>
    <div class="topbar-right">{% block topbar_right %}{% endblock %}</div>
  </div>
  <div class="content">
    {% for msg,cat in get_flashed_messages(with_categories=true) %}
    <div class="flash {% if cat=='err' %}err{% endif %}">{{msg}}</div>
    {% endfor %}
    {% block content %}{% endblock %}
  </div>
</div>

</div><!-- /shell -->
<script>
function srt(th){
  var tbl=th.closest('table'),idx=Array.from(th.parentElement.children).indexOf(th);
  var asc=!th.classList.contains('asc');
  th.parentElement.querySelectorAll('th.srt').forEach(function(t){
    t.classList.remove('asc','dsc')});
  th.classList.add(asc?'asc':'dsc');
  var rows=Array.from(tbl.tBodies[0].rows);
  rows.sort(function(a,b){
    var av=(a.cells[idx]?a.cells[idx].innerText:'').trim();
    var bv=(b.cells[idx]?b.cells[idx].innerText:'').trim();
    var n=parseFloat(av),m=parseFloat(bv);
    if(!isNaN(n)&&!isNaN(m))return asc?n-m:m-n;
    return asc?av.localeCompare(bv,'de'):bv.localeCompare(av,'de');
  });
  rows.forEach(function(r){tbl.tBodies[0].appendChild(r)});
}
</script>
</body></html>"""


def tmpl(title, content, active="", topbar_right=""):
    t = BASE
    t = t.replace("{% block title %}HomeDMS{% endblock %}", title)
    t = t.replace("{% block content %}{% endblock %}", content)
    t = t.replace("{% block topbar_right %}{% endblock %}", topbar_right)
    t = t.replace("{% block search_val %}{% endblock %}", "")
    return t


def render(title, content, active="", topbar_right=""):
    ic = len(inbox_files())
    db = get_db()
    rc = 0
    if "reminders" in db.list_collections():
        rc = sum(1 for r in db.list_nodes("reminders").values()
                 if not r.get("fired") and r.get("remind_at", "9999") <= today())
    T = BASE
    for blk, val in [
        ("{% block title %}HomeDMS{% endblock %}", title),
        ("{% block content %}{% endblock %}", content),
        ("{% block topbar_right %}{% endblock %}", topbar_right),
        ("{% block search_val %}{% endblock %}", ""),
        ("{% if active=='dashboard' %}active{% endif %}", "active" if active=="dashboard" else ""),
        ("{% if active=='inbox' %}active{% endif %}", "active" if active=="inbox" else ""),
        ("{% if active=='reminders' %}active{% endif %}", "active" if active=="reminders" else ""),
        ("{% if active=='documents' %}active{% endif %}", "active" if active=="documents" else ""),
        ("{% if active=='contacts' %}active{% endif %}", "active" if active=="contacts" else ""),
        ("{% if active=='search' %}active{% endif %}", "active" if active=="search" else ""),
        ("{% if active=='settings' %}active{% endif %}", "active" if active=="settings" else ""),
        ("{% if inbox_count %}<span class=\"sb-badge\">{{inbox_count}}</span>{% endif %}",
         f'<span class="sb-badge">{ic}</span>' if ic else ""),
        ("{% if reminder_count %}<span class=\"sb-badge\">{{reminder_count}}</span>{% endif %}",
         f'<span class="sb-badge">{rc}</span>' if rc else ""),
    ]:
        T = T.replace(blk, val)
    return render_template_string(T)


if __name__ == "__main__":
    app.run(debug=True, port=5002)

