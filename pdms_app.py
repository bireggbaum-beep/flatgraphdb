"""
pDMS + Equipment Modul — Demo-App auf FlatGraphDB
Run: pip install flask && python pdms_app.py
"""

import os
import json
from datetime import datetime, timezone
from flask import Flask, render_template_string, request, redirect, url_for, flash
from flatgraph import FlatGraphDB, MaintenanceEngine

app = Flask(__name__)
app.secret_key = "pdms-secret"

DB_ROOT = os.path.join(os.path.dirname(__file__), "_pdms_db")
DEFAULT_USER = "User"

STATUS_FLOW = {"WK": "FR", "FR": "OB", "OB": None}
STATUS_LABEL = {"WK": "In Arbeit", "FR": "Freigegeben", "OB": "Veraltet"}
STATUS_COLOR = {"WK": "#4361ee", "FR": "#2a9d60", "OB": "#888"}
CATEGORIES = {"M": "Maschine", "F": "Fahrzeug", "I": "Instrument", "E": "Elektro"}
EQ_STATUS = {"AKTIV": "#2a9d60", "INAKTIV": "#e67e22", "STILLGELEGT": "#888"}

LOCKED_FIELDS = {
    "_global":              {"created_at", "created_by", "_deletion_flag"},
    "documents":            {"doc_nr", "doc_type", "doc_part", "status"},
    "equipments":           {"status"},
    "functional_locations": set(),
    "originals":            set(),
    "doc_types":            set(),
}


def get_locked(col):
    return LOCKED_FIELDS["_global"] | LOCKED_FIELDS.get(col, set())


def field_input(name, value):
    if name.endswith("_at"):
        return (str(value) if value is not None else "", True)
    str_value = "" if value is None else str(value)
    escaped   = str_value.replace('"', "&quot;").replace("<", "&lt;")
    if isinstance(value, bool):
        sel_t = "selected" if value     else ""
        sel_f = "selected" if not value else ""
        html  = (f'<select name="{name}">'
                 f'<option value="true" {sel_t}>Ja</option>'
                 f'<option value="false" {sel_f}>Nein</option>'
                 f'</select>')
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        html = f'<input type="number" name="{name}" value="{escaped}">'
    elif name == "language":
        opts = "".join(f'<option value="{c}" {"selected" if str_value == c else ""}>{c}</option>'
                       for c in ("DE", "EN"))
        html = f'<select name="{name}">{opts}</select>'
    elif name == "category":
        opts = "".join(f'<option value="{k}" {"selected" if str_value == k else ""}>{k} – {v}</option>'
                       for k, v in CATEGORIES.items())
        html = f'<select name="{name}">{opts}</select>'
    elif name == "file_type":
        opts = "".join(f'<option value="{ft}" {"selected" if str_value == ft else ""}>{ft}</option>'
                       for ft in ("PDF", "DXF", "DOCX", "TXT"))
        html = f'<select name="{name}">{opts}</select>'
    else:
        html = f'<input type="text" name="{name}" value="{escaped}">'
    return (html, False)


def now():
    return datetime.now(timezone.utc).isoformat()


def get_db():
    return FlatGraphDB(root_dir=DB_ROOT)


def seed_if_empty():
    db = get_db()
    if db.list_collections():
        return

    # Dokumenttypen
    db.create_node("doc_types", "DRW", {"code": "DRW", "description": "Zeichnung"})
    db.create_node("doc_types", "MAN", {"code": "MAN", "description": "Handbuch"})
    db.create_node("doc_types", "QSP", {"code": "QSP", "description": "Prüfplan"})
    db.create_node("doc_types", "SPE", {"code": "SPE", "description": "Spezifikation"})

    # Functional Locations
    db.create_node("functional_locations", "WERK-01", {
        "description": "Werk 01 – Hauptwerk", "category": "Werk"})
    db.create_node("functional_locations", "WERK-01-HALLE-A", {
        "description": "Halle A – Fertigung", "category": "Halle"})
    db.create_node("functional_locations", "WERK-01-HALLE-B", {
        "description": "Halle B – Montage", "category": "Halle"})
    db.create_edge("functional_locations/WERK-01", "functional_locations/WERK-01-HALLE-A", "hat_unterbereich")
    db.create_edge("functional_locations/WERK-01", "functional_locations/WERK-01-HALLE-B", "hat_unterbereich")

    # Equipment
    db.create_node("equipments", "EQ-00001", {
        "description": "CNC Fräsmaschine DMG Mori", "category": "M",
        "manufacturer": "DMG Mori", "model": "DMU 50",
        "serial_nr": "DMG-2021-4471", "construction_year": 2021,
        "status": "AKTIV", "cost_center": "K-100"})
    db.create_node("equipments", "EQ-00002", {
        "description": "Spindelmotor EQ-00001", "category": "E",
        "manufacturer": "Siemens", "model": "1FT7-132",
        "serial_nr": "SIE-2021-0092", "construction_year": 2021,
        "status": "AKTIV", "cost_center": "K-100"})
    db.create_node("equipments", "EQ-00003", {
        "description": "Steuerungseinheit EQ-00001", "category": "E",
        "manufacturer": "Siemens", "model": "SINUMERIK 840D",
        "serial_nr": "SIE-2021-0093", "construction_year": 2021,
        "status": "AKTIV", "cost_center": "K-100"})
    db.create_node("equipments", "EQ-00004", {
        "description": "Schweißroboter KUKA KR 16", "category": "M",
        "manufacturer": "KUKA", "model": "KR 16-2",
        "serial_nr": "KUK-2019-8812", "construction_year": 2019,
        "status": "AKTIV", "cost_center": "K-200"})

    # Equipment Hierarchie (EQ-00001 ist Parent von EQ-00002 und EQ-00003)
    db.create_edge("equipments/EQ-00001", "equipments/EQ-00002", "ist_uebergeordnet", cascade_delete=True)
    db.create_edge("equipments/EQ-00001", "equipments/EQ-00003", "ist_uebergeordnet", cascade_delete=True)

    # Equipment → Functional Location
    db.create_edge("equipments/EQ-00001", "functional_locations/WERK-01-HALLE-A", "installiert_in",
                   meta={"since": "2021-06-01"})
    db.create_edge("equipments/EQ-00004", "functional_locations/WERK-01-HALLE-B", "installiert_in",
                   meta={"since": "2019-03-15"})

    # Dokumente
    t = now()
    db.create_node("documents", "10001-DRW", {
        "doc_nr": 10001, "doc_type": "DRW", "doc_part": "000",
        "title": "CNC Fräsmaschine DMG Mori – Gesamtzeichnung",
        "status": "FR", "language": "DE", "lab_office": "Konstruktion",
        "created_by": DEFAULT_USER, "created_at": t, "changed_at": t})
    db.create_node("documents", "10002-MAN", {
        "doc_nr": 10002, "doc_type": "MAN", "doc_part": "000",
        "title": "Betriebsanleitung DMU 50",
        "status": "FR", "language": "DE", "lab_office": "Dokumentation",
        "created_by": DEFAULT_USER, "created_at": t, "changed_at": t})
    db.create_node("documents", "10003-QSP", {
        "doc_nr": 10003, "doc_type": "QSP", "doc_part": "000",
        "title": "Prüfplan Spindelmotor Wartung",
        "status": "WK", "language": "DE", "lab_office": "Qualität",
        "created_by": DEFAULT_USER, "created_at": t, "changed_at": t})
    db.create_node("documents", "10004-DRW", {
        "doc_nr": 10004, "doc_type": "DRW", "doc_part": "000",
        "title": "KUKA KR 16 – Aufstellungsplan",
        "status": "FR", "language": "DE", "lab_office": "Konstruktion",
        "created_by": DEFAULT_USER, "created_at": t, "changed_at": t})
    db.create_node("documents", "10005-SPE", {
        "doc_nr": 10005, "doc_type": "SPE", "doc_part": "000",
        "title": "Spezifikation Schweißnahtgüte",
        "status": "OB", "language": "DE", "lab_office": "Qualität",
        "created_by": DEFAULT_USER, "created_at": t, "changed_at": t})

    # Fake-Asset für DOC 10001
    vault_path = os.path.join(DB_ROOT, "vault", "10001-DRW_Gesamtzeichnung.pdf")
    os.makedirs(os.path.dirname(vault_path), exist_ok=True)
    with open(vault_path, "w") as f:
        f.write("FAKE PDF")
    orig_ref = db.create_node("originals", "ORI-00001", {
        "filename": "10001-DRW_Gesamtzeichnung.pdf",
        "file_type": "PDF",
        "datei": "vault/10001-DRW_Gesamtzeichnung.pdf",
        "uploaded_at": t})
    db.create_edge("documents/10001-DRW", orig_ref, "hat_original", cascade_delete=True)

    # Object Links: Dokumente → Equipment
    db.create_edge("documents/10001-DRW", "equipments/EQ-00001", "object_link",
                   meta={"linked_at": t, "linked_by": DEFAULT_USER})
    db.create_edge("documents/10002-MAN", "equipments/EQ-00001", "object_link",
                   meta={"linked_at": t, "linked_by": DEFAULT_USER})
    db.create_edge("documents/10003-QSP", "equipments/EQ-00002", "object_link",
                   meta={"linked_at": t, "linked_by": DEFAULT_USER})
    db.create_edge("documents/10004-DRW", "equipments/EQ-00004", "object_link",
                   meta={"linked_at": t, "linked_by": DEFAULT_USER})


