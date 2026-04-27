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
    return T  # caller does render_template_string(T, **ctx)


# ---------------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/")
def dashboard():
    db    = get_db()
    t     = today()
    docs  = db.list_nodes("documents") if "documents" in db.list_collections() else {}
    rems  = db.list_nodes("reminders") if "reminders" in db.list_collections() else {}

    total   = len(docs)
    active  = sum(1 for d in docs.values() if d.get("status","AKTIV") == "AKTIV")
    expired = sum(1 for d in docs.values()
                  if d.get("expires_at") and d["expires_at"] < t
                  and d.get("status") != "ARCHIVIERT")

    due_soon = sorted(
        [(nid, d) for nid, d in docs.items()
         if d.get("due_date") and d["due_date"] >= t
         and d.get("status","AKTIV") == "AKTIV"],
        key=lambda x: x[1]["due_date"]
    )[:8]

    expiring = sorted(
        [(nid, d) for nid, d in docs.items()
         if d.get("expires_at") and d["expires_at"] >= t
         and d.get("status","AKTIV") == "AKTIV"],
        key=lambda x: x[1]["expires_at"]
    )[:8]

    open_rems = sorted(
        [(nid, r) for nid, r in rems.items()
         if not r.get("fired") and r.get("remind_at","9999") <= t],
        key=lambda x: x[1].get("remind_at","")
    )

    by_cat = {}
    for d in docs.values():
        c = d.get("category","—")
        by_cat[c] = by_cat.get(c, 0) + 1

    inbox_c = len(inbox_files())

    CONTENT = """
<div class="tiles">
  <div class="tile"><div class="num">{{total}}</div><div class="lbl">Dokumente</div></div>
  <div class="tile"><div class="num" style="color:#2a9d60">{{active}}</div>
    <div class="lbl">Aktiv</div></div>
  <div class="tile"><div class="num" style="color:#e74c3c">{{expired}}</div>
    <div class="lbl">Abgelaufen</div></div>
  <div class="tile"><div class="num" style="color:#f59e0b">{{inbox_c}}</div>
    <div class="lbl">Inbox</div></div>
  <div class="tile"><div class="num" style="color:#e74c3c">{{open_rems|length}}</div>
    <div class="lbl">Offene Reminder</div></div>
</div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">

<div>
{% if open_rems %}
<div class="card">
  <h2>&#9675; Offene Reminder</h2>
  <table><tr><th>Datum</th><th>Typ</th><th>Dokument</th><th></th></tr>
  {% for nid,r in open_rems %}
  <tr>
    <td style="color:#e74c3c;font-weight:600">{{r.remind_at}}</td>
    <td><span class="badge">{{rtypes.get(r.type,r.type)}}</span></td>
    <td><a href="/document/{{r.doc_ref}}">{{r.get('doc_title','—')}}</a></td>
    <td><form class="il" method="post" action="/reminder/{{nid}}/dismiss">
      <button class="btn sm sec">✓</button></form></td>
  </tr>
  {% endfor %}
  </table>
</div>
{% endif %}

{% if due_soon %}
<div class="card">
  <h2>&#9201; Fällig demnächst</h2>
  <table><tr><th>Fälligkeit</th><th>Titel</th><th>Aussteller</th></tr>
  {% for nid,d in due_soon %}
  <tr>
    <td style="white-space:nowrap;font-weight:600;color:#f59e0b">{{d.due_date}}</td>
    <td><a href="/document/documents/{{nid}}">{{d.title[:40]}}</a></td>
    <td style="color:#888">{{d.get('issuer','—')}}</td>
  </tr>
  {% endfor %}
  </table>
</div>
{% endif %}
</div>

<div>
{% if expiring %}
<div class="card">
  <h2>&#9888; Läuft bald ab</h2>
  <table><tr><th>Ablauf</th><th>Titel</th><th>Aussteller</th></tr>
  {% for nid,d in expiring %}
  <tr>
    <td style="white-space:nowrap;font-weight:600;
        color:{% if d.expires_at < t_30 %}#e74c3c{% else %}#f59e0b{% endif %}">
      {{d.expires_at}}</td>
    <td><a href="/document/documents/{{nid}}">{{d.title[:40]}}</a></td>
    <td style="color:#888">{{d.get('issuer','—')}}</td>
  </tr>
  {% endfor %}
  </table>
</div>
{% endif %}

{% if by_cat %}
<div class="card">
  <h2>&#9723; Nach Kategorie</h2>
  <table><tr><th>Kategorie</th><th>Anzahl</th></tr>
  {% for cat,cnt in by_cat_sorted %}
  <tr>
    <td><a href="/documents?category={{cat}}">{{cat}}</a></td>
    <td>{{cnt}}</td>
  </tr>
  {% endfor %}
  </table>
</div>
{% endif %}
</div>

</div>
"""
    from datetime import timedelta
    t_30 = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()
    by_cat_sorted = sorted(by_cat.items(), key=lambda x: -x[1])

    return render("Dashboard", CONTENT, active="dashboard",
                  topbar_right=f'<a class="btn sm" href="/document/new">+ Dokument</a>'), \
           None  # dummy tuple trick doesn't work — use render_template_string directly


@app.route("/")
def _dashboard_fix():
    pass  # placeholder — overwritten below


# Remove the dummy above, redefine cleanly:
app.view_functions.pop("_dashboard_fix", None)
app.view_functions.pop("dashboard", None)


@app.route("/")
def dashboard():  # noqa: F811
    db   = get_db()
    t    = today()
    docs = db.list_nodes("documents") if "documents" in db.list_collections() else {}
    rems = db.list_nodes("reminders") if "reminders" in db.list_collections() else {}

    total   = len(docs)
    active  = sum(1 for d in docs.values() if d.get("status","AKTIV") == "AKTIV")
    expired = sum(1 for d in docs.values()
                  if d.get("expires_at") and d["expires_at"] < t
                  and d.get("status") not in ("ARCHIVIERT","STORNIERT"))
    inbox_c = len(inbox_files())

    due_soon = sorted(
        [(nid, d) for nid, d in docs.items()
         if d.get("due_date") and d["due_date"] >= t
         and d.get("status","AKTIV") == "AKTIV"],
        key=lambda x: x[1]["due_date"])[:8]

    expiring = sorted(
        [(nid, d) for nid, d in docs.items()
         if d.get("expires_at") and d["expires_at"] >= t
         and d.get("status","AKTIV") == "AKTIV"],
        key=lambda x: x[1]["expires_at"])[:8]

    open_rems = sorted(
        [(nid, r) for nid, r in rems.items()
         if not r.get("fired") and r.get("remind_at","9999") <= t],
        key=lambda x: x[1].get("remind_at",""))

    by_cat = {}
    for d in docs.values():
        c = d.get("category","—")
        by_cat[c] = by_cat.get(c, 0) + 1

    from datetime import timedelta
    t_30 = (datetime.now(timezone.utc).date() + timedelta(days=30)).isoformat()

    C = """
<div class="tiles">
  <div class="tile"><div class="num">{{total}}</div>
    <div class="lbl">Dokumente gesamt</div></div>
  <div class="tile"><div class="num" style="color:#2a9d60">{{active}}</div>
    <div class="lbl">Aktiv</div></div>
  <div class="tile"><div class="num" style="color:#e74c3c">{{expired}}</div>
    <div class="lbl">Abgelaufen</div></div>
  <div class="tile"><div class="num" style="color:#f59e0b">{{inbox_c}}</div>
    <div class="lbl">Inbox</div></div>
  <div class="tile"><div class="num" style="color:#e74c3c">{{open_rems|length}}</div>
    <div class="lbl">Offene Reminder</div></div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
<div>
{% if open_rems %}
<div class="card"><h2>Offene Reminder</h2>
<table><tr><th>Datum</th><th>Typ</th><th>Dokument</th><th></th></tr>
{% for nid,r in open_rems %}
<tr>
  <td style="color:#e74c3c;font-weight:600;white-space:nowrap">{{r.remind_at}}</td>
  <td><span class="badge">{{rtypes.get(r.get('type',''),r.get('type',''))}}</span></td>
  <td><a href="/document/documents/{{r.get('doc_ref','').split('/')[-1]}}">
    {{r.get('doc_title','—')[:35]}}</a></td>
  <td><form class="il" method="post" action="/reminder/{{nid}}/dismiss">
    <button class="btn sm sec">✓</button></form></td>
</tr>
{% endfor %}
</table></div>
{% endif %}
{% if due_soon %}
<div class="card"><h2>Fällig demnächst</h2>
<table><tr><th>Fälligkeit</th><th>Titel</th><th>Aussteller</th></tr>
{% for nid,d in due_soon %}
<tr>
  <td style="white-space:nowrap;font-weight:600;color:#f59e0b">{{d.due_date}}</td>
  <td><a href="/document/documents/{{nid}}">{{d.title[:38]}}</a></td>
  <td style="color:#888">{{d.get('issuer','—')}}</td>
</tr>
{% endfor %}
</table></div>
{% endif %}
</div>
<div>
{% if expiring %}
<div class="card"><h2>Läuft bald ab</h2>
<table><tr><th>Ablauf</th><th>Titel</th><th>Aussteller</th></tr>
{% for nid,d in expiring %}
<tr>
  <td style="white-space:nowrap;font-weight:600;
      color:{% if d.expires_at <= t_30 %}#e74c3c{% else %}#f59e0b{% endif %}">
    {{d.expires_at}}</td>
  <td><a href="/document/documents/{{nid}}">{{d.title[:38]}}</a></td>
  <td style="color:#888">{{d.get('issuer','—')}}</td>
</tr>
{% endfor %}
</table></div>
{% endif %}
{% if by_cat %}
<div class="card"><h2>Nach Kategorie</h2>
<table><tr><th>Kategorie</th><th>Anzahl</th></tr>
{% for cat,cnt in by_cat_sorted %}
<tr>
  <td><a href="/documents?category={{cat}}">{{cat}}</a></td>
  <td>{{cnt}}</td>
</tr>
{% endfor %}
</table></div>
{% endif %}
</div>
</div>
"""
    T = render("Dashboard", C, active="dashboard",
               topbar_right='<a class="btn sm" href="/document/new">+ Dokument</a>')
    return render_template_string(T,
        total=total, active=active, expired=expired, inbox_c=inbox_c,
        due_soon=due_soon, expiring=expiring, open_rems=open_rems,
        by_cat_sorted=sorted(by_cat.items(), key=lambda x: -x[1]),
        rtypes=REMINDER_TYPES, t_30=t_30)


