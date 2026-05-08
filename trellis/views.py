"""
Trellis — view-model builders.

Pure functions that take a TrellisDB and return template-ready dicts and
lists. No HTTP, no Jinja, no FastAPI imports — so they're trivial to call
from tests, scripts, or future alternative UIs.

The route layer composes these into the final template context.
"""

from __future__ import annotations

from typing import Any

from .config import NodeType, TrellisConfig
from .core import TrellisDB


# ---------------------------------------------------------------- defaults

def empty_state() -> dict[str, Any]:
    """Default values for every variable the templates may reference.

    Routes start from this and override only what their view actually fills.
    Avoids "Undefined in template" surprises when partials are rendered
    independently.
    """
    return {
        # global tab
        "active_tab":          "modell",
        # nav / layout
        "selected_type":       None,
        "selected_ref":        None,
        "nodes":               [],
        # inspector
        "inspector":           None,
        "structural_state":    None,
        "structural_form_pk":  None,
        "structural_error":    None,
        # create form
        "form_spec":           None,
        "candidates":          {},
        "form_error":          None,
        # edit form
        "edit_node":           None,
        "edit_ref":            None,
        # contract editor
        "contract_form_open":  False,
        "contract_form_error": None,
        "known_statuses":      [],
        "all_type_names":      [],
        # mention popup
        "hits":                [],
        "create_for":          [],
        "query":               "",
        # readiness dashboard
        "dashboard_type":      None,
        "dashboard_columns":   [],
        # settings
        "settings_yaml":       "",
    }


# ---------------------------------------------------------------- common

def common(db: TrellisDB) -> dict[str, Any]:
    """Variables every page needs: the type list and the active config."""
    return {
        "type_names": db.config.type_names(),
        "config":     db.config,
    }


# ---------------------------------------------------------------- views

def list_view(db: TrellisDB, type_name: str) -> list[dict[str, Any]]:
    """Rows for the middle column when a type is selected."""
    rows: list[dict[str, Any]] = []
    for nid, data in db.list_nodes(type_name).items():
        ref = f"{type_name}/{nid}"
        rows.append({
            "ref":            ref,
            "id":             nid,
            "name":           data.get("name", nid),
            "current_status": data.get("current_status"),
            "is_stub":        db.is_stub(ref),
        })
    rows.sort(key=lambda r: (r["name"] or "").lower())
    return rows


def inspector_view(db: TrellisDB, ref: str) -> dict[str, Any]:
    """Right-hand inspector panel — currently a thin wrapper.

    Kept as its own function so additional UI-only adornments (e.g. derived
    badges, formatted timestamps) can land here without polluting core.
    """
    return db.inspector(ref)