# =============================================================================
# TEMPLATES
# =============================================================================

NAV = """
<nav style="background:#1a1a2e;color:#fff;padding:0 1.5rem;display:flex;
            align-items:center;gap:1.5rem;height:48px;font-size:.88rem">
  <span style="font-weight:700;font-size:1rem;letter-spacing:.5px">⬡ pDMS</span>
  <a href="/" style="color:#adb5d0">Dashboard</a>
  <a href="/documents" style="color:#adb5d0">Dokumente</a>
  <a href="/equipments" style="color:#adb5d0">Equipment</a>
  <a href="/functional-locations" style="color:#adb5d0">Funktionale Plätze</a>
  <a href="/doc-types" style="color:#adb5d0">Dok-Typen</a>
  <a href="/traversal" style="color:#adb5d0">Traversal</a>
  <a href="/gc" style="color:#adb5d0">GC</a>
</nav>
"""

BASE = """<!doctype html><html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>pDMS</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#f4f6f9;color:#1a1a2e;font-size:.9rem}
a{color:#4361ee;text-decoration:none}a:hover{text-decoration:underline}
nav a:hover{color:#fff;text-decoration:none}
.wrap{max-width:1200px;margin:1.5rem auto;padding:0 1.5rem}
h1{font-size:1.3rem;margin-bottom:1rem}
h2{font-size:1rem;margin-bottom:.6rem;color:#444}
.card{background:#fff;border-radius:8px;padding:1rem 1.25rem;
      box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:.75rem;margin-bottom:1.25rem}
.stat .num{font-size:1.8rem;font-weight:700;color:#4361ee}
.stat .lbl{font-size:.78rem;color:#666;margin-top:.15rem}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
      overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}
th{background:#f0f2f8;text-align:left;padding:.55rem .9rem;font-size:.78rem;
   text-transform:uppercase;letter-spacing:.4px;color:#555}
td{padding:.55rem .9rem;border-top:1px solid #eee;vertical-align:top}
tr:hover td{background:#fafbff}
.badge{display:inline-block;padding:.18rem .5rem;border-radius:20px;
       font-size:.73rem;font-weight:700;background:#e8ecff;color:#4361ee}
.btn{display:inline-block;padding:.38rem .85rem;border-radius:5px;font-size:.83rem;
     font-weight:600;cursor:pointer;border:none;background:#4361ee;color:#fff;line-height:1.4}
.btn:hover{background:#3451d1;text-decoration:none}
.btn.sm{padding:.25rem .6rem;font-size:.76rem}
.btn.danger{background:#e74c3c}.btn.danger:hover{background:#c0392b}
.btn.sec{background:#eee;color:#333}.btn.sec:hover{background:#ddd}
.btn.warn{background:#e67e22;color:#fff}
form.il{display:inline}
label{display:block;font-size:.82rem;font-weight:600;margin-bottom:.25rem;color:#444}
input[type=text],input[type=number],select,textarea{width:100%;padding:.42rem .7rem;
  border:1px solid #ccc;border-radius:5px;font-size:.88rem;margin-bottom:.8rem}
textarea{font-family:monospace;height:100px}
.flash{padding:.6rem .9rem;border-radius:5px;margin-bottom:.9rem;font-size:.85rem;
       background:#e8fff0;color:#0a0;border:1px solid #b2f0c8}
.flash.err{background:#ffe8e8;color:#c00;border-color:#f0b2b2}
.kv{display:grid;grid-template-columns:150px 1fr;gap:.25rem .6rem;font-size:.87rem}
.kv .k{color:#666;font-weight:600}.kv .v{word-break:break-all}
.sec-title{font-size:.72rem;text-transform:uppercase;letter-spacing:.5px;
           color:#999;font-weight:700;margin:1.2rem 0 .5rem}
.row{display:flex;gap:.6rem;align-items:flex-end;flex-wrap:wrap}
</style></head><body>""" + NAV + """
<div class="wrap">
{% for msg,cat in get_flashed_messages(with_categories=true) %}
<div class="flash {% if cat=='err' %}err{% endif %}">{{msg}}</div>
{% endfor %}
{% block content %}{% endblock %}
</div></body></html>"""


def tmpl(block):
    return BASE.replace("{% block content %}{% endblock %}", block)


# =============================================================================
# DASHBOARD
# =============================================================================