# ---------------------------------------------------------------------------
# DOCUMENTS LIST
# ---------------------------------------------------------------------------

@app.route("/documents")
def documents():
    db       = get_db()
    cat      = request.args.get("category", "")
    status   = request.args.get("status", "")
    doc_type = request.args.get("doc_type", "")
    issuer   = request.args.get("issuer", "").strip()
    q        = request.args.get("q", "").strip().lower()

    all_docs = db.list_nodes("documents") if "documents" in db.list_collections() else {}

    if cat:      all_docs = {k:v for k,v in all_docs.items() if v.get("category")==cat}
    if status:   all_docs = {k:v for k,v in all_docs.items() if v.get("status")==status}
    if doc_type: all_docs = {k:v for k,v in all_docs.items() if v.get("doc_type")==doc_type}
    if issuer:   all_docs = {k:v for k,v in all_docs.items()
                             if issuer.lower() in v.get("issuer","").lower()}
    if q:        all_docs = {k:v for k,v in all_docs.items()
                             if q in v.get("title","").lower()
                             or q in v.get("issuer","").lower()
                             or q in v.get("notes","").lower()}

    docs_sorted = sorted(all_docs.items(),
                         key=lambda x: x[1].get("doc_date","") or x[1].get("created_at",""),
                         reverse=True)

    C = """
<div class="filterbar">
  <form method="get" style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;width:100%">
    <div><label>Kategorie</label>
      <select name="category" style="width:130px">
        <option value="">Alle</option>
        {% for c in cats %}<option value="{{c}}"
          {% if c==cat %}selected{% endif %}>{{c}}</option>{% endfor %}
      </select></div>
    <div><label>Status</label>
      <select name="status" style="width:110px">
        <option value="">Alle</option>
        {% for s in statuses %}<option value="{{s}}"
          {% if s==status %}selected{% endif %}>{{s}}</option>{% endfor %}
      </select></div>
    <div><label>Typ</label>
      <select name="doc_type" style="width:120px">
        <option value="">Alle</option>
        {% for dt in dtypes %}<option value="{{dt}}"
          {% if dt==doc_type %}selected{% endif %}>{{dt}}</option>{% endfor %}
      </select></div>
    <div><label>Aussteller</label>
      <input type="text" name="issuer" value="{{issuer}}" style="width:130px"></div>
    <div style="align-self:flex-end;display:flex;gap:6px">
      <button class="btn sm" type="submit">Filtern</button>
      <a class="btn sm sec" href="/documents">Reset</a>
    </div>
  </form>
</div>
{% if docs %}
<table>
  <tr>
    <th class="srt" onclick="srt(this)">ID</th>
    <th class="srt" onclick="srt(this)">Titel</th>
    <th class="srt" onclick="srt(this)">Kategorie</th>
    <th class="srt" onclick="srt(this)">Typ</th>
    <th class="srt" onclick="srt(this)">Aussteller</th>
    <th class="srt" onclick="srt(this)">Datum</th>
    <th class="srt" onclick="srt(this)">Fälligkeit</th>
    <th class="srt" onclick="srt(this)">Ablauf</th>
    <th class="srt" onclick="srt(this)">Status</th>
  </tr>
  {% for nid,d in docs %}
  <tr>
    <td style="white-space:nowrap;font-size:11px;color:#888">
      <a href="/document/documents/{{nid}}">{{nid}}</a></td>
    <td><a href="/document/documents/{{nid}}"><strong>{{d.title[:48]}}</strong></a></td>
    <td><span class="badge">{{d.get('category','—')}}</span></td>
    <td style="color:#888;font-size:11.5px">{{d.get('doc_type','—')}}</td>
    <td style="font-size:12px">{{d.get('issuer','—')}}</td>
    <td style="white-space:nowrap;font-size:11.5px;color:#666">
      {{d.get('doc_date','—')}}</td>
    <td style="white-space:nowrap;font-size:11.5px;
        color:{% if d.get('due_date') and d.due_date < today %}#e74c3c
        {% else %}#666{% endif %}">
      {{d.get('due_date','—')}}</td>
    <td style="white-space:nowrap;font-size:11.5px;
        color:{% if d.get('expires_at') and d.expires_at < today %}#e74c3c
        {% else %}#666{% endif %}">
      {{d.get('expires_at','—')}}</td>
    <td><span class="badge {% if d.get('status')=='AKTIV' %}green
        {% elif d.get('status') in ('ARCHIVIERT','STORNIERT') %}gray
        {% elif d.get('status')=='ABGELAUFEN' %}red{% endif %}">
      {{d.get('status','AKTIV')}}</span></td>
  </tr>
  {% endfor %}
</table>
<div style="margin-top:6px;font-size:11px;color:#999">{{docs|length}} Dokumente</div>
{% else %}
<div class="card"><p style="color:#999">Keine Dokumente gefunden.</p>
  <a class="btn sm" href="/document/new" style="margin-top:8px">
    Erstes Dokument anlegen</a></div>
{% endif %}
"""
    T = render("Dokumente", C, active="documents",
               topbar_right='<a class="btn sm" href="/document/new">+ Dokument</a>')
    return render_template_string(T,
        docs=docs_sorted, cat=cat, status=status, doc_type=doc_type,
        issuer=issuer, cats=DOC_CATEGORIES, statuses=list(DOC_STATUS.keys()),
        dtypes=DOC_TYPES, today=today())


# ---------------------------------------------------------------------------
# DOCUMENT DETAIL
# ---------------------------------------------------------------------------

