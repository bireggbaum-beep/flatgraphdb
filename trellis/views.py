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
        "selected_type": None,
        "selected_ref":  None,
        "nodes":         [],
        "inspector":     None,
        "form_spec":     None,
        "candidates":    {},
        "form_error":    None,
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