@app.route("/")
def dashboard():
    seed_if_empty()
    db = get_db()
    docs = db.list_nodes("documents")
    eqs  = db.list_nodes("equipments")
    fls  = db.list_nodes("functional_locations")
    links = db.list_edges("object_link")

    status_counts = {"WK": 0, "FR": 0, "OB": 0}
    for d in docs.values():
        status_counts[d.get("status", "WK")] = status_counts.get(d.get("status", "WK"), 0) + 1

    recent = sorted(docs.items(), key=lambda x: x[1].get("changed_at",""), reverse=True)[:5]

    T = tmpl("""
<h1>Dashboard</h1>
<div class="grid">
  <div class="card stat"><div class="num">{{docs}}</div><div class="lbl">Dokumente</div></div>
  <div class="card stat"><div class="num">{{eqs}}</div><div class="lbl">Equipment</div></div>
  <div class="card stat"><div class="num">{{fls}}</div><div class="lbl">Funktionale Plätze</div></div>
  <div class="card stat"><div class="num">{{links}}</div><div class="lbl">Object Links</div></div>
  <div class="card stat"><div class="num" style="color:#4361ee">{{wk}}</div><div class="lbl">In Arbeit (WK)</div></div>
  <div class="card stat"><div class="num" style="color:#2a9d60">{{fr}}</div><div class="lbl">Freigegeben (FR)</div></div>
  <div class="card stat"><div class="num" style="color:#888">{{ob}}</div><div class="lbl">Veraltet (OB)</div></div>
</div>
<h2>Zuletzt geändert</h2>
<table><tr><th>Dokument</th><th>Titel</th><th>Typ</th><th>Status</th><th>Geändert</th></tr>
{% for nid,d in recent %}
<tr>
  <td><a href="/document/documents/{{nid}}">{{nid}}</a></td>
  <td>{{d.title}}</td>
  <td><span class="badge">{{d.doc_type}}</span></td>
  <td><span class="badge" style="background:{{colors[d.status]}};color:#fff">{{labels[d.status]}}</span></td>
  <td style="color:#999;font-size:.8rem">{{d.changed_at[:19].replace('T',' ')}}</td>
</tr>
{% endfor %}
</table>
""")
    return render_template_string(T,
        docs=len(docs), eqs=len(eqs), fls=len(fls), links=len(links),
        wk=status_counts["WK"], fr=status_counts["FR"], ob=status_counts["OB"],
        recent=recent, colors=STATUS_COLOR, labels=STATUS_LABEL)


# =============================================================================
# DOKUMENTE
# =============================================================================

@app.route("/documents")
def documents():
    db = get_db()
    q      = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    dtype  = request.args.get("doc_type", "")

    if q:
        docs = db.find_nodes("documents", {"title": q})
    else:
        docs = db.list_nodes("documents")

    if status:
        docs = {k: v for k, v in docs.items() if v.get("status") == status}
    if dtype:
        docs = {k: v for k, v in docs.items() if v.get("doc_type") == dtype}

    types = db.list_nodes("doc_types")
    docs_sorted = sorted(docs.items(), key=lambda x: x[1].get("changed_at",""), reverse=True)

    T = tmpl("""
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">
  <h1 style="margin:0">Dokumente</h1>
  <a class="btn sm" href="/documents/new">+ Neu</a>
</div>
<div class="card" style="padding:.75rem 1rem;margin-bottom:1rem">
  <form method="get" style="display:flex;gap:.6rem;align-items:flex-end;flex-wrap:wrap">
    <div><label>Suche (Wildcard: *DRW*)</label>
      <input type="text" name="q" value="{{q}}" style="width:280px;margin:0"></div>
    <div><label>Status</label>
      <select name="status" style="width:130px;margin:0">
        <option value="">Alle</option>
        {% for k,v in status_labels.items() %}
        <option value="{{k}}" {% if k==status %}selected{% endif %}>{{v}}</option>
        {% endfor %}
      </select></div>
    <div><label>Typ</label>
      <select name="doc_type" style="width:110px;margin:0">
        <option value="">Alle</option>
        {% for tid,t in types.items() %}
        <option value="{{tid}}" {% if tid==dtype %}selected{% endif %}>{{tid}}</option>
        {% endfor %}
      </select></div>
    <button class="btn sm" type="submit">Suchen</button>
    <a class="btn sm sec" href="/documents">Reset</a>
  </form>
</div>
<table>
  <tr><th>Nr</th><th>Titel</th><th>Typ</th><th>Status</th><th>Abteilung</th><th>Geändert</th><th></th></tr>
  {% for nid,d in docs %}
  <tr>
    <td><a href="/document/documents/{{nid}}"><strong>{{nid}}</strong></a></td>
    <td>{{d.title}}</td>
    <td><span class="badge">{{d.doc_type}}</span></td>
    <td><span class="badge" style="background:{{colors[d.status]}};color:#fff">{{labels[d.status]}}</span></td>
    <td>{{d.get('lab_office','')}}</td>
    <td style="color:#999;font-size:.79rem">{{d.changed_at[:10]}}</td>
    <td>
      <form class="il" method="post" action="/document/documents/{{nid}}/delete"
            onsubmit="return confirm('Archivieren?')">
        <button class="btn sm danger">Archivieren</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
""")
    return render_template_string(T, docs=docs_sorted, q=q, status=status, dtype=dtype,
                                  types=types, colors=STATUS_COLOR,
                                  labels=STATUS_LABEL, status_labels=STATUS_LABEL)


@app.route("/documents/new", methods=["GET", "POST"])
def document_new():
    db = get_db()
    types = db.list_nodes("doc_types")

    if request.method == "POST":
        doc_type  = request.form.get("doc_type", "").strip()
        title     = request.form.get("title", "").strip()
        lab_office= request.form.get("lab_office", "").strip()
        language  = request.form.get("language", "DE")

        if not doc_type or not title:
            flash("Dokumenttyp und Titel sind Pflicht.", "err")
        else:
            existing = db.list_nodes("documents")
            max_nr = max((v.get("doc_nr", 0) for v in existing.values()), default=10000)
            doc_nr = max_nr + 1
            node_id = f"{doc_nr}-{doc_type}"
            t = now()
            db.create_node("documents", node_id, {
                "doc_nr": doc_nr, "doc_type": doc_type, "doc_part": "000",
                "title": title, "status": "WK", "language": language,
                "lab_office": lab_office, "created_by": DEFAULT_USER,
                "created_at": t, "changed_at": t})
            flash(f"Dokument {node_id} angelegt.")
            return redirect(url_for("document_detail", ref=f"documents/{node_id}"))

    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/documents">← Dokumente</a></div>
<h1>Neues Dokument</h1>
<div class="card" style="max-width:560px">
  <form method="post">
    <label>Dokumenttyp *</label>
    <select name="doc_type">
      {% for tid,t in types.items() %}<option value="{{tid}}">{{tid}} – {{t.description}}</option>{% endfor %}
    </select>
    <label>Titel *</label>
    <input type="text" name="title" placeholder="Kurzbeschreibung">
    <label>Abteilung</label>
    <input type="text" name="lab_office" placeholder="z.B. Konstruktion">
    <label>Sprache</label>
    <select name="language"><option value="DE">DE</option><option value="EN">EN</option></select>
    <button class="btn" type="submit">Anlegen</button>
    <a class="btn sec" href="/documents">Abbrechen</a>
  </form>