@app.route("/document/<path:ref>")
def document_detail(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("documents"))
    nid      = ref.split("/", 1)[1]
    status   = data.get("status", "AKTIV")
    next_st  = DOC_STATUS_FLOW.get(status)

    # Attachments via edges
    attachments = []
    if "attachments" in db.list_collections():
        for eid, e in db.get_connected_edges(ref, direction="out"):
            if e.get("type") == "has_attachment":
                a = db.get_node(e["target"])
                if a:
                    attachments.append((e["target"], a))

    # Related documents
    related = []
    for eid, e in db.get_connected_edges(ref, direction="both"):
        if e.get("type") == "related_to":
            other = e["target"] if e["source"] == ref else e["source"]
            node  = db.get_node(other)
            if node:
                related.append((other, node))

    # Contact
    contact = None
    if data.get("issuer_ref"):
        contact = db.get_node(data["issuer_ref"])

    # Reminders
    reminders = []
    if "reminders" in db.list_collections():
        for nid_r, r in db.list_nodes("reminders").items():
            if r.get("doc_ref") == ref:
                reminders.append((nid_r, r))

    # Primary file for viewer
    primary_file = None
    for _, a in attachments:
        if a.get("primary") or not primary_file:
            primary_file = a
            break

    has_pdf = primary_file and primary_file.get("file_path", "").lower().endswith(".pdf")

    C = """
<div style="display:flex;gap:12px;align-items:flex-start">

<!-- LEFT: metadata -->
<div style="flex:1;min-width:0">

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;
              margin-bottom:10px">
    <div>
      <span style="font-size:11px;color:#888;font-weight:600">{{nid}}</span>
      <h1 style="font-size:15px;margin:2px 0">{{data.title}}</h1>
      <div style="display:flex;gap:6px;margin-top:4px">
        <span class="badge">{{data.get('category','—')}}</span>
        <span class="badge gray">{{data.get('doc_type','—')}}</span>
        <span class="badge {% if status=='AKTIV' %}green
            {% elif status=='ABGELAUFEN' %}red{% else %}gray{% endif %}">
          {{status}}</span>
      </div>
    </div>
    <div style="display:flex;gap:6px;flex-shrink:0">
      <a class="btn sm sec" href="/document/{{ref}}/edit">Bearbeiten</a>
      {% if next_st %}
      <form class="il" method="post" action="/document/{{ref}}/status">
        <input type="hidden" name="new_status" value="{{next_st}}">
        <button class="btn sm warn">→ {{next_st}}</button>
      </form>
      {% endif %}
    </div>
  </div>

  <div class="kv">
    {% if data.get('issuer') %}
    <span class="k">Aussteller</span>
    <span class="v">{% if contact %}<a href="/contact/{{data.issuer_ref}}">
      {{data.issuer}}</a>{% else %}{{data.issuer}}{% endif %}</span>
    {% endif %}
    {% if data.get('doc_date') %}
    <span class="k">Datum</span><span class="v">{{data.doc_date}}</span>
    {% endif %}
    {% if data.get('amount') %}
    <span class="k">Betrag</span>
    <span class="v"><strong>{{data.amount}} {{data.get('currency','CHF')}}</strong></span>
    {% endif %}
    {% if data.get('due_date') %}
    <span class="k">Fälligkeit</span>
    <span class="v" style="color:{% if data.due_date < today %}#e74c3c
        {% else %}#1a1d23{% endif %}">{{data.due_date}}</span>
    {% endif %}
    {% if data.get('expires_at') %}
    <span class="k">Ablaufdatum</span>
    <span class="v" style="color:{% if data.expires_at < today %}#e74c3c
        {% else %}#1a1d23{% endif %}">{{data.expires_at}}</span>
    {% endif %}
    {% if data.get('cancellable_until') %}
    <span class="k">Kündbar bis</span><span class="v">{{data.cancellable_until}}</span>
    {% endif %}
    {% if data.get('asn') %}
    <span class="k">Archivnummer</span>
    <span class="v" style="font-family:monospace">{{data.asn}}</span>
    {% endif %}
    {% if data.get('language') %}
    <span class="k">Sprache</span><span class="v">{{data.language}}</span>
    {% endif %}
    {% if data.get('tags') %}
    <span class="k">Tags</span>
    <span class="v">{% for tag in data.tags %}
      <span class="badge gray" style="margin-right:3px">{{tag}}</span>
    {% endfor %}</span>
    {% endif %}
    {% if data.get('notes') %}
    <span class="k">Notizen</span>
    <span class="v" style="white-space:pre-wrap;color:#555">{{data.notes}}</span>
    {% endif %}
    <span class="k">Erstellt</span>
    <span class="v" style="color:#aaa;font-size:11px">
      {{data.get('created_at','')[:10]}} · {{data.get('created_by','')}}</span>
    {% if data.get('changed_at') and data.changed_at != data.get('created_at') %}
    <span class="k">Geändert</span>
    <span class="v" style="color:#aaa;font-size:11px">
      {{data.changed_at[:10]}}</span>
    {% endif %}
  </div>
</div>

<!-- Attachments -->
<div class="card">
  <h2>Anhänge
    {% if attachments %}<span style="font-weight:400;color:#aaa">
      ({{attachments|length}})</span>{% endif %}
  </h2>
  {% if attachments %}
  <table style="margin-bottom:8px">
    <tr><th>Datei</th><th>Typ</th><th>Hochgeladen</th><th></th></tr>
    {% for aref,a in attachments %}
    <tr>
      <td><a href="/vault/{{a.file_path.split('vault/')[-1]}}">
        {{a.get('filename','—')}}</a></td>
      <td style="color:#888;font-size:11px">{{a.get('file_ext','').upper()}}</td>
      <td style="color:#aaa;font-size:11px">{{a.get('uploaded_at','')[:10]}}</td>
      <td>
        {% if not a.get('primary') %}
        <form class="il" method="post"
              action="/document/{{ref}}/attachment/{{aref.split('/')[-1]}}/primary">
          <button class="btn sm ghost" title="Als primär setzen">★</button>
        </form>
        {% else %}
        <span style="color:#f59e0b;font-size:12px" title="Primärdatei">★</span>
        {% endif %}
      </td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p style="color:#aaa;font-size:12px;margin-bottom:8px">Noch keine Datei.</p>
  {% endif %}
  <form method="post" action="/document/{{ref}}/upload"
        enctype="multipart/form-data" style="display:flex;gap:8px;align-items:flex-end">
    <div style="flex:1"><label>Datei hochladen</label>
      <input type="file" name="file" accept=".pdf,.jpg,.jpeg,.png,.docx,.xlsx,.txt"
             style="padding:3px;font-size:12px"></div>
    <button class="btn sm" type="submit">Hochladen</button>
  </form>
</div>

<!-- Related docs -->
{% if related %}
<div class="card"><h2>Verknüpfte Dokumente</h2>
<table>
  <tr><th>ID</th><th>Titel</th><th>Kategorie</th><th></th></tr>
  {% for rref,rd in related %}
  <tr>
    <td style="font-size:11px;color:#888">{{rref.split('/')[-1]}}</td>
    <td><a href="/document/{{rref}}">{{rd.title[:45]}}</a></td>
    <td><span class="badge">{{rd.get('category','—')}}</span></td>
    <td>
      <form class="il" method="post" action="/document/{{ref}}/unlink/{{rref}}">
        <button class="btn sm ghost">×</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
</div>
{% endif %}

<!-- Reminders -->
<div class="card"><h2>Reminder</h2>
{% if reminders %}
<table style="margin-bottom:8px">
  <tr><th>Datum</th><th>Typ</th><th>Nachricht</th><th></th></tr>
  {% for rid,r in reminders %}
  <tr>
    <td style="white-space:nowrap;font-size:12px;
        {% if not r.get('fired') and r.remind_at <= today %}color:#e74c3c;
        font-weight:600{% else %}color:#666{% endif %}">
      {{r.remind_at}}</td>
    <td><span class="badge {% if r.get('fired') %}gray{% endif %}">
      {{rtypes.get(r.get('type',''),r.get('type',''))}}</span></td>
    <td style="font-size:12px;color:#555">{{r.get('message','')[:50]}}</td>
    <td>
      {% if not r.get('fired') %}
      <form class="il" method="post" action="/reminder/{{rid}}/dismiss">
        <button class="btn sm sec">✓</button>
      </form>
      {% else %}
      <span style="color:#aaa;font-size:11px">erledigt</span>
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
{% endif %}
<form method="post" action="/document/{{ref}}/reminder/new"
      style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
  <div><label>Datum</label>
    <input type="date" name="remind_at" style="width:140px"></div>
  <div><label>Typ</label>
    <select name="type" style="width:120px">
      {% for k,v in rtypes.items() %}
      <option value="{{k}}">{{v}}</option>
      {% endfor %}
    </select></div>
  <div style="flex:1"><label>Nachricht</label>
    <input type="text" name="message" placeholder="Optional…"></div>
  <div style="align-self:flex-end">
    <button class="btn sm" type="submit">+ Reminder</button>
  </div>
</form>
</div>

</div><!-- /left -->

<!-- RIGHT: PDF viewer -->
{% if has_pdf %}
<div style="width:420px;flex-shrink:0;position:sticky;top:0;height:calc(100vh - 80px)">
  <div class="card" style="height:100%;padding:8px;display:flex;flex-direction:column">
    <div style="display:flex;justify-content:space-between;align-items:center;
                margin-bottom:6px">
      <span style="font-size:11px;color:#888">
        {{primary_file.get('filename','')}}</span>
      <a class="btn sm sec"
         href="/vault/{{primary_file.file_path.split('vault/')[-1]}}"
         target="_blank">↗ Öffnen</a>
    </div>
    <iframe src="/vault/{{primary_file.file_path.split('vault/')[-1]}}"
            style="flex:1;border:none;border-radius:4px;width:100%;
                   min-height:0"></iframe>
  </div>
</div>
{% endif %}

</div><!-- /flex -->
"""
    T = render(data.get("title","Dokument"), C, active="documents",
               topbar_right=f'<a class="btn sm sec" href="/documents">← Liste</a> '
                            f'<a class="btn sm" href="/document/{ref}/edit">Bearbeiten</a>')
    return render_template_string(T,
        ref=ref, nid=nid, data=data, status=status, next_st=next_st,
        attachments=attachments, related=related, contact=contact,
        reminders=reminders, primary_file=primary_file, has_pdf=has_pdf,
        rtypes=REMINDER_TYPES, today=today())


