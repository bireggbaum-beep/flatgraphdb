"""
FlatGraphDB Web Demo
Run: pip install flask && python web_app.py
"""

import os
import json
from flask import Flask, render_template_string, request, redirect, url_for, flash
from flatgraph import FlatGraphDB, MaintenanceEngine

app = Flask(__name__)
app.secret_key = "flatgraph-demo-secret"

DB_ROOT = os.path.join(os.path.dirname(__file__), "_demo_db")


def get_db():
    return FlatGraphDB(root_dir=DB_ROOT)


def seed_if_empty():
    db = get_db()
    if db.list_collections():
        return
    # Produkt
    db.create_node("products", "PROD-001", {"name": "Fahrrad", "farbe": "Rot"})
    # Equipments
    db.create_node("equipments", "EQ-001", {"name": "Schweißgerät", "hersteller": "Fronius"})
    db.create_node("equipments", "EQ-002", {"name": "Montagetisch", "hersteller": "Treston"})
    db.create_node("equipments", "EQ-003", {"name": "Förderbandanlage", "hersteller": "Interroll"})
    # Räume
    db.create_node("rooms", "ROOM-A", {"name": "Halle A – Schweißen", "flaeche_m2": 200})
    db.create_node("rooms", "ROOM-B", {"name": "Halle B – Montage", "flaeche_m2": 350})
    # Materialien
    db.create_node("materials", "MAT-001", {"name": "Stahlrohr", "einheit": "m"})
    db.create_node("materials", "MAT-002", {"name": "Schraubenset", "einheit": "Stk"})
    # Prozesse
    db.create_node("processes", "PROC-001",   {"name": "Rahmen fertigen"})
    db.create_node("processes", "PROC-001-A", {"name": "Rohre schneiden"})
    db.create_node("processes", "PROC-001-B", {"name": "Rahmen schweißen"})
    db.create_node("processes", "PROC-002",   {"name": "Fahrrad montieren"})
    db.create_node("processes", "PROC-002-A", {"name": "Komponenten verschrauben"})
    db.create_node("processes", "PROC-002-B", {"name": "Qualitätskontrolle"})
    # Dokument
    db.create_node("documents", "DOC-001", {"title": "Schweißanleitung v1", "status": "AKTIV"})
    # Edges
    db.create_edge("processes/PROC-002",   "products/PROD-001",    "erzeugt")
    db.create_edge("processes/PROC-001",   "processes/PROC-001-A", "hat_subprozess", meta={"reihenfolge": 10})
    db.create_edge("processes/PROC-001",   "processes/PROC-001-B", "hat_subprozess", meta={"reihenfolge": 20})
    db.create_edge("processes/PROC-002",   "processes/PROC-002-A", "hat_subprozess", meta={"reihenfolge": 10})
    db.create_edge("processes/PROC-002",   "processes/PROC-002-B", "hat_subprozess", meta={"reihenfolge": 20})
    db.create_edge("processes/PROC-001-B", "equipments/EQ-001",    "benoetigt_equipment")
    db.create_edge("processes/PROC-002-A", "equipments/EQ-002",    "benoetigt_equipment")
    db.create_edge("processes/PROC-002-B", "equipments/EQ-003",    "benoetigt_equipment")
    db.create_edge("equipments/EQ-001",    "rooms/ROOM-A",         "steht_in")
    db.create_edge("equipments/EQ-002",    "rooms/ROOM-B",         "steht_in")
    db.create_edge("equipments/EQ-003",    "rooms/ROOM-B",         "steht_in")
    db.create_edge("processes/PROC-001-A", "materials/MAT-001",    "verbraucht_material", meta={"menge": 3.5})
    db.create_edge("processes/PROC-002-A", "materials/MAT-002",    "verbraucht_material", meta={"menge": 24})
    db.create_edge("processes/PROC-001-B", "documents/DOC-001",    "hat_dokument", cascade_delete=True)