</div>
""")
    return render_template_string(T, types=types)


@app.route("/document/<path:ref>")
def document_detail(ref):
    db = get_db()
    data = db.get_node(ref)
    if not data:
        flash(f"Dokument '{ref}' nicht gefunden.", "err")
        return redirect(url_for("documents"))

    nid = ref.split("/", 1)[1]
    out_edges = db.get_connected_edges(ref, direction="out")
    in_edges  = db.get_connected_edges(ref, direction="in")

    # Object Links ausgehend
    obj_links = [(eid, e) for eid, e in out_edges if e["typ"] == "object_link"]
    # Original
    originals = [(eid, e) for eid, e in out_edges if e["typ"] == "hat_original"]
    orig_nodes = [(eid, e, db.get_node(e["ziel"])) for eid, e in originals if db.get_node(e["ziel"])]

    next_status = STATUS_FLOW.get(data.get("status", "WK"))
    equipments  = db.list_nodes("equipments")
    fls         = db.list_nodes("functional_locations")

    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/documents">← Dokumente</a></div>
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">
  <h1 style="margin:0">{{nid}}</h1>
  <span class="badge" style="background:{{colors[data.status]}};color:#fff;font-size:.85rem">
    {{labels[data.status]}}</span>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
<div>
<div class="card">
  <h2>Dokumentdaten</h2>
  <div class="kv">
    <span class="k">Titel</span><span class="v">{{data.title}}</span>
    <span class="k">Typ</span><span class="v">{{data.doc_type}}</span>
    <span class="k">Teil</span><span class="v">{{data.doc_part}}</span>
    <span class="k">Status</span><span class="v">{{labels[data.status]}}</span>
    <span class="k">Sprache</span><span class="v">{{data.language}}</span>
    <span class="k">Abteilung</span><span class="v">{{data.get('lab_office','')}}</span>
    <span class="k">Erstellt von</span><span class="v">{{data.created_by}}</span>
    <span class="k">Erstellt am</span><span class="v">{{data.created_at[:19].replace('T',' ')}}</span>
    <span class="k">Geändert am</span><span class="v">{{data.changed_at[:19].replace('T',' ')}}</span>
  </div>
  <div style="margin-top:.8rem;display:flex;gap:.5rem;flex-wrap:wrap">
    {% if next_status %}
    <form class="il" method="post" action="/document/{{ref}}/status">
      <input type="hidden" name="new_status" value="{{next_status}}">
      <button class="btn {% if next_status=='OB' %}warn{% endif %}">
        → {{labels[next_status]}} setzen</button>
    </form>
    {% endif %}
    <a class="btn sm sec" href="/edit/{{ref}}">Bearbeiten</a>
    <form class="il" method="post" action="/document/{{ref}}/delete"
          onsubmit="return confirm('Archivieren?')">
      <button class="btn danger sm">Archivieren</button>
    </form>
  </div>
</div>

<div class="card">
  <h2>Originaldatei</h2>
  {% if orig_nodes %}
    {% for eid,e,o in orig_nodes %}
    <div class="kv">
      <span class="k">Dateiname</span><span class="v">{{o.filename}}</span>
      <span class="k">Typ</span><span class="v">{{o.file_type}}</span>
      <span class="k">Pfad</span><span class="v" style="color:#999">{{o.datei}}</span>
    </div>
    {% endfor %}
  {% else %}
  <p style="color:#999;font-size:.85rem">Kein Original angehängt.</p>
  {% endif %}
</div>
</div>

<div>
<div class="card">
  <h2>Object Links</h2>
  {% if obj_links %}
  <table style="margin-bottom:.75rem">
    <tr><th>Ziel</th><th>Verknüpft am</th><th></th></tr>
    {% for eid,e in obj_links %}
    <tr>
      <td><a href="/equipment/{{e.ziel}}" >{{e.ziel}}</a></td>
      <td style="font-size:.79rem;color:#999">{{e.get('linked_at','')[:10]}}</td>
      <td><form class="il" method="post" action="/edge/{{eid}}/delete">
        <button class="btn sm danger">✕</button></form></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#999;font-size:.85rem;margin-bottom:.75rem">Keine Links.</p>{% endif %}
  <form method="post" action="/document/{{ref}}/link">
    <div class="row">
      <select name="target_ref" style="flex:1;margin:0">
        <optgroup label="Equipment">
        {% for eid,eq in equipments.items() %}
          <option value="equipments/{{eid}}">{{eid}} – {{eq.description[:40]}}</option>
        {% endfor %}
        </optgroup>
        <optgroup label="Funktionale Plätze">
        {% for fid,fl in fls.items() %}
          <option value="functional_locations/{{fid}}">{{fid}} – {{fl.description[:35]}}</option>
        {% endfor %}
        </optgroup>
      </select>
      <button class="btn sm" type="submit">Link anlegen</button>
    </div>
  </form>
</div>
</div>
</div>
""")
    return render_template_string(T, ref=ref, nid=nid, data=data,
                                  obj_links=obj_links, orig_nodes=orig_nodes,
                                  next_status=next_status, equipments=equipments,
                                  fls=fls, colors=STATUS_COLOR, labels=STATUS_LABEL)


@app.route("/document/<path:ref>/status", methods=["POST"])
def document_status(ref):
    parts = ref.split("/", 1)
    if len(parts) != 2:
        return redirect(url_for("documents"))
    col, nid = parts
    new_status = request.form.get("new_status", "")
    db = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Dokument nicht gefunden.", "err")
        return redirect(url_for("documents"))
    cur = data.get("status", "WK")
    if STATUS_FLOW.get(cur) != new_status:
        flash(f"Status-Übergang {cur}→{new_status} nicht erlaubt.", "err")
    else:
        db.update_node(col, nid, {"status": new_status, "changed_at": now()})
        flash(f"Status auf {STATUS_LABEL[new_status]} gesetzt.")
    return redirect(url_for("document_detail", ref=ref))


@app.route("/document/<path:ref>/link", methods=["POST"])
def document_link(ref):
    target = request.form.get("target_ref", "").strip()
    db = get_db()
    if not target or not db.get_node(target):
        flash("Ziel nicht gefunden.", "err")
        return redirect(url_for("document_detail", ref=ref))
    t = now()
    db.create_edge(ref, target, "object_link",
                   meta={"linked_at": t, "linked_by": DEFAULT_USER})
    flash(f"Object Link zu {target} angelegt.")
    return redirect(url_for("document_detail", ref=ref))


@app.route("/document/<path:ref>/delete", methods=["POST"])
def document_delete(ref):
    parts = ref.split("/", 1)
    if len(parts) != 2:
        return redirect(url_for("documents"))
    col, nid = parts
    get_db().soft_delete(col, nid)
    flash(f"{ref} archiviert (wartet auf GC).")
    return redirect(url_for("documents"))


@app.route("/edge/<edge_id>/delete", methods=["POST"])
def edge_delete(edge_id):
    ref = request.referrer or url_for("documents")
    get_db().delete_edge(edge_id)
    flash("Edge gelöscht.")
    return redirect(ref)


# =============================================================================
# GENERIC EDIT
# =============================================================================

_DETAIL_ROUTES = {
    "documents":            ("document_detail",            "documents"),
    "equipments":           ("equipment_detail",           "equipments"),
    "functional_locations": ("functional_location_detail", "functional_locations"),
}