# ---------------------------------------------------------------------------
# DOCUMENT STATUS
# ---------------------------------------------------------------------------

@app.route("/document/<path:ref>/status", methods=["POST"])
def document_status(ref):
    parts = ref.split("/", 1)
    if len(parts) != 2:
        return redirect(url_for("documents"))
    col, nid   = parts
    new_status = request.form.get("new_status", "")
    db         = get_db()
    data       = db.get_node(ref)
    if not data:
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("documents"))
    cur = data.get("status", "AKTIV")
    if DOC_STATUS_FLOW.get(cur) != new_status:
        flash(f"Übergang {cur}→{new_status} nicht erlaubt.", "err")
    else:
        db.update_node(col, nid, {"status": new_status, "changed_at": now()})
        flash(f"Status auf {new_status} gesetzt.")
    return redirect(url_for("document_detail", ref=ref))


# ---------------------------------------------------------------------------
# FILE UPLOAD + SERVE
# ---------------------------------------------------------------------------

@app.route("/document/<path:ref>/upload", methods=["POST"])
def document_upload(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("documents"))
    f = request.files.get("file")
    if not f or f.filename == "":
        flash("Keine Datei ausgewählt.", "err")
        return redirect(url_for("document_detail", ref=ref))
    if not allowed_file(f.filename):
        flash(f"Dateityp nicht erlaubt.", "err")
        return redirect(url_for("document_detail", ref=ref))

    nid_safe = ref.split("/", 1)[1].replace("/", "_")
    ext      = f.filename.rsplit(".", 1)[1].lower()
    import uuid as _uuid
    vault_name = f"{nid_safe}_{_uuid.uuid4().hex[:6]}.{ext}"
    vault_path = safe_vault_path(vault_name)
    f.save(vault_path)

    t          = now()
    a_nid      = db.next_id("attachments", prefix="ATT-", padding=5)
    # First attachment becomes primary
    existing   = [e for e in db.get_connected_edges(ref, direction="out").values()
                  if e.get("type") == "has_attachment"]
    is_primary = len(existing) == 0
    db.create_node("attachments", a_nid, {
        "filename":    f.filename,
        "file_path":   f"vault/{vault_name}",
        "file_ext":    ext,
        "primary":     is_primary,
        "uploaded_at": t,
        "uploaded_by": DEFAULT_USER,
    })
    db.create_edge(ref, f"attachments/{a_nid}", "has_attachment")
    flash(f"Datei hochgeladen.")
    return redirect(url_for("document_detail", ref=ref))


@app.route("/vault/<path:filename>")
def vault_serve(filename):
    safe = os.path.basename(filename)
    path = os.path.join(VAULT_DIR, safe)
    if not os.path.isfile(path):
        return "Datei nicht gefunden.", 404
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    inline = ext in ("pdf", "jpg", "jpeg", "png")
    return send_file(path, as_attachment=not inline,
                     download_name=safe)


@app.route("/document/<path:ref>/attachment/<att_nid>/primary", methods=["POST"])
def attachment_set_primary(ref, att_nid):
    db = get_db()
    # Clear primary on all attachments of this doc
    for eid, e in db.get_connected_edges(ref, direction="out"):
        if e.get("type") == "has_attachment":
            a     = db.get_node(e["target"])
            a_col = e["target"].split("/")[0]
            a_nid_other = e["target"].split("/")[1]
            if a:
                db.update_node(a_col, a_nid_other,
                               {"primary": a_nid_other == att_nid})
    flash("Primärdatei gesetzt.")
    return redirect(url_for("document_detail", ref=ref))


# ---------------------------------------------------------------------------
# DOCUMENT NEW / EDIT
# ---------------------------------------------------------------------------

def _doc_form(data, fields, contacts, action, submit_label, title):
    """Shared form template for new and edit."""
    C = f"""
<div style="max-width:780px">
<div class="card">
<form method="post" action="{action}">
  <div class="form-grid">
    <div style="grid-column:1/-1"><label>Titel *</label>
      <input type="text" name="title" value="{{{{data.get('title','')}}}}"
             required placeholder="Dokumententitel"></div>
    <div><label>Kategorie</label>
      <select name="category" onchange="this.form.submit()" style="width:100%">
        {{% for c in cats %}}
        <option value="{{{{c}}}}" {{%if c==data.get('category','')%}}selected{{%endif%}}>
          {{{{c}}}}</option>
        {{% endfor %}}
      </select></div>
    <div><label>Dokumenttyp</label>
      <select name="doc_type" style="width:100%">
        {{% for dt in dtypes %}}
        <option value="{{{{dt}}}}" {{%if dt==data.get('doc_type','')%}}selected{{%endif%}}>
          {{{{dt}}}}</option>
        {{% endfor %}}
      </select></div>
  </div>
  <div class="form-grid">
    {{% if 'issuer' in fields %}}
    <div><label>Aussteller</label>
      <input type="text" name="issuer"
             value="{{{{data.get('issuer','')}}}}" placeholder="AXA, Swisscom…"></div>
    {{% endif %}}
    {{% if 'doc_date' in fields %}}
    <div><label>Dokumentdatum</label>
      <input type="date" name="doc_date" value="{{{{data.get('doc_date','')}}}}" ></div>
    {{% endif %}}
    {{% if 'amount' in fields %}}
    <div><label>Betrag</label>
      <input type="number" name="amount" step="0.01"
             value="{{{{data.get('amount','')}}}}" placeholder="0.00"></div>
    {{% endif %}}
    {{% if 'currency' in fields %}}
    <div><label>Währung</label>
      <select name="currency" style="width:100%">
        {{% for cur in currencies %}}
        <option value="{{{{cur}}}}"
          {{%if cur==data.get('currency','CHF')%}}selected{{%endif%}}>
          {{{{cur}}}}</option>
        {{% endfor %}}
      </select></div>
    {{% endif %}}
    {{% if 'due_date' in fields %}}
    <div><label>Fälligkeit</label>
      <input type="date" name="due_date" value="{{{{data.get('due_date','')}}}}" ></div>
    {{% endif %}}
    {{% if 'expires_at' in fields %}}
    <div><label>Ablaufdatum</label>
      <input type="date" name="expires_at"
             value="{{{{data.get('expires_at','')}}}}" ></div>
    {{% endif %}}
    {{% if 'cancellable_until' in fields %}}
    <div><label>Kündbar bis</label>
      <input type="date" name="cancellable_until"
             value="{{{{data.get('cancellable_until','')}}}}" ></div>
    {{% endif %}}
    {{% if 'asn' in fields %}}
    <div><label>Archivnummer (physisch)</label>
      <input type="text" name="asn" value="{{{{data.get('asn','')}}}}"
             placeholder="Ordner-A/Fach-3"></div>
    {{% endif %}}
    {{% if 'language' in fields %}}
    <div><label>Sprache</label>
      <select name="language" style="width:100%">
        {{% for lg in langs %}}
        <option value="{{{{lg}}}}"
          {{%if lg==data.get('language','DE')%}}selected{{%endif%}}>
          {{{{lg}}}}</option>
        {{% endfor %}}
      </select></div>
    {{% endif %}}
    {{% if 'tags' in fields %}}
    <div style="grid-column:1/-1"><label>Tags (kommagetrennt)</label>
      <input type="text" name="tags"
             value="{{{{', '.join(data.get('tags',[]))}}}}"
             placeholder="rechnung, 2026, wichtig"></div>
    {{% endif %}}
  </div>
  {{% if 'notes' in fields %}}
  <div style="margin-bottom:10px"><label>Notizen</label>
    <textarea name="notes">{{{{data.get('notes','')}}}}</textarea></div>
  {{% endif %}}
  <div style="display:flex;gap:8px;margin-top:4px">
    <button class="btn" type="submit">{submit_label}</button>
    <a class="btn sec" href="javascript:history.back()">Abbrechen</a>
  </div>
</form>
</div>
</div>
"""
    return C