BASE = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FlatGraphDB Demo</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, sans-serif; background: #f4f6f9; color: #1a1a2e; }
    a { color: #4361ee; text-decoration: none; }
    a:hover { text-decoration: underline; }

    nav {
      background: #1a1a2e; color: #fff; padding: 0 2rem;
      display: flex; align-items: center; gap: 2rem; height: 52px;
    }
    nav .logo { font-weight: 700; font-size: 1.1rem; color: #fff; letter-spacing: 0.5px; }
    nav a { color: #adb5d0; font-size: .9rem; }
    nav a:hover { color: #fff; text-decoration: none; }

    .container { max-width: 1100px; margin: 2rem auto; padding: 0 1.5rem; }
    h1 { font-size: 1.5rem; margin-bottom: 1.25rem; }
    h2 { font-size: 1.1rem; margin-bottom: .75rem; color: #333; }

    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
    .card {
      background: #fff; border-radius: 10px; padding: 1.25rem 1.5rem;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
    }
    .card .num { font-size: 2rem; font-weight: 700; color: #4361ee; }
    .card .label { font-size: .85rem; color: #666; margin-top: .2rem; }

    table { width: 100%; border-collapse: collapse; background: #fff;
            border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.08); }
    th { background: #f0f2f8; text-align: left; padding: .65rem 1rem; font-size: .82rem;
         text-transform: uppercase; letter-spacing: .5px; color: #555; }
    td { padding: .65rem 1rem; border-top: 1px solid #eee; font-size: .9rem; vertical-align: top; }
    tr:hover td { background: #fafbff; }

    .badge {
      display: inline-block; padding: .2rem .55rem; border-radius: 20px;
      font-size: .75rem; font-weight: 600; background: #e8ecff; color: #4361ee;
    }
    .badge.red   { background: #ffe8e8; color: #d00; }
    .badge.green { background: #e8fff0; color: #0a0; }
    .badge.gray  { background: #eee;    color: #666; }

    .btn {
      display: inline-block; padding: .45rem 1rem; border-radius: 6px;
      font-size: .85rem; font-weight: 600; cursor: pointer; border: none;
      background: #4361ee; color: #fff;
    }
    .btn:hover { background: #3451d1; text-decoration: none; }
    .btn.danger { background: #e74c3c; }
    .btn.danger:hover { background: #c0392b; }
    .btn.secondary { background: #eee; color: #333; }
    .btn.secondary:hover { background: #ddd; }

    form.inline { display: inline; }

    .panel { background: #fff; border-radius: 10px; padding: 1.5rem;
             box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 1.5rem; }

    label { display: block; font-size: .85rem; font-weight: 600; margin-bottom: .3rem; color: #444; }
    input[type=text], select, textarea {
      width: 100%; padding: .5rem .75rem; border: 1px solid #ccc; border-radius: 6px;
      font-size: .9rem; margin-bottom: 1rem;
    }
    textarea { font-family: monospace; height: 120px; }

    .flash { padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1rem;
             background: #e8fff0; color: #0a0; border: 1px solid #b2f0c8; font-size: .9rem; }
    .flash.error { background: #ffe8e8; color: #d00; border-color: #f0b2b2; }

    .kv { display: grid; grid-template-columns: 160px 1fr; gap: .3rem .75rem; font-size: .9rem; }
    .kv .k { color: #666; font-weight: 600; }
    .kv .v { word-break: break-all; }

    .section-title {
      font-size: .75rem; text-transform: uppercase; letter-spacing: .6px;
      color: #999; font-weight: 700; margin: 1.5rem 0 .6rem;
    }
  </style>
</head>
<body>
<nav>
  <span class="logo">⬡ FlatGraphDB</span>
  <a href="/">Dashboard</a>
  <a href="/edges">Edges</a>
  <a href="/traverse">Traversal</a>
  <a href="/gc">Garbage Collector</a>
</nav>
<div class="container">
  {% for msg, cat in get_flashed_messages(with_categories=true) %}
    <div class="flash {% if cat == 'error' %}error{% endif %}">{{ msg }}</div>
  {% endfor %}
  {% block content %}{% endblock %}
</div>
</body>
</html>
"""

DASHBOARD = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h1>Dashboard</h1>
<div class="grid">
  <div class="card"><div class="num">{{ total_nodes }}</div><div class="label">Nodes gesamt</div></div>
  <div class="card"><div class="num">{{ total_edges }}</div><div class="label">Edges gesamt</div></div>
  <div class="card"><div class="num">{{ collections|length }}</div><div class="label">Collections</div></div>
</div>

<h2>Collections</h2>
<table>
  <tr><th>Collection</th><th>Nodes</th><th>Aktion</th></tr>
  {% for col, count in collections %}
  <tr>
    <td><a href="/collection/{{ col }}">{{ col }}</a></td>
    <td><span class="badge">{{ count }}</span></td>
    <td><a class="btn secondary" href="/collection/{{ col }}/new" style="padding:.3rem .7rem;font-size:.8rem">+ Node</a></td>
  </tr>
  {% endfor %}
</table>
{% endblock %}""")

COLLECTION = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem">
  <h1 style="margin:0">{{ collection }}</h1>
  <a class="btn" href="/collection/{{ collection }}/new">+ Node</a>
</div>
<table>
  <tr><th>ID</th>{% for k in fields %}<th>{{ k }}</th>{% endfor %}<th></th></tr>
  {% for nid, data in nodes %}
  <tr>
    <td><a href="/node/{{ collection }}/{{ nid }}"><strong>{{ nid }}</strong></a></td>
    {% for k in fields %}
    <td>{{ data.get(k, '–') }}</td>
    {% endfor %}
    <td>
      <form class="inline" method="post" action="/node/{{ collection }}/{{ nid }}/delete"
            onsubmit="return confirm('Wirklich soft-deleten?')">
        <button class="btn danger" style="padding:.25rem .6rem;font-size:.78rem">Delete</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% endblock %}""")

NODE_DETAIL = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div style="margin-bottom:.5rem"><a href="/collection/{{ ref.split('/')[0] }}">← {{ ref.split('/')[0] }}</a></div>
<h1>{{ ref }}</h1>

<div class="panel">
  <h2>Felder</h2>
  <div class="kv">
    {% for k, v in data.items() %}
    <span class="k">{{ k }}</span><span class="v">{{ v }}</span>
    {% endfor %}
  </div>
  <div style="margin-top:1rem">
    <form class="inline" method="post" action="/node/{{ ref }}/delete"
          onsubmit="return confirm('Wirklich soft-deleten?')">
      <button class="btn danger">Soft Delete</button>
    </form>
  </div>
</div>

{% if out_edges %}
<div class="section-title">Ausgehende Edges (→)</div>
<table>
  <tr><th>Typ</th><th>Ziel</th><th>Metadaten</th></tr>
  {% for eid, e in out_edges %}
  <tr>
    <td><span class="badge">{{ e.typ }}</span></td>
    <td><a href="/node/{{ e.ziel }}">{{ e.ziel }}</a></td>
    <td style="font-size:.82rem;color:#666">
      {% set meta = e.items()|list %}
      {% for k, v in meta if k not in ['quelle','ziel','typ','erstellt_am'] %}
        <strong>{{ k }}</strong>: {{ v }}&nbsp;
      {% endfor %}
    </td>
  </tr>
  {% endfor %}
</table>
{% endif %}

{% if in_edges %}
<div class="section-title">Eingehende Edges (←)</div>
<table>
  <tr><th>Typ</th><th>Quelle</th><th>Metadaten</th></tr>
  {% for eid, e in in_edges %}
  <tr>
    <td><span class="badge gray">{{ e.typ }}</span></td>
    <td><a href="/node/{{ e.quelle }}">{{ e.quelle }}</a></td>
    <td style="font-size:.82rem;color:#666">
      {% set meta = e.items()|list %}
      {% for k, v in meta if k not in ['quelle','ziel','typ','erstellt_am'] %}
        <strong>{{ k }}</strong>: {{ v }}&nbsp;
      {% endfor %}
    </td>
  </tr>
  {% endfor %}
</table>
{% endif %}

{% endblock %}""")

NEW_NODE = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<div style="margin-bottom:.5rem"><a href="/collection/{{ collection }}">← {{ collection }}</a></div>
<h1>Neuer Node in <em>{{ collection }}</em></h1>
<div class="panel">
  <form method="post">
    <label>Node ID</label>
    <input type="text" name="node_id" placeholder="z.B. EQ-004" required>
    <label>Felder (JSON)</label>
    <textarea name="data_json" placeholder='{"name": "Neues Equipment"}'></textarea>
    <button class="btn" type="submit">Anlegen</button>
    <a class="btn secondary" href="/collection/{{ collection }}">Abbrechen</a>
  </form>
</div>
{% endblock %}""")

EDGES_PAGE = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h1>Alle Edges <span class="badge" style="font-size:1rem">{{ edges|length }}</span></h1>
<table>
  <tr><th>ID</th><th>Typ</th><th>Quelle</th><th>Ziel</th><th>Extras</th><th></th></tr>
  {% for eid, e in edges %}
  <tr>
    <td style="font-size:.78rem;color:#999">{{ eid }}</td>
    <td><span class="badge">{{ e.typ }}</span>
        {% if e.get('_cascade_delete') %}<span class="badge red">cascade</span>{% endif %}
    </td>
    <td><a href="/node/{{ e.quelle }}">{{ e.quelle }}</a></td>
    <td><a href="/node/{{ e.ziel }}">{{ e.ziel }}</a></td>
    <td style="font-size:.82rem;color:#666">
      {% for k, v in e.items() if k not in ['quelle','ziel','typ','erstellt_am','_cascade_delete'] %}
        <strong>{{ k }}</strong>: {{ v }}&nbsp;
      {% endfor %}
    </td>
    <td>
      <form class="inline" method="post" action="/edge/{{ eid }}/delete"
            onsubmit="return confirm('Edge löschen?')">
        <button class="btn danger" style="padding:.25rem .6rem;font-size:.78rem">✕</button>
      </form>
    </td>
  </tr>
  {% endfor %}
</table>
{% endblock %}""")

TRAVERSE_PAGE = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h1>Graph-Traversal</h1>
<div class="panel">
  <form method="get">
    <div style="display:grid;grid-template-columns:1fr 1fr 1fr auto;gap:.75rem;align-items:end">
      <div>
        <label>Start-Node (collection/id)</label>
        <input type="text" name="start" value="{{ start or '' }}" placeholder="processes/PROC-001">
      </div>
      <div>
        <label>Beziehungstyp (leer = alle)</label>
        <input type="text" name="rel_type" value="{{ rel_type or '' }}" placeholder="hat_subprozess">
      </div>
      <div>
        <label>Richtung</label>
        <select name="direction">
          <option value="out" {% if direction == 'out' %}selected{% endif %}>Ausgehend (out)</option>
          <option value="in"  {% if direction == 'in'  %}selected{% endif %}>Eingehend (in)</option>
        </select>
      </div>
      <button class="btn" type="submit" style="margin-bottom:1rem">Traversieren</button>
    </div>
  </form>
</div>

{% if start %}
  {% if error %}
    <div class="flash error">{{ error }}</div>
  {% else %}
  <div class="panel">
    <h2>Ergebnis: {{ results|length }} Node(s) gefunden</h2>
    {% if results %}
    <table style="margin-top:.75rem">
      <tr><th>#</th><th>Node-Ref</th><th>Name / Titel</th></tr>
      {% for ref in results %}
      <tr>
        <td style="color:#999">{{ loop.index }}</td>
        <td><a href="/node/{{ ref }}">{{ ref }}</a></td>
        <td>{{ node_names.get(ref, '–') }}</td>
      </tr>
      {% endfor %}
    </table>
    {% else %}
    <p style="color:#999">Keine verbundenen Nodes gefunden.</p>
    {% endif %}
  </div>
  {% endif %}
{% endif %}
{% endblock %}""")

GC_PAGE = BASE.replace("{% block content %}{% endblock %}", """
{% block content %}
<h1>Garbage Collector</h1>
<div class="panel">
  <p style="margin-bottom:1rem;color:#555">
    Bereinigt alle Nodes mit <code>_deletion_flag</code> physisch –
    inklusive Cascade-Delete-Ketten. Idempotent und sicher.
  </p>
  <form method="post" action="/gc/run">
    <button class="btn" type="submit">GC jetzt ausführen</button>
  </form>
</div>

{% if stats %}
<div class="panel">
  <h2>Letzter Lauf</h2>
  <div class="kv" style="margin-top:.5rem">
    <span class="k">Gescannt</span>      <span class="v">{{ stats.scanned }}</span>
    <span class="k">Edges entfernt</span><span class="v">{{ stats.edges_removed }}</span>
    <span class="k">Nodes gelöscht</span><span class="v">{{ stats.nodes_purged }}</span>
    <span class="k">Assets archiviert</span><span class="v">{{ stats.assets_archived }}</span>
  </div>
</div>
{% endif %}

<div class="panel">
  <h2>Soft-deleted Nodes (warten auf GC)</h2>
  {% if pending %}
  <table>
    <tr><th>Ref</th><th>Deletion Flag</th></tr>
    {% for ref, flag in pending %}
    <tr>
      <td>{{ ref }}</td>
      <td style="font-size:.82rem;color:#999">{{ flag }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}
  <p style="color:#999">Keine Nodes warten auf Bereinigung.</p>
  {% endif %}
</div>
{% endblock %}""")


# ----------------------------------------------------------------
# ROUTES
# ----------------------------------------------------------------

@app.route("/")
def dashboard():
    seed_if_empty()
    db = get_db()
    cols = db.list_collections()
    col_data = [(c, len(db.list_nodes(c))) for c in sorted(cols)]
    total_nodes = sum(c[1] for c in col_data)
    total_edges = len(db.list_edges())
    return render_template_string(DASHBOARD, collections=col_data,
                                  total_nodes=total_nodes, total_edges=total_edges)


@app.route("/collection/<name>")
def collection_view(name):
    db = get_db()
    nodes_dict = db.list_nodes(name)
    if not nodes_dict:
        fields = []
        nodes = []
    else:
        all_keys = set()
        for d in nodes_dict.values():
            all_keys.update(k for k in d if not k.startswith("_"))
        fields = sorted(all_keys)
        nodes = sorted(nodes_dict.items())
    return render_template_string(COLLECTION, collection=name, nodes=nodes, fields=fields)


@app.route("/node/<path:ref>")
def node_detail(ref):
    db = get_db()
    data = db.get_node(ref)
    if data is None:
        flash(f"Node '{ref}' nicht gefunden oder gelöscht.", "error")
        return redirect(url_for("dashboard"))
    out_edges = db.get_connected_edges(ref, direction="out")
    in_edges  = db.get_connected_edges(ref, direction="in")
    return render_template_string(NODE_DETAIL, ref=ref, data=data,
                                  out_edges=out_edges, in_edges=in_edges)


@app.route("/node/<path:ref>/delete", methods=["POST"])
def node_delete(ref):
    parts = ref.rsplit("/", 1)
    if len(parts) != 2:
        flash("Ungültige Node-Ref.", "error")
        return redirect(url_for("dashboard"))
    col, nid = parts
    db = get_db()
    db.soft_delete(col, nid)
    flash(f"{ref} wurde soft-deleted und wartet auf den GC.")
    return redirect(url_for("collection_view", name=col))


@app.route("/collection/<name>/new", methods=["GET", "POST"])
def node_new(name):
    if request.method == "POST":
        node_id  = request.form.get("node_id", "").strip()
        data_raw = request.form.get("data_json", "{}").strip()
        if not node_id:
            flash("Node ID darf nicht leer sein.", "error")
            return render_template_string(NEW_NODE, collection=name)
        try:
            data = json.loads(data_raw)
        except json.JSONDecodeError as e:
            flash(f"Ungültiges JSON: {e}", "error")
            return render_template_string(NEW_NODE, collection=name)
        db = get_db()
        try:
            db.create_node(name, node_id, data)
            flash(f"Node '{name}/{node_id}' angelegt.")
            return redirect(url_for("collection_view", name=name))
        except (ValueError, TypeError) as e:
            flash(str(e), "error")
    return render_template_string(NEW_NODE, collection=name)


@app.route("/edges")
def edges_view():
    db = get_db()
    edges = sorted(db.list_edges().items())
    return render_template_string(EDGES_PAGE, edges=edges)


@app.route("/edge/<edge_id>/delete", methods=["POST"])
def edge_delete(edge_id):
    db = get_db()
    db.delete_edge(edge_id)
    flash(f"Edge {edge_id} gelöscht.")
    return redirect(url_for("edges_view"))


@app.route("/traverse")
def traverse_view():
    start     = request.args.get("start", "").strip()
    rel_type  = request.args.get("rel_type", "").strip() or None
    direction = request.args.get("direction", "out")

    results    = []
    node_names = {}
    error      = None

    if start:
        db = get_db()
        if db.get_node(start) is None:
            error = f"Node '{start}' nicht gefunden."
        else:
            results = db.traverse(start, rel_type=rel_type, direction=direction,
                                  include_start=True)
            for ref in results:
                nd = db.get_node(ref)
                if nd:
                    node_names[ref] = nd.get("name") or nd.get("title") or ""

    return render_template_string(TRAVERSE_PAGE, start=start, rel_type=rel_type,
                                  direction=direction, results=results,
                                  node_names=node_names, error=error)


@app.route("/gc")
def gc_view():
    db = get_db()
    pending = []
    for col in db.list_collections():
        all_nodes = db.list_nodes(col, include_deleted=True)
        for nid, data in all_nodes.items():
            if "_deletion_flag" in data:
                pending.append((f"{col}/{nid}", data["_deletion_flag"]))
    return render_template_string(GC_PAGE, stats=None, pending=pending)


@app.route("/gc/run", methods=["POST"])
def gc_run():
    gc = MaintenanceEngine(root_dir=DB_ROOT)
    stats = gc.run_garbage_collection()
    pending = []
    for col in gc.list_collections():
        all_nodes = gc.list_nodes(col, include_deleted=True)
        for nid, data in all_nodes.items():
            if "_deletion_flag" in data:
                pending.append((f"{col}/{nid}", data["_deletion_flag"]))
    flash(f"GC abgeschlossen: {stats['nodes_purged']} Node(s) bereinigt.")
    return render_template_string(GC_PAGE, stats=stats, pending=pending)


if __name__ == "__main__":
    seed_if_empty()
    print("FlatGraphDB Demo läuft auf http://127.0.0.1:5000")
    app.run(debug=True)