@app.route("/edit/<col>/<path:nid>", methods=["GET", "POST"])
def node_edit(col, nid):
    db   = get_db()
    ref  = f"{col}/{nid}"
    data = db.get_node(ref)
    if data is None:
        flash(f"Node '{ref}' nicht gefunden.", "err")
        return redirect(url_for("dashboard"))
    route_info = _DETAIL_ROUTES.get(col)
    if route_info is None:
        flash(f"Bearbeitung für '{col}' nicht unterstützt.", "err")
        return redirect(url_for("dashboard"))
    detail_view, _ = route_info
    locked = get_locked(col)

    if request.method == "POST":
        updates = {}
        for field, old_value in data.items():
            if field in locked or field.endswith("_at") or field.startswith("_"):
                continue
            raw = request.form.get(field)
            if raw is None:
                continue
            if isinstance(old_value, bool):
                new_value = raw.lower() == "true"
            elif isinstance(old_value, int) and not isinstance(old_value, bool):
                try:    new_value = int(raw) if raw.strip() else None
                except: new_value = raw
            elif isinstance(old_value, float):
                try:    new_value = float(raw) if raw.strip() else None
                except: new_value = raw
            else:
                new_value = raw if raw != "" else (None if old_value is None else raw)
            if new_value != old_value:
                updates[field] = new_value
        if updates:
            if col == "documents":
                updates["changed_at"] = now()
            db.update_node(col, nid, updates)
            flash(f"{nid} gespeichert.")
        else:
            flash("Keine Änderungen.")
        return redirect(url_for(detail_view, ref=ref))

    back_titles = {"documents": "← Dokumente", "equipments": "← Equipment",
                   "functional_locations": "← Funktionale Plätze"}
    back_url  = url_for(detail_view, ref=ref)
    back_text = back_titles.get(col, "← Zurück")
    fields = []
    for field, value in data.items():
        html_input, auto_locked = field_input(field, value)
        fields.append({
            "name":      field,
            "label":     field.replace("_", " ").capitalize(),
            "html":      html_input,
            "is_locked": (field in locked) or auto_locked,
            "value":     value,
        })
    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="{{back_url}}">{{back_text}}</a></div>
<h1>Bearbeiten: {{nid}}</h1>
<div class="card" style="max-width:640px">
  <form method="post">
    {% for f in fields %}
    <label>{{f.label}}{% if f.is_locked %}
      <span style="font-weight:400;color:#999;font-size:.78rem"> (gesperrt)</span>
    {% endif %}</label>
    {% if f.is_locked %}
      <div style="padding:.42rem .7rem;border:1px solid #e0e0e0;border-radius:5px;
                  background:#f8f8f8;margin-bottom:.8rem;color:#888;font-size:.88rem">
        {{f.value if f.value is not none else '–'}}</div>
    {% else %}{{f.html|safe}}
    {% endif %}
    {% endfor %}
    <div style="display:flex;gap:.6rem;margin-top:.5rem">
      <button class="btn" type="submit">Speichern</button>
      <a class="btn sec" href="{{back_url}}">Abbrechen</a>
    </div>
  </form>
</div>
""")
    return render_template_string(T, nid=nid, fields=fields,
                                  back_url=back_url, back_text=back_text)


# =============================================================================
# EQUIPMENT
# =============================================================================

@app.route("/equipments")
def equipments():
    db  = get_db()
    eqs = db.list_nodes("equipments")
    T = tmpl("""
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">
  <h1 style="margin:0">Equipment</h1>
  <a class="btn sm" href="/equipments/new">+ Neu</a>
</div>
<table>
  <tr><th>Nr</th><th>Beschreibung</th><th>Kat.</th><th>Hersteller</th><th>Status</th><th>Kostenstelle</th><th></th></tr>
  {% for nid,e in eqs %}
  <tr>
    <td><a href="/equipment/equipments/{{nid}}"><strong>{{nid}}</strong></a></td>
    <td>{{e.description}}</td>
    <td><span class="badge">{{e.category}}</span></td>
    <td>{{e.get('manufacturer','')}}</td>
    <td><span class="badge" style="background:{{st_col[e.status]}};color:#fff">{{e.status}}</span></td>
    <td>{{e.get('cost_center','')}}</td>
    <td><form class="il" method="post" action="/equipment/equipments/{{nid}}/delete"
          onsubmit="return confirm('Stilllegen?')">
      <button class="btn sm danger">Stilllegen</button></form></td>
  </tr>
  {% endfor %}
</table>
""")
    return render_template_string(T, eqs=sorted(eqs.items()), st_col=EQ_STATUS)


@app.route("/equipments/new", methods=["GET", "POST"])
def equipment_new():
    db = get_db()
    if request.method == "POST":
        desc   = request.form.get("description","").strip()
        cat    = request.form.get("category","M")
        mfr    = request.form.get("manufacturer","").strip()
        model  = request.form.get("model","").strip()
        serial = request.form.get("serial_nr","").strip()
        year   = request.form.get("construction_year","")
        cc     = request.form.get("cost_center","").strip()
        if not desc:
            flash("Beschreibung ist Pflicht.", "err")
        else:
            nid = db.next_id("equipments", prefix="EQ-", padding=5)
            db.create_node("equipments", nid, {
                "description": desc, "category": cat, "manufacturer": mfr,
                "model": model, "serial_nr": serial,
                "construction_year": int(year) if year else None,
                "status": "AKTIV", "cost_center": cc})
            flash(f"Equipment {nid} angelegt.")
            return redirect(url_for("equipment_detail", ref=f"equipments/{nid}"))
    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/equipments">← Equipment</a></div>
<h1>Neues Equipment</h1>
<div class="card" style="max-width:560px">
  <form method="post">
    <label>Beschreibung *</label>
    <input type="text" name="description" placeholder="z.B. CNC Fräsmaschine">
    <label>Kategorie</label>
    <select name="category">
      {% for k,v in cats.items() %}<option value="{{k}}">{{k}} – {{v}}</option>{% endfor %}
    </select>
    <label>Hersteller</label><input type="text" name="manufacturer">
    <label>Modell</label><input type="text" name="model">
    <label>Seriennummer</label><input type="text" name="serial_nr">
    <label>Baujahr</label><input type="number" name="construction_year" placeholder="2024">
    <label>Kostenstelle</label><input type="text" name="cost_center">
    <button class="btn" type="submit">Anlegen</button>
    <a class="btn sec" href="/equipments">Abbrechen</a>
  </form>
</div>
""")
    return render_template_string(T, cats=CATEGORIES)