@app.route("/document/new", methods=["GET", "POST"])
def document_new():
    db  = get_db()
    cat = request.args.get("category", request.form.get("category", DOC_CATEGORIES[0]))

    if request.method == "POST" and "title" in request.form:
        title    = request.form.get("title", "").strip()
        if not title:
            flash("Titel ist Pflicht.", "err")
            return redirect(url_for("document_new"))
        t        = now()
        nid      = db.next_id("documents", prefix="DOC-", padding=5)
        amount   = request.form.get("amount", "").strip()
        tags_raw = request.form.get("tags", "").strip()
        tags     = [t2.strip() for t2 in tags_raw.split(",") if t2.strip()]
        doc_data = {
            "title":             title,
            "category":          request.form.get("category", cat),
            "doc_type":          request.form.get("doc_type", DOC_TYPES[0]),
            "issuer":            request.form.get("issuer", "").strip(),
            "doc_date":          request.form.get("doc_date", "").strip(),
            "due_date":          request.form.get("due_date", "").strip(),
            "expires_at":        request.form.get("expires_at", "").strip(),
            "cancellable_until": request.form.get("cancellable_until", "").strip(),
            "asn":               request.form.get("asn", "").strip(),
            "language":          request.form.get("language", "DE"),
            "notes":             request.form.get("notes", "").strip(),
            "status":            "AKTIV",
            "created_by":        DEFAULT_USER,
            "created_at":        t,
            "changed_at":        t,
        }
        if amount:
            try:
                doc_data["amount"]   = float(amount)
                doc_data["currency"] = request.form.get("currency", "CHF")
            except ValueError:
                pass
        if tags:
            doc_data["tags"] = tags
        # Remove empty strings
        doc_data = {k: v for k, v in doc_data.items() if v != "" and v != []}
        db.create_node("documents", nid, doc_data)
        fire_webhooks("document_created", {"ref": f"documents/{nid}", "title": title})
        flash(f"Dokument {nid} angelegt.")
        return redirect(url_for("document_detail", ref=f"documents/{nid}"))

    fields   = get_field_profile(cat)
    contacts = db.list_nodes("contacts") if "contacts" in db.list_collections() else {}
    C = _doc_form({}, fields, contacts,
                  action="/document/new",
                  submit_label="Anlegen",
                  title="Neues Dokument")
    T = render("Neues Dokument", C, active="documents")
    return render_template_string(T,
        data={"category": cat}, fields=fields,
        cats=DOC_CATEGORIES, dtypes=DOC_TYPES,
        currencies=CURRENCIES, langs=LANGUAGES, contacts=contacts)


@app.route("/document/<path:ref>/edit", methods=["GET", "POST"])
def document_edit(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("documents"))
    parts    = ref.split("/", 1)
    col, nid = parts[0], parts[1]
    cat      = request.form.get("category", data.get("category", DOC_CATEGORIES[0]))

    if request.method == "POST" and "title" in request.form:
        title    = request.form.get("title", "").strip()
        if not title:
            flash("Titel ist Pflicht.", "err")
            return redirect(url_for("document_edit", ref=ref))
        amount   = request.form.get("amount", "").strip()
        tags_raw = request.form.get("tags", "").strip()
        tags     = [t2.strip() for t2 in tags_raw.split(",") if t2.strip()]
        updates  = {
            "title":             title,
            "category":          cat,
            "doc_type":          request.form.get("doc_type", DOC_TYPES[0]),
            "issuer":            request.form.get("issuer", "").strip(),
            "doc_date":          request.form.get("doc_date", "").strip(),
            "due_date":          request.form.get("due_date", "").strip(),
            "expires_at":        request.form.get("expires_at", "").strip(),
            "cancellable_until": request.form.get("cancellable_until", "").strip(),
            "asn":               request.form.get("asn", "").strip(),
            "language":          request.form.get("language", "DE"),
            "notes":             request.form.get("notes", "").strip(),
            "changed_at":        now(),
        }
        if amount:
            try:
                updates["amount"]   = float(amount)
                updates["currency"] = request.form.get("currency", "CHF")
            except ValueError:
                pass
        if tags:
            updates["tags"] = tags
        db.update_node(col, nid, updates)
        flash("Gespeichert.")
        return redirect(url_for("document_detail", ref=ref))

    fields   = get_field_profile(cat)
    contacts = db.list_nodes("contacts") if "contacts" in db.list_collections() else {}
    C = _doc_form(data, fields, contacts,
                  action=f"/document/{ref}/edit",
                  submit_label="Speichern",
                  title=f"Bearbeiten: {data.get('title','')}")
    T = render(f"Bearbeiten: {data.get('title','')}", C, active="documents")
    return render_template_string(T,
        data=data, fields=fields,
        cats=DOC_CATEGORIES, dtypes=DOC_TYPES,
        currencies=CURRENCIES, langs=LANGUAGES, contacts=contacts)


# ---------------------------------------------------------------------------
# CONTACTS
# ---------------------------------------------------------------------------

@app.route("/contacts")
def contacts():
    db   = get_db()
    all_c = db.list_nodes("contacts") if "contacts" in db.list_collections() else {}
    c_sorted = sorted(all_c.items(), key=lambda x: x[1].get("name", ""))
    C = """
<div class="filterbar" style="justify-content:flex-end">
  <a class="btn sm" href="/contact/new">+ Kontakt</a>
</div>
{% if contacts %}
<table>
  <tr>
    <th class="srt" onclick="srt(this)">Name</th>
    <th class="srt" onclick="srt(this)">Kürzel</th>
    <th class="srt" onclick="srt(this)">Kategorie</th>
    <th class="srt" onclick="srt(this)">Website</th>
    <th>Dokumente</th>
  </tr>
  {% for nid,c in contacts %}
  <tr>
    <td><a href="/contact/contacts/{{nid}}"><strong>{{c.name}}</strong></a></td>
    <td style="color:#888;font-size:12px">{{c.get('short','')}}</td>
    <td><span class="badge gray">{{c.get('category','—')}}</span></td>
    <td style="font-size:12px">
      {% if c.get('website') %}
      <a href="{{c.website}}" target="_blank" rel="noopener">{{c.website[:35]}}</a>
      {% else %}—{% endif %}</td>
    <td style="font-size:12px;color:#888">{{doc_counts.get(nid,0)}}</td>
  </tr>
  {% endfor %}
</table>
{% else %}
<div class="card"><p style="color:#aaa">Keine Kontakte.</p>
  <a class="btn sm" href="/contact/new" style="margin-top:8px">Ersten Kontakt anlegen</a>
</div>
{% endif %}
"""
    # Count docs per contact
    doc_counts = {}
    if "documents" in db.list_collections():
        for d in db.list_nodes("documents").values():
            ref = d.get("issuer_ref", "")
            if ref:
                nid_c = ref.split("/")[-1]
                doc_counts[nid_c] = doc_counts.get(nid_c, 0) + 1

    T = render("Kontakte", C, active="contacts",
               topbar_right='<a class="btn sm" href="/contact/new">+ Kontakt</a>')
    return render_template_string(T, contacts=c_sorted, doc_counts=doc_counts)


