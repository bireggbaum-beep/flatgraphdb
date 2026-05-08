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
        # nav / layout
        "selected_type":       None,
        "selected_ref":        None,
        "nodes":               [],
        # inspector
        "inspector":           None,
        # create form
        "form_spec":           None,
        "candidates":          {},
        "form_error":          None,
        # edit form
        "edit_node":           None,
        "edit_ref":             None,
        # contract editor (lives inside the inspector partial)
        "contract_form_open":  False,
        "contract_form_error": None,
        "known_statuses":      [],
        "all_type_names":      [],
        # mention popup
        "hits":                [],
        "create_for":          [],
        "query":               "",
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