@app.route("/equipment/<path:ref>")
def equipment_detail(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Equipment nicht gefunden.", "err")
        return redirect(url_for("equipments"))
    nid = ref.split("/", 1)[1]

    children_refs = db.get_connected(ref, direction="out", rel_type="ist_uebergeordnet")
    children = [(r, db.get_node(r)) for r in children_refs if db.get_node(r)]

    parent_refs = db.get_connected(ref, direction="in", rel_type="ist_uebergeordnet")
    parent = (parent_refs[0], db.get_node(parent_refs[0])) if parent_refs else None

    fl_refs = db.get_connected_edges(ref, direction="out", rel_type="installiert_in")
    fl_data = [(eid, e, db.get_node(e["ziel"])) for eid,e in fl_refs if db.get_node(e["ziel"])]

    doc_edges = db.get_connected_edges(ref, direction="in", rel_type="object_link")
    docs = [(eid, e, db.get_node(e["quelle"])) for eid,e in doc_edges if db.get_node(e["quelle"])]

    all_docs_in_tree = db.traverse(ref, rel_type="ist_uebergeordnet",
                                   direction="out", include_start=True)
    tree_doc_refs = set()
    for eq_ref in all_docs_in_tree:
        for _, e in db.get_connected_edges(eq_ref, direction="in", rel_type="object_link"):
            if e["quelle"].startswith("documents/"):
                tree_doc_refs.add(e["quelle"])
    tree_docs = [(r, db.get_node(r)) for r in sorted(tree_doc_refs) if db.get_node(r)]

    fls_all = db.list_nodes("functional_locations")
    eqs_all = db.list_nodes("equipments")

    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/equipments">← Equipment</a></div>
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">
  <h1 style="margin:0">{{nid}}</h1>
  <span class="badge" style="background:{{st_col[data.status]}};color:#fff">{{data.status}}</span>
  <span class="badge">{{cats[data.category]}}</span>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
<div>
<div class="card">
  <h2>Stammdaten</h2>
  <div class="kv">
    <span class="k">Beschreibung</span><span class="v">{{data.description}}</span>
    <span class="k">Hersteller</span><span class="v">{{data.get('manufacturer','')}}</span>
    <span class="k">Modell</span><span class="v">{{data.get('model','')}}</span>
    <span class="k">Seriennr.</span><span class="v">{{data.get('serial_nr','')}}</span>
    <span class="k">Baujahr</span><span class="v">{{data.get('construction_year','')}}</span>
    <span class="k">Kostenstelle</span><span class="v">{{data.get('cost_center','')}}</span>
  </div>
  <div style="margin-top:.75rem;display:flex;gap:.5rem;flex-wrap:wrap">
    <a class="btn sm sec" href="/edit/{{ref}}">Bearbeiten</a>
    <form class="il" method="post" action="/equipment/{{ref}}/delete"
          onsubmit="return confirm('Stilllegen?')">
      <button class="btn sm danger">Stilllegen</button>
    </form>
  </div>
</div>

<div class="card">
  <h2>Installationsort</h2>
  {% if fl_data %}
    {% for eid,e,fl in fl_data %}
    <div class="kv" style="margin-bottom:.5rem">
      <span class="k">Platz</span>
      <span class="v"><a href="/functional-location/{{e.ziel}}">{{e.ziel}}</a></span>
      <span class="k">Beschreibung</span><span class="v">{{fl.description}}</span>
      <span class="k">Seit</span><span class="v">{{e.get('since','')}}</span>
    </div>
    <form class="il" method="post" action="/edge/{{eid}}/delete">
      <button class="btn sm sec">Link entfernen</button></form>
    {% endfor %}
  {% else %}<p style="color:#999;font-size:.85rem;margin-bottom:.75rem">Nicht installiert.</p>{% endif %}
  <form method="post" action="/equipment/{{ref}}/link-fl" style="margin-top:.5rem">
    <div class="row">
      <select name="fl_ref" style="flex:1;margin:0">
        {% for fid,fl in fls_all.items() %}
        <option value="functional_locations/{{fid}}">{{fid}} – {{fl.description[:40]}}</option>
        {% endfor %}
      </select>
      <input type="text" name="since" placeholder="Seit (YYYY-MM-DD)"
             style="width:140px;margin:0">
      <button class="btn sm" type="submit">Zuordnen</button>
    </div>
  </form>
</div>
</div>

<div>
<div class="card">
  <h2>Hierarchie</h2>
  {% if parent %}
  <p style="margin-bottom:.5rem;font-size:.85rem">
    ↑ Übergeordnet: <a href="/equipment/{{parent[0]}}">{{parent[0]}}</a>
    – {{parent[1].description}}</p>
  {% endif %}
  {% if children %}
  <div class="sec-title">Unterbaugruppen</div>
  <table>
    <tr><th>Nr</th><th>Beschreibung</th><th>Status</th></tr>
    {% for cr,cd in children %}
    <tr>
      <td><a href="/equipment/{{cr}}">{{cr.split('/')[-1]}}</a></td>
      <td>{{cd.description}}</td>
      <td><span class="badge" style="background:{{st_col[cd.status]}};color:#fff">{{cd.status}}</span></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#999;font-size:.85rem">Keine Unterbaugruppen.</p>{% endif %}
  <div style="margin-top:.75rem">
    <form method="post" action="/equipment/{{ref}}/add-child">
      <div class="row">
        <select name="child_ref" style="flex:1;margin:0">
          {% for eid,eq in eqs_all.items() %}
          <option value="equipments/{{eid}}">{{eid}} – {{eq.description[:40]}}</option>
          {% endfor %}
        </select>
        <button class="btn sm" type="submit">Als Unterbaugruppe</button>
      </div>
    </form>
  </div>
</div>

<div class="card">
  <h2>Verknüpfte Dokumente (direkt)</h2>
  {% if docs %}
  <table style="margin-bottom:.5rem">
    <tr><th>Dokument</th><th>Titel</th><th>Status</th></tr>
    {% for eid,e,d in docs %}
    <tr>
      <td><a href="/document/{{e.quelle}}">{{e.quelle.split('/')[-1]}}</a></td>
      <td>{{d.title[:45]}}</td>
      <td><span class="badge" style="background:{{colors[d.status]}};color:#fff">{{d.status}}</span></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#999;font-size:.85rem">Keine direkt verknüpften Dokumente.</p>{% endif %}

  {% if tree_docs %}
  <div class="sec-title">Alle Docs im Equipment-Baum (inkl. Unterbaugruppen)</div>
  <table>
    <tr><th>Dokument</th><th>Titel</th></tr>
    {% for r,d in tree_docs %}
    <tr>
      <td><a href="/document/{{r}}">{{r.split('/')[-1]}}</a></td>
      <td>{{d.title[:50]}}</td>
    </tr>
    {% endfor %}
  </table>
  {% endif %}
</div>
</div>
</div>
""")
    return render_template_string(T, ref=ref, nid=nid, data=data,
        children=children, parent=parent, fl_data=fl_data, docs=docs,
        tree_docs=tree_docs, fls_all=fls_all, eqs_all=eqs_all,
        colors=STATUS_COLOR, st_col=EQ_STATUS, cats=CATEGORIES)


@app.route("/equipment/<path:ref>/link-fl", methods=["POST"])
def equipment_link_fl(ref):
    fl_ref = request.form.get("fl_ref","").strip()
    since  = request.form.get("since","").strip()
    db = get_db()
    if not fl_ref or not db.get_node(fl_ref):
        flash("Funktionaler Platz nicht gefunden.", "err")
    else:
        db.create_edge(ref, fl_ref, "installiert_in", meta={"since": since})
        flash(f"Installationsort {fl_ref} zugeordnet.")
    return redirect(url_for("equipment_detail", ref=ref))


@app.route("/equipment/<path:ref>/add-child", methods=["POST"])
def equipment_add_child(ref):
    child = request.form.get("child_ref","").strip()
    db = get_db()
    if not child or not db.get_node(child):
        flash("Equipment nicht gefunden.", "err")
    else:
        db.create_edge(ref, child, "ist_uebergeordnet", cascade_delete=True)
        flash(f"{child} als Unterbaugruppe hinzugefügt.")
    return redirect(url_for("equipment_detail", ref=ref))


@app.route("/equipment/<path:ref>/delete", methods=["POST"])
def equipment_delete(ref):
    parts = ref.split("/", 1)
    if len(parts) == 2:
        get_db().soft_delete(parts[0], parts[1])
        flash(f"{ref} stillgelegt (wartet auf GC).")
    return redirect(url_for("equipments"))


# =============================================================================
# FUNCTIONAL LOCATIONS
# =============================================================================

@app.route("/functional-locations")
def functional_locations():
    db  = get_db()
    fls = db.list_nodes("functional_locations")
    T = tmpl("""
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">
  <h1 style="margin:0">Funktionale Plätze</h1>
  <a class="btn sm" href="/functional-locations/new">+ Neu</a>
</div>
<table>
  <tr><th>TPLNR</th><th>Beschreibung</th><th>Kategorie</th><th>Equipment</th></tr>
  {% for fid,fl in fls %}
  <tr>
    <td><a href="/functional-location/functional_locations/{{fid}}"><strong>{{fid}}</strong></a></td>
    <td>{{fl.description}}</td>
    <td>{{fl.get('category','')}}</td>
    <td>{{eq_counts.get(fid,0)}}</td>
  </tr>
  {% endfor %}
</table>
""")
    eq_counts = {}
    for fid in fls:
        eq_counts[fid] = len(db.get_connected(
            f"functional_locations/{fid}", direction="in", rel_type="installiert_in"))
    return render_template_string(T, fls=sorted(fls.items()), eq_counts=eq_counts)


@app.route("/functional-locations/new", methods=["GET","POST"])
def functional_location_new():
    if request.method == "POST":
        tplnr = request.form.get("tplnr","").strip()
        desc  = request.form.get("description","").strip()
        cat   = request.form.get("category","").strip()
        if not tplnr or not desc:
            flash("TPLNR und Beschreibung sind Pflicht.", "err")
        else:
            db = get_db()
            db.create_node("functional_locations", tplnr,
                           {"description": desc, "category": cat})
            flash(f"Funktionaler Platz {tplnr} angelegt.")
            return redirect(url_for("functional_locations"))
    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/functional-locations">← Funktionale Plätze</a></div>
<h1>Neuer Funktionaler Platz</h1>
<div class="card" style="max-width:480px">
  <form method="post">
    <label>TPLNR (Schlüssel) *</label>
    <input type="text" name="tplnr" placeholder="z.B. WERK-01-HALLE-C">
    <label>Beschreibung *</label>
    <input type="text" name="description" placeholder="Halle C – Prüfbereich">
    <label>Kategorie</label>
    <input type="text" name="category" placeholder="Werk / Halle / Bereich">
    <button class="btn" type="submit">Anlegen</button>
    <a class="btn sec" href="/functional-locations">Abbrechen</a>
  </form>
</div>
""")
    return render_template_string(T)