@app.route("/contact/new", methods=["GET", "POST"])
def contact_new():
    db = get_db()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name ist Pflicht.", "err")
            return redirect(url_for("contact_new"))
        nid = db.next_id("contacts", prefix="CON-", padding=5)
        db.create_node("contacts", nid, {
            "name":     name,
            "short":    request.form.get("short", "").strip(),
            "category": request.form.get("category", CONTACT_CATEGORIES[0]),
            "address":  request.form.get("address", "").strip(),
            "website":  request.form.get("website", "").strip(),
            "notes":    request.form.get("notes", "").strip(),
        })
        flash(f"Kontakt {nid} angelegt.")
        return redirect(url_for("contact_detail", ref=f"contacts/{nid}"))

    C = """
<div style="max-width:600px"><div class="card">
<form method="post">
  <div class="form-grid">
    <div style="grid-column:1/-1"><label>Name *</label>
      <input type="text" name="name" required placeholder="AXA Versicherungen AG"></div>
    <div><label>Kürzel</label>
      <input type="text" name="short" placeholder="AXA"></div>
    <div><label>Kategorie</label>
      <select name="category">
        {% for c in cats %}<option value="{{c}}">{{c}}</option>{% endfor %}
      </select></div>
    <div style="grid-column:1/-1"><label>Adresse</label>
      <input type="text" name="address" placeholder="Strasse, PLZ Ort"></div>
    <div style="grid-column:1/-1"><label>Website</label>
      <input type="text" name="website" placeholder="https://…"></div>
  </div>
  <div style="margin-bottom:10px"><label>Notizen</label>
    <textarea name="notes" style="min-height:60px"></textarea></div>
  <div style="display:flex;gap:8px">
    <button class="btn">Anlegen</button>
    <a class="btn sec" href="/contacts">Abbrechen</a>
  </div>
</form>
</div></div>
"""
    T = render("Neuer Kontakt", C, active="contacts")
    return render_template_string(T, cats=CONTACT_CATEGORIES)