def candidates_for_pflicht(
    db: TrellisDB,
    spec: NodeType,
) -> dict[str, list[dict[str, Any]]]:
    """For each Pflicht-Kante on `spec`, return the picker options.

    The structure is `{edge_type: [{ref, type, name}, ...]}`. Sorted within
    each edge type by (target type, name) for stable rendering.
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for pk in spec.pflicht_kanten:
        opts: list[dict[str, Any]] = []
        for ttype in pk.ziel_typen:
            for nid, data in db.list_nodes(ttype).items():
                opts.append({
                    "ref":  f"{ttype}/{nid}",
                    "type": ttype,
                    "name": data.get("name", nid),
                })
        opts.sort(key=lambda o: (o["type"], (o["name"] or "").lower()))
        out[pk.typ] = opts
    return out


# ---------------------------------------------------------------- mention

MENTION_LIMIT = 12


def mention_results(
    db: TrellisDB,
    query: str,
    types: list[str],
    *,
    limit: int = MENTION_LIMIT,
) -> dict[str, Any]:
    """Build the data for a mention-popup partial.

    `types` constrains the search; an empty list means "any type". `query`
    is a non-empty trimmed search string. The popup also offers inline-create
    options for every type listed (or all types if `types` is empty).
    """
    known = set(db.config.type_names())
    asked = [t for t in types if t in known] or list(known)

    hits: list[dict[str, Any]] = []
    for t in asked:
        for h in db.search(query, type_name=t, limit=limit):
            hits.append(h)
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break

    return {
        "query":      query,
        "hits":       hits,
        "create_for": asked,
    }


def collect_known_statuses(db: TrellisDB) -> list[str]:
    """Union of all statuses declared in types.yaml across all types.

    Drives the autocomplete corpus for current_status / required_status,
    even when the target type's own list is short. Free-form values still
    pass — this is a hint, not a gate.
    """
    seen: dict[str, None] = {}
    for spec in db.config.types.values():
        for s in spec.statuses:
            seen[s] = None
    return list(seen.keys())


# ---------------------------------------------------------------- structural

def structural_state(db: TrellisDB, ref: str) -> dict[str, Any]:
    """Group structural edges by edge type for the inspector's edit UI.

    Returns:
        {
          "ref":            "<ref>",
          "pflicht_slots":  [
            {
              "typ":         "betrifft",
              "ziel_typen":  ["Equipment", ...],
              "anzahl":      "genau_eine"|"mindestens_eine",
              "edges":       [{edge_id, other_ref, other_name, other_type}, ...],
              "is_full":     bool — for genau_eine: 1; otherwise False (always addable)
            }, ...
          ],
          "extra":          [   # structural edges that aren't declared as pflicht
            {typ, edges: [...]}
          ]
        }
    """
    type_name, _ = ref.split("/", 1)
    spec = db.config.get_type(type_name)
    out_edges = db.db.get_connected_edges(ref, direction="out")  # all out-edges

    # Build {edge_type: [(edge_id, target_ref), ...]}
    by_type: dict[str, list[tuple[str, str]]] = {}
    for eid, e in out_edges:
        et = e["type"]
        meta = db.config.edge_types.get(et)
        if meta is None or meta.kategorie != "strukturell":
            continue
        by_type.setdefault(et, []).append((eid, e["target"]))

    pflicht_slots: list[dict[str, Any]] = []
    seen_pk_typs: set[str] = set()
    for pk in spec.pflicht_kanten:
        seen_pk_typs.add(pk.typ)
        edges_raw = by_type.get(pk.typ, [])
        edges = [_describe_other(db, eid, oref) for eid, oref in edges_raw]
        is_full = pk.anzahl == "genau_eine" and len(edges) >= 1
        pflicht_slots.append({
            "typ":        pk.typ,
            "ziel_typen": list(pk.ziel_typen),
            "anzahl":     pk.anzahl,
            "edges":      edges,
            "is_full":    is_full,
        })

    extra: list[dict[str, Any]] = []
    for et, raw in by_type.items():
        if et in seen_pk_typs:
            continue
        extra.append({
            "typ":   et,
            "edges": [_describe_other(db, eid, oref) for eid, oref in raw],
        })

    return {
        "ref":           ref,
        "pflicht_slots": pflicht_slots,
        "extra":         extra,
    }


def _describe_other(db: TrellisDB, edge_id: str, other_ref: str) -> dict[str, Any]:
    other_node = db.get_node(other_ref) or {}
    return {
        "edge_id":    edge_id,
        "other_ref":  other_ref,
        "other_name": other_node.get("name") or other_ref,
        "other_type": other_ref.split("/", 1)[0],
    }


# ---------------------------------------------------------------- dashboard

def build_dashboard_columns(
    db: TrellisDB,
    type_name: str,
    refs: list[str],
) -> list[dict[str, Any]]:
    """Build the column list for the readiness dashboard.

    Column 0 is the top-level list of nodes of `type_name`. Each subsequent
    column is the drill-down for the corresponding ref in `refs`. Each item
    in every column carries a `next_path` so the template can wire the click
    URL without doing path arithmetic itself.
    """
    from . import readiness

    columns: list[dict[str, Any]] = []

    top = readiness.top_level_view(db, type_name)
    for row in top:
        row["next_path"] = row["ref"]
    columns.append({
        "kind":         "top",
        "type_name":    type_name,
        "rows":         top,
        "selected_ref": refs[0] if refs else None,
    })

    for i, src in enumerate(refs):
        col = readiness.column_view(db, src)
        prefix = refs[: i + 1]
        for item in col["items"]:
            item["next_path"] = ",".join(prefix + [item["ref"]])
        columns.append({
            "kind":         "drill",
            "source":       col,
            "selected_ref": refs[i + 1] if i + 1 < len(refs) else None,
        })
    return columns