@app.route("/functional-location/<path:ref>")
def functional_location_detail(ref):
    db   = get_db()
    data = db.get_node(ref)
    if not data:
        flash("Funktionaler Platz nicht gefunden.", "err")
        return redirect(url_for("functional_locations"))

    eq_refs = db.get_connected(ref, direction="in", rel_type="installiert_in")
    eqs = [(r, db.get_node(r)) for r in eq_refs if db.get_node(r)]

    all_doc_refs = db.collect_related(
        ref, ["installiert_in", "object_link"], direction="in")
    docs = [(r, db.get_node(r)) for r in all_doc_refs if db.get_node(r)]

    sub_refs = db.get_connected(ref, direction="out", rel_type="hat_unterbereich")
    subs = [(r, db.get_node(r)) for r in sub_refs if db.get_node(r)]

    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/functional-locations">← Funktionale Plätze</a></div>
<h1>{{ref.split('/')[-1]}}</h1>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
<div>
<div class="card">
  <h2>Stammdaten</h2>
  <div class="kv">
    <span class="k">Beschreibung</span><span class="v">{{data.description}}</span>
    <span class="k">Kategorie</span><span class="v">{{data.get('category','')}}</span>
  </div>
  <div style="margin-top:.75rem">
    <a class="btn sm sec" href="/edit/{{ref}}">Bearbeiten</a>
  </div>
</div>
{% if subs %}
<div class="card">
  <h2>Unterbereiche</h2>
  {% for sr,sd in subs %}
  <div><a href="/functional-location/{{sr}}">{{sr.split('/')[-1]}}</a>
    – {{sd.description}}</div>
  {% endfor %}
</div>
{% endif %}
</div>
<div>
<div class="card">
  <h2>Installierte Equipment ({{eqs|length}})</h2>
  {% if eqs %}
  <table>
    <tr><th>Nr</th><th>Beschreibung</th><th>Status</th></tr>
    {% for er,ed in eqs %}
    <tr>
      <td><a href="/equipment/{{er}}">{{er.split('/')[-1]}}</a></td>
      <td>{{ed.description[:45]}}</td>
      <td><span class="badge" style="background:{{st_col[ed.status]}};color:#fff">{{ed.status}}</span></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#999;font-size:.85rem">Kein Equipment installiert.</p>{% endif %}
</div>
<div class="card">
  <h2>Verknüpfte Dokumente über Equipment ({{docs|length}})</h2>
  {% if docs %}
  <table>
    <tr><th>Dokument</th><th>Titel</th><th>Status</th></tr>
    {% for dr,dd in docs %}
    <tr>
      <td><a href="/document/{{dr}}">{{dr.split('/')[-1]}}</a></td>
      <td>{{dd.title[:45]}}</td>
      <td><span class="badge" style="background:{{colors[dd.status]}};color:#fff">{{dd.status}}</span></td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#999;font-size:.85rem">Keine Dokumente gefunden.</p>{% endif %}
</div>
</div>
</div>
""")
    return render_template_string(T, ref=ref, data=data, eqs=eqs, docs=docs,
                                  subs=subs, colors=STATUS_COLOR, st_col=EQ_STATUS)


# =============================================================================
# DOK-TYPEN
# =============================================================================

@app.route("/doc-types")
def doc_types():
    db    = get_db()
    types = db.list_nodes("doc_types")
    T = tmpl("""
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem">
  <h1 style="margin:0">Dokumenttypen</h1>
  <a class="btn sm" href="/doc-types/new">+ Neu</a>
</div>
<table>
  <tr><th>Code</th><th>Beschreibung</th></tr>
  {% for tid,t in types %}
  <tr>
    <td><span class="badge">{{tid}}</span></td>
    <td>{{t.description}}</td>
  </tr>
  {% endfor %}
</table>
""")
    return render_template_string(T, types=sorted(types.items()))


@app.route("/doc-types/new", methods=["GET","POST"])
def doc_type_new():
    if request.method == "POST":
        code = request.form.get("code","").strip().upper()
        desc = request.form.get("description","").strip()
        if not code or not desc:
            flash("Code und Beschreibung sind Pflicht.", "err")
        else:
            get_db().create_node("doc_types", code, {"code": code, "description": desc})
            flash(f"Dokumenttyp {code} angelegt.")
            return redirect(url_for("doc_types"))
    T = tmpl("""
<div style="margin-bottom:.5rem"><a href="/doc-types">← Dokumenttypen</a></div>
<h1>Neuer Dokumenttyp</h1>
<div class="card" style="max-width:400px">
  <form method="post">
    <label>Code (z.B. DRW) *</label>
    <input type="text" name="code" placeholder="DRW" maxlength="10">
    <label>Beschreibung *</label>
    <input type="text" name="description" placeholder="Zeichnung">
    <button class="btn" type="submit">Anlegen</button>
    <a class="btn sec" href="/doc-types">Abbrechen</a>
  </form>