@app.route("/contact/<path:ref>")
def contact_detail(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Kontakt nicht gefunden.", "err")
        return redirect(url_for("contacts"))
    nid = ref.split("/", 1)[1]

    # Documents linked to this contact
    linked_docs = []
    if "documents" in db.list_collections():
        for dnid, d in db.list_nodes("documents").items():
            if d.get("issuer_ref") == ref:
                linked_docs.append((dnid, d))
    linked_docs.sort(key=lambda x: x[1].get("doc_date", ""), reverse=True)

    C = """
<div style="max-width:700px">
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start">
    <div>
      <h1 style="font-size:16px;margin-bottom:4px">{{data.name}}</h1>
      <span class="badge gray">{{data.get('category','—')}}</span>
      {% if data.get('short') %}
      <span style="color:#aaa;font-size:12px;margin-left:6px">{{data.short}}</span>
      {% endif %}
    </div>
    <a class="btn sm sec" href="/contact/{{ref}}/edit">Bearbeiten</a>
  </div>
  <div class="kv" style="margin-top:10px">
    {% if data.get('address') %}
    <span class="k">Adresse</span><span class="v">{{data.address}}</span>
    {% endif %}
    {% if data.get('website') %}
    <span class="k">Website</span>
    <span class="v"><a href="{{data.website}}" target="_blank" rel="noopener">
      {{data.website}}</a></span>
    {% endif %}
    {% if data.get('notes') %}
    <span class="k">Notizen</span>
    <span class="v" style="white-space:pre-wrap;color:#555">{{data.notes}}</span>
    {% endif %}
  </div>
</div>
{% if linked_docs %}
<div class="card"><h2>Dokumente ({{linked_docs|length}})</h2>
<table>
  <tr><th>ID</th><th>Titel</th><th>Kategorie</th><th>Datum</th><th>Status</th></tr>
  {% for dnid,d in linked_docs %}
  <tr>
    <td style="font-size:11px;color:#888">{{dnid}}</td>
    <td><a href="/document/documents/{{dnid}}">{{d.title[:45]}}</a></td>
    <td><span class="badge">{{d.get('category','—')}}</span></td>
    <td style="font-size:12px;color:#888">{{d.get('doc_date','—')}}</td>
    <td><span class="badge {% if d.get('status','AKTIV')=='AKTIV' %}green
        {% else %}gray{% endif %}">{{d.get('status','AKTIV')}}</span></td>
  </tr>
  {% endfor %}
</table></div>
{% endif %}
</div>
"""
    T = render(data.get("name", "Kontakt"), C, active="contacts",
               topbar_right='<a class="btn sm sec" href="/contacts">← Liste</a>')
    return render_template_string(T, ref=ref, nid=nid, data=data,
                                  linked_docs=linked_docs)


@app.route("/contact/<path:ref>/edit", methods=["GET", "POST"])
def contact_edit(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Kontakt nicht gefunden.", "err")
        return redirect(url_for("contacts"))
    col, nid = ref.split("/", 1)

    if request.method == "POST":
        db.update_node(col, nid, {
            "name":     request.form.get("name", "").strip(),
            "short":    request.form.get("short", "").strip(),
            "category": request.form.get("category", ""),
            "address":  request.form.get("address", "").strip(),
            "website":  request.form.get("website", "").strip(),
            "notes":    request.form.get("notes", "").strip(),
        })
        flash("Gespeichert.")
        return redirect(url_for("contact_detail", ref=ref))

    C = """
<div style="max-width:600px"><div class="card">
<form method="post">
  <div class="form-grid">
    <div style="grid-column:1/-1"><label>Name *</label>
      <input type="text" name="name" value="{{data.name}}" required></div>
    <div><label>Kürzel</label>
      <input type="text" name="short" value="{{data.get('short','')}}"></div>
    <div><label>Kategorie</label>
      <select name="category">
        {% for c in cats %}
        <option value="{{c}}" {% if c==data.get('category') %}selected{% endif %}>
          {{c}}</option>
        {% endfor %}
      </select></div>
    <div style="grid-column:1/-1"><label>Adresse</label>
      <input type="text" name="address" value="{{data.get('address','')}}"></div>
    <div style="grid-column:1/-1"><label>Website</label>
      <input type="text" name="website" value="{{data.get('website','')}}"></div>
  </div>
  <div style="margin-bottom:10px"><label>Notizen</label>
    <textarea name="notes">{{data.get('notes','')}}</textarea></div>
  <div style="display:flex;gap:8px">
    <button class="btn">Speichern</button>
    <a class="btn sec" href="/contact/{{ref}}">Abbrechen</a>
  </div>
</form>
</div></div>
"""
    T = render(f"Bearbeiten: {data.get('name','')}", C, active="contacts")
    return render_template_string(T, ref=ref, data=data, cats=CONTACT_CATEGORIES)


# ---------------------------------------------------------------------------
# REMINDERS
# ---------------------------------------------------------------------------

@app.route("/reminders")
def reminders():
    db   = get_db()
    t    = today()
    rems = db.list_nodes("reminders") if "reminders" in db.list_collections() else {}

    open_r = sorted(
        [(nid, r) for nid, r in rems.items() if not r.get("fired")],
        key=lambda x: x[1].get("remind_at", "9999"))
    done_r = sorted(
        [(nid, r) for nid, r in rems.items() if r.get("fired")],
        key=lambda x: x[1].get("remind_at", ""), reverse=True)[:20]

    C = """
{% if open_r %}
<div class="card"><h2>Offen ({{open_r|length}})</h2>
<table>
  <tr><th class="srt" onclick="srt(this)">Datum</th>
      <th class="srt" onclick="srt(this)">Typ</th>
      <th>Dokument</th>
      <th class="srt" onclick="srt(this)">Nachricht</th>
      <th></th></tr>
  {% for nid,r in open_r %}
  <tr>
    <td style="white-space:nowrap;font-weight:600;
        color:{% if r.remind_at <= today %}#e74c3c{% else %}#1a1d23{% endif %}">
      {{r.remind_at}}</td>
    <td><span class="badge {% if r.remind_at <= today %}red{% endif %}">
      {{rtypes.get(r.get('type',''),r.get('type',''))}}</span></td>
    <td style="font-size:12px">
      <a href="/document/{{r.get('doc_ref','')}}">
        {{r.get('doc_title','—')[:40]}}</a></td>
    <td style="font-size:12px;color:#555">{{r.get('message','—')}}</td>
    <td>
      <form class="il" method="post" action="/reminder/{{nid}}/dismiss">
        <button class="btn sm sec">✓ Erledigt</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table></div>
{% else %}
<div class="card"><p style="color:#aaa">Keine offenen Reminder.</p></div>
{% endif %}

{% if done_r %}
<div class="card"><h2>Erledigt (letzte 20)</h2>
<table>
  <tr><th>Datum</th><th>Typ</th><th>Dokument</th><th>Nachricht</th></tr>
  {% for nid,r in done_r %}
  <tr style="opacity:.55">
    <td style="font-size:12px;white-space:nowrap">{{r.remind_at}}</td>
    <td><span class="badge gray">
      {{rtypes.get(r.get('type',''),r.get('type',''))}}</span></td>
    <td style="font-size:12px">{{r.get('doc_title','—')[:40]}}</td>
    <td style="font-size:12px;color:#888">{{r.get('message','—')}}</td>
  </tr>
  {% endfor %}
</table></div>
{% endif %}
"""
    T = render("Reminder", C, active="reminders")
    return render_template_string(T, open_r=open_r, done_r=done_r,
                                  rtypes=REMINDER_TYPES, today=t)


@app.route("/document/<path:ref>/reminder/new", methods=["POST"])
def reminder_new(ref):
    db        = get_db()
    remind_at = request.form.get("remind_at", "").strip()
    rtype     = request.form.get("type", "CUSTOM")
    message   = request.form.get("message", "").strip()
    if not remind_at:
        flash("Datum ist Pflicht.", "err")
        return redirect(url_for("document_detail", ref=ref))
    doc  = db.get_node(ref)
    nid  = db.next_id("reminders", prefix="REM-", padding=5)
    db.create_node("reminders", nid, {
        "doc_ref":   ref,
        "doc_title": (doc or {}).get("title", ""),
        "remind_at": remind_at,
        "type":      rtype,
        "message":   message,
        "fired":     False,
    })
    fire_webhooks("reminder_created", {
        "ref": f"reminders/{nid}", "doc_ref": ref,
        "remind_at": remind_at, "type": rtype,
    })
    flash("Reminder gesetzt.")
    return redirect(url_for("document_detail", ref=ref))


@app.route("/reminder/<nid>/dismiss", methods=["POST"])
def reminder_dismiss(nid):
    db = get_db()
    db.update_node("reminders", nid, {"fired": True, "fired_at": now()})
    flash("Reminder erledigt.")
    return redirect(request.referrer or url_for("reminders"))


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------

@app.route("/search")
def search():
    db = get_db()
    q  = request.args.get("q", "").strip()
    results = []

    if q:
        ql = q.lower()
        if "documents" in db.list_collections():
            for nid, d in db.list_nodes("documents").items():
                score = 0
                if ql in d.get("title",    "").lower(): score += 3
                if ql in d.get("issuer",   "").lower(): score += 2
                if ql in d.get("notes",    "").lower(): score += 1
                if ql in d.get("asn",      "").lower(): score += 2
                if ql in d.get("category", "").lower(): score += 1
                if ql in nid.lower():                   score += 2
                if score:
                    results.append(("document", f"documents/{nid}", nid, d, score))

        if "contacts" in db.list_collections():
            for nid, c in db.list_nodes("contacts").items():
                score = 0
                if ql in c.get("name",  "").lower(): score += 3
                if ql in c.get("short", "").lower(): score += 2
                if ql in c.get("notes", "").lower(): score += 1
                if score:
                    results.append(("contact", f"contacts/{nid}", nid, c, score))

        results.sort(key=lambda x: -x[4])

    C = """
<div style="max-width:800px">
{% if q %}
<div style="margin-bottom:10px;font-size:12px;color:#888">
  {{results|length}} Treffer für <strong>«{{q}}»</strong>
</div>
{% if results %}
<table>
  <tr><th>Typ</th><th>ID</th><th>Titel / Name</th><th>Details</th></tr>
  {% for rtype,ref,nid,d,score in results %}
  <tr>
    <td><span class="badge {% if rtype=='contact' %}gray{% endif %}">
      {{rtype|capitalize}}</span></td>
    <td style="font-size:11px;color:#888;white-space:nowrap">{{nid}}</td>
    <td>
      {% if rtype=='document' %}
      <a href="/document/{{ref}}"><strong>{{d.get('title','—')}}</strong></a>
      {% else %}
      <a href="/contact/{{ref}}"><strong>{{d.get('name','—')}}</strong></a>
      {% endif %}
    </td>
    <td style="font-size:12px;color:#666">
      {% if rtype=='document' %}
        {{d.get('issuer','')}}
        {% if d.get('doc_date') %} · {{d.doc_date}}{% endif %}
        {% if d.get('amount') %} · {{d.amount}} {{d.get('currency','')}}{% endif %}
        <span class="badge {% if d.get('status','AKTIV')=='AKTIV' %}green
          {% else %}gray{% endif %}" style="margin-left:4px">
          {{d.get('status','AKTIV')}}</span>
      {% else %}
        {{d.get('category','')}}
      {% endif %}
    </td>
  </tr>
  {% endfor %}
</table>
{% else %}
<div class="card"><p style="color:#aaa">Keine Treffer für «{{q}}».</p></div>
{% endif %}
{% else %}
<div class="card" style="color:#aaa;text-align:center;padding:32px">
  Suchbegriff eingeben…
</div>
{% endif %}
</div>
"""
    T = BASE
    for blk, val in [
        ("{% block title %}HomeDMS{% endblock %}", "Suche"),
        ("{% block content %}{% endblock %}", C),
        ("{% block topbar_right %}{% endblock %}", ""),
        ("{% block search_val %}{% endblock %}", q),
        ("{% if active=='search' %}active{% endif %}", "active"),
        ("{% if active=='dashboard' %}active{% endif %}", ""),
        ("{% if active=='inbox' %}active{% endif %}", ""),
        ("{% if active=='reminders' %}active{% endif %}", ""),
        ("{% if active=='documents' %}active{% endif %}", ""),
        ("{% if active=='contacts' %}active{% endif %}", ""),
        ("{% if active=='settings' %}active{% endif %}", ""),
        ("{% if inbox_count %}<span class=\"sb-badge\">{{inbox_count}}</span>{% endif %}",
         f'<span class="sb-badge">{len(inbox_files())}</span>' if inbox_files() else ""),
        ("{% if reminder_count %}<span class=\"sb-badge\">{{reminder_count}}</span>{% endif %}", ""),
    ]:
        T = T.replace(blk, val)
    return render_template_string(T, q=q, results=results)


# ---------------------------------------------------------------------------
# INBOX
# ---------------------------------------------------------------------------

@app.route("/inbox")
def inbox():
    files = inbox_files()
    C = """
<div style="margin-bottom:10px;font-size:12px;color:#888">
  {{files|length}} Datei(en) in der Inbox
  <span style="margin-left:8px;color:#aaa">Ordner: _dms_db/inbox/</span>
</div>
{% if files %}
<table>
  <tr><th>Dateiname</th><th>Größe</th><th>Geändert</th><th></th></tr>
  {% for f in files %}
  <tr>
    <td><strong>{{f.name}}</strong></td>
    <td style="font-size:12px;color:#888">{{f.size}}</td>
    <td style="font-size:12px;color:#888">{{f.mtime}}</td>
    <td style="display:flex;gap:6px">
      <a class="btn sm"
         href="/document/new?inbox_file={{f.name|urlencode}}">
        DocInfoRecord anlegen</a>
      <form class="il" method="post" action="/inbox/{{f.name|urlencode}}/discard">
        <button class="btn sm danger">Verwerfen</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% else %}
<div class="card" style="text-align:center;padding:32px;color:#aaa">
  Inbox ist leer.<br>
  <span style="font-size:12px">Dateien in <code>_dms_db/inbox/</code> kopieren.</span>
</div>
{% endif %}
"""
    import os as _os
    file_infos = []
    for fname in files:
        path  = _os.path.join(INBOX_DIR, fname)
        stat  = _os.stat(path)
        size  = f"{stat.st_size // 1024} KB" if stat.st_size > 1024 else f"{stat.st_size} B"
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        file_infos.append(type("F", (), {"name": fname, "size": size, "mtime": mtime})())

    T = render("Inbox", C, active="inbox")
    return render_template_string(T, files=file_infos)


@app.route("/inbox/<path:filename>/discard", methods=["POST"])
def inbox_discard(filename):
    safe = os.path.basename(filename)
    path = os.path.join(INBOX_DIR, safe)
    if os.path.isfile(path):
        os.remove(path)
        flash(f"{safe} verworfen.")
    return redirect(url_for("inbox"))


# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
def settings():
    db = get_db()

    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "save_profile":
            cat    = request.form.get("category", "")
            fields = [f.strip() for f in
                      request.form.get("fields", "").split(",") if f.strip()]
            if cat and fields:
                existing = {nid: p for nid, p in
                            db.list_nodes("field_profiles").items()
                            if p.get("category") == cat}
                if existing:
                    nid_p = list(existing.keys())[0]
                    db.update_node("field_profiles", nid_p, {"fields": fields})
                else:
                    nid_p = db.next_id("field_profiles", prefix="FP-", padding=3)
                    db.create_node("field_profiles", nid_p,
                                   {"category": cat, "fields": fields})
                flash(f"Feldprofil für '{cat}' gespeichert.")
        elif action == "reset_profile":
            cat = request.form.get("category", "")
            for nid_p, p in list(db.list_nodes("field_profiles").items()):
                if p.get("category") == cat:
                    db.soft_delete("field_profiles", nid_p)
            flash(f"Feldprofil für '{cat}' zurückgesetzt.")
        return redirect(url_for("settings"))

    profiles = {}
    if "field_profiles" in db.list_collections():
        for p in db.list_nodes("field_profiles").values():
            profiles[p["category"]] = p.get("fields", [])

    webhooks = list(db.list_nodes("webhooks").items()) \
        if "webhooks" in db.list_collections() else []

    C = """
<!-- Field Profiles -->
<div class="card" style="margin-bottom:10px">
  <h2>Feldprofile</h2>
  <p style="font-size:12px;color:#888;margin-bottom:10px">
    Welche Felder pro Kategorie im Formular erscheinen.
    Verfügbare Felder: {{all_fields|join(', ')}}</p>
  <table style="margin-bottom:10px">
    <tr><th>Kategorie</th><th>Aktive Felder</th><th></th></tr>
    {% for cat in cats %}
    <tr>
      <td style="font-weight:600;white-space:nowrap">{{cat}}</td>
      <td style="font-size:12px;color:#555">
        {{profiles.get(cat, defaults.get(cat, [])) | join(', ')}}</td>
      <td>
        <form method="post" style="display:flex;gap:6px;align-items:center">
          <input type="hidden" name="action" value="save_profile">
          <input type="hidden" name="category" value="{{cat}}">
          <input type="text" name="fields"
                 value="{{profiles.get(cat, defaults.get(cat, [])) | join(', ')}}"
                 style="width:340px;font-size:12px">
          <button class="btn sm">Speichern</button>
          {% if cat in profiles %}
          <button class="btn sm sec" formaction="/settings"
                  onclick="this.form.action.value='reset_profile'">Reset</button>
          {% endif %}
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
</div>

<!-- Webhooks -->
<div class="card" style="margin-bottom:10px">
  <h2>Webhooks</h2>
  {% if webhooks %}
  <table style="margin-bottom:10px">
    <tr><th>URL</th><th>Event</th><th>Beschreibung</th><th>Aktiv</th><th></th></tr>
    {% for nid,wh in webhooks %}
    <tr>
      <td style="font-size:12px;max-width:200px;overflow:hidden;
                 text-overflow:ellipsis">{{wh.url}}</td>
      <td><span class="badge gray">{{wh.get('event','—')}}</span></td>
      <td style="font-size:12px;color:#888">{{wh.get('description','')}}</td>
      <td>
        <form class="il" method="post" action="/settings/webhook/{{nid}}/toggle">
          <button class="btn sm {% if wh.get('active') %}warn{% else %}sec{% endif %}">
            {% if wh.get('active') %}AN{% else %}AUS{% endif %}</button>
        </form>
      </td>
      <td style="display:flex;gap:4px">
        <form class="il" method="post" action="/settings/webhook/{{nid}}/test">
          <button class="btn sm ghost">Test</button>
        </form>
        <form class="il" method="post" action="/settings/webhook/{{nid}}/delete">
          <button class="btn sm danger">×</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
  <form method="post" action="/settings/webhook/new"
        style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
    <div style="flex:2"><label>URL</label>
      <input type="text" name="url" placeholder="https://…"></div>
    <div><label>Event</label>
      <select name="event" style="width:160px">
        <option value="all">Alle</option>
        <option value="document_created">document_created</option>
        <option value="reminder_created">reminder_created</option>
      </select></div>
    <div style="flex:1"><label>Beschreibung</label>
      <input type="text" name="description" placeholder="z.B. Teams Kanal"></div>
    <div style="align-self:flex-end">
      <button class="btn sm">+ Webhook</button>
    </div>
  </form>
</div>

<!-- GC -->
<div class="card">
  <h2>Wartung</h2>
  <form method="post" action="/gc" style="display:inline">
    <button class="btn sm warn">Garbage Collection starten</button>
  </form>
  <span style="font-size:12px;color:#aaa;margin-left:10px">
    Bereinigt soft-deleted Nodes und kompaktiert _temp.json Dateien.</span>
</div>
"""
    T = render("Einstellungen", C, active="settings")
    return render_template_string(T,
        cats=DOC_CATEGORIES, profiles=profiles,
        defaults=DEFAULT_FIELD_PROFILES,
        all_fields=list(ALL_DOC_FIELDS.keys()),
        webhooks=webhooks)


@app.route("/settings/webhook/new", methods=["POST"])
def webhook_new():
    db  = get_db()
    url = request.form.get("url", "").strip()
    if not url:
        flash("URL ist Pflicht.", "err")
        return redirect(url_for("settings"))
    nid = db.next_id("webhooks", prefix="WHK-", padding=4)
    db.create_node("webhooks", nid, {
        "url":         url,
        "event":       request.form.get("event", "all"),
        "description": request.form.get("description", "").strip(),
        "active":      True,
    })
    flash("Webhook angelegt.")
    return redirect(url_for("settings"))


@app.route("/settings/webhook/<nid>/toggle", methods=["POST"])
def webhook_toggle(nid):
    db   = get_db()
    node = db.get_node(f"webhooks/{nid}")
    if node:
        db.update_node("webhooks", nid, {"active": not node.get("active", True)})
    return redirect(url_for("settings"))


@app.route("/settings/webhook/<nid>/delete", methods=["POST"])
def webhook_delete(nid):
    db = get_db()
    db.soft_delete("webhooks", nid)
    flash("Webhook gelöscht.")
    return redirect(url_for("settings"))


@app.route("/settings/webhook/<nid>/test", methods=["POST"])
def webhook_test(nid):
    db = get_db()
    wh = db.get_node(f"webhooks/{nid}")
    if not wh:
        flash("Webhook nicht gefunden.", "err")
        return redirect(url_for("settings"))
    try:
        payload = json.dumps({"event": "test", "source": "HomeDMS",
                              "timestamp": now()}).encode()
        req = _urllib.Request(wh["url"], data=payload,
                  headers={"Content-Type": "application/json"}, method="POST")
        _urllib.urlopen(req, timeout=3)
        flash(f"Test gesendet an {wh['url']}.")
    except Exception as e:
        flash(f"Fehler: {e}", "err")
    return redirect(url_for("settings"))


@app.route("/gc", methods=["POST"])
def gc():
    gc_engine = MaintenanceEngine(DB_ROOT)
    stats     = gc_engine.run_garbage_collection(verbose=False)
    flash(f"GC abgeschlossen: {stats}")
    return redirect(request.referrer or url_for("settings"))


if __name__ == "__main__":
    app.run(debug=True, port=5002)








