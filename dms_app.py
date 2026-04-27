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


if __name__ == "__main__":
    app.run(debug=True, port=5002)