</div>
""")
    return render_template_string(T)


# =============================================================================
# TRAVERSAL
# =============================================================================

@app.route("/traversal")
def traversal():
    db      = get_db()
    start   = request.args.get("start","").strip()
    rel     = request.args.get("rel_type","").strip() or None
    direc   = request.args.get("direction","out")
    results = []
    node_info = {}
    error = None

    if start:
        if not db.get_node(start):
            error = f"Node '{start}' nicht gefunden."
        else:
            results = db.traverse(start, rel_type=rel, direction=direc, include_start=True)
            for r in results:
                nd = db.get_node(r)
                if nd:
                    node_info[r] = nd.get("title") or nd.get("description") or nd.get("name","")

    edge_types = list(db._cache["edges"].keys())
    all_refs   = ([f"documents/{k}" for k in db.list_nodes("documents")] +
                  [f"equipments/{k}" for k in db.list_nodes("equipments")] +
                  [f"functional_locations/{k}" for k in db.list_nodes("functional_locations")])

    T = tmpl("""
<h1>Graph-Traversal (BFS)</h1>
<div class="card">
  <form method="get">
    <div class="row">
      <div style="flex:2"><label>Start-Node</label>
        <select name="start" style="margin:0">
          <option value="">— wählen —</option>
          {% for r in all_refs %}
          <option value="{{r}}" {% if r==start %}selected{% endif %}>{{r}}</option>
          {% endfor %}
        </select></div>
      <div style="flex:1"><label>Beziehungstyp</label>
        <select name="rel_type" style="margin:0">
          <option value="">Alle</option>
          {% for et in edge_types %}
          <option value="{{et}}" {% if et==rel %}selected{% endif %}>{{et}}</option>
          {% endfor %}
        </select></div>
      <div><label>Richtung</label>
        <select name="direction" style="margin:0">
          <option value="out" {% if direction=='out' %}selected{% endif %}>→ out</option>
          <option value="in"  {% if direction=='in'  %}selected{% endif %}>← in</option>
        </select></div>
      <button class="btn" type="submit" style="margin-top:1.3rem">Traversieren</button>
    </div>
  </form>
</div>
{% if error %}<div class="flash err">{{error}}</div>{% endif %}
{% if start and not error %}
<div class="card">
  <h2>Ergebnis: {{results|length}} Node(s)</h2>
  {% if results %}
  <table style="margin-top:.5rem">
    <tr><th>#</th><th>Ref</th><th>Name / Titel</th></tr>
    {% for r in results %}
    <tr>
      <td style="color:#999">{{loop.index}}</td>
      <td><a href="/{% if 'equipments' in r %}equipment{% elif 'documents' in r %}document
           {%- elif 'functional' in r %}functional-location{% else %}node{% endif %}/{{r}}">{{r}}</a></td>
      <td>{{node_info.get(r,'')}}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p style="color:#999">Keine verbundenen Nodes gefunden.</p>{% endif %}
</div>
{% endif %}
""")
    return render_template_string(T, start=start, rel=rel, direction=direc,
        results=results, node_info=node_info, error=error,
        edge_types=edge_types, all_refs=sorted(all_refs))


# =============================================================================
# GARBAGE COLLECTOR
# =============================================================================

@app.route("/gc")
def gc_view():
    db = get_db()
    pending = []
    for col in db.list_collections():
        for nid, data in db.list_nodes(col, include_deleted=True).items():
            if "_deletion_flag" in data:
                pending.append((f"{col}/{nid}", data.get("_deletion_flag","")[:19], col))
    T = tmpl("""
<h1>Garbage Collector</h1>
<div class="card" style="max-width:600px">
  <p style="margin-bottom:.75rem;color:#555;font-size:.88rem">
    Bereinigt alle soft-deleted Nodes physisch. Cascade-Delete-Ketten
    werden transitiv aufgelöst. Idempotent.</p>
  <form method="post" action="/gc/run">
    <button class="btn" type="submit">GC jetzt ausführen</button>
  </form>
</div>
{% if stats %}
<div class="card" style="max-width:600px;margin-top:.5rem">
  <h2>Letzter Lauf</h2>
  <div class="kv" style="margin-top:.5rem">
    <span class="k">Gescannt</span><span class="v">{{stats.scanned}}</span>
    <span class="k">Edges entfernt</span><span class="v">{{stats.edges_removed}}</span>
    <span class="k">Nodes gelöscht</span><span class="v">{{stats.nodes_purged}}</span>
    <span class="k">Assets archiviert</span><span class="v">{{stats.assets_archived}}</span>
  </div>
</div>
{% endif %}
<div class="card">
  <h2>Warten auf Bereinigung ({{pending|length}})</h2>
  {% if pending %}
  <table><tr><th>Ref</th><th>Collection</th><th>Markiert am</th></tr>
  {% for ref,flag,col in pending %}
  <tr>
    <td>{{ref}}</td><td>{{col}}</td>
    <td style="color:#999;font-size:.8rem">{{flag.replace('T',' ')}}</td>
  </tr>
  {% endfor %}
  </table>
  {% else %}<p style="color:#999">Keine Nodes warten auf Bereinigung.</p>{% endif %}
</div>
""")
    return render_template_string(T, pending=pending, stats=None)


@app.route("/gc/run", methods=["POST"])
def gc_run():
    gc   = MaintenanceEngine(root_dir=DB_ROOT)
    stats = gc.run_garbage_collection()
    pending = []
    for col in gc.list_collections():
        for nid, data in gc.list_nodes(col, include_deleted=True).items():
            if "_deletion_flag" in data:
                pending.append((f"{col}/{nid}", data.get("_deletion_flag","")[:19], col))
    flash(f"GC: {stats['nodes_purged']} Node(s) bereinigt, {stats['edges_removed']} Edge(s) entfernt.")
    T = tmpl("""
<h1>Garbage Collector</h1>
<div class="card" style="max-width:600px">
  <p style="margin-bottom:.75rem;color:#555;font-size:.88rem">Idempotent, jederzeit wieder ausführbar.</p>
  <form method="post" action="/gc/run">
    <button class="btn" type="submit">Erneut ausführen</button>
  </form>
</div>
<div class="card" style="max-width:600px;margin-top:.5rem">
  <h2>Letzter Lauf</h2>
  <div class="kv" style="margin-top:.5rem">
    <span class="k">Gescannt</span><span class="v">{{stats.scanned}}</span>
    <span class="k">Edges entfernt</span><span class="v">{{stats.edges_removed}}</span>
    <span class="k">Nodes gelöscht</span><span class="v">{{stats.nodes_purged}}</span>
    <span class="k">Assets archiviert</span><span class="v">{{stats.assets_archived}}</span>
  </div>
</div>
<div class="card">
  <h2>Verbleibend ({{pending|length}})</h2>
  {% if pending %}<table><tr><th>Ref</th><th>Collection</th><th>Flag</th></tr>
  {% for ref,flag,col in pending %}<tr><td>{{ref}}</td><td>{{col}}</td>
  <td style="color:#999">{{flag}}</td></tr>{% endfor %}</table>
  {% else %}<p style="color:#999">Keine pending Nodes.</p>{% endif %}
</div>
""")
    return render_template_string(T, pending=pending, stats=stats)


if __name__ == "__main__":
    seed_if_empty()
    print("pDMS läuft auf http://127.0.0.1:5001")
    app.run(debug=True, port=5001)
