"""
Trellis — readiness resolver.

A node has a *direct* readiness state computed from its outgoing
contract edges (`requires`-style):

  Green   every contract is satisfied (target.current_status equals
          contract.required_status).
  Red     at least one contract is unsatisfied AND its is_blocker=True.
  Yellow  some contract is unsatisfied but none of the unsatisfied ones
          is a blocker.

The *aggregate* readiness is the worst-of the node's direct readiness and
the aggregate readiness of every contract target reachable from it. This is
the value the dashboard's column-1 icons reflect: a deep red leaf turns its
top-level ancestor red.

Cycles are guarded with a visited set; an already-visited node contributes
green so it doesn't double-count.

This module is pure: it depends on TrellisDB but produces no I/O of its own.
"""

from __future__ import annotations

from typing import Any

from .core import TrellisDB


GREEN  = "green"
YELLOW = "yellow"
RED    = "red"

_ORDER = {GREEN: 0, YELLOW: 1, RED: 2}


def _worse(a: str, b: str) -> str:
    return a if _ORDER[a] >= _ORDER[b] else b


# ---------------------------------------------------------------- direct

def is_satisfied(target_node: dict[str, Any] | None, required_status: str) -> bool:
    """A contract is satisfied iff the target's current_status equals the
    required value. Missing target node or missing status → unsatisfied."""
    if target_node is None:
        return False
    return (target_node.get("current_status") or "") == required_status


def direct_readiness(db: TrellisDB, ref: str) -> str:
    contracts = db.list_contracts(ref, direction="out")
    if not contracts:
        return GREEN
    any_unsatisfied = False
    any_unsatisfied_blocker = False
    for c in contracts:
        target = db.get_node(c["target"])
        if not is_satisfied(target, c.get("required_status") or ""):
            any_unsatisfied = True
            if c.get("is_blocker"):
                any_unsatisfied_blocker = True
    if any_unsatisfied_blocker:
        return RED
    if any_unsatisfied:
        return YELLOW
    return GREEN


# ---------------------------------------------------------------- aggregate

def aggregate_readiness(
    db: TrellisDB,
    ref: str,
    _seen: set[str] | None = None,
) -> str:
    """Worst-of(direct(ref), aggregate(target) for each contract target).

    Memoised lightly via the `_seen` set: a ref already on the current path
    contributes GREEN to break cycles. We do not memoise across calls — the
    underlying graph may have changed and the resolver is fast enough on
    realistic project sizes (lazy traversal, RAM cache in flatgraph).
    """
    if _seen is None:
        _seen = set()
    if ref in _seen:
        return GREEN
    _seen.add(ref)

    worst = direct_readiness(db, ref)
    for c in db.list_contracts(ref, direction="out"):
        sub = aggregate_readiness(db, c["target"], _seen)
        worst = _worse(worst, sub)
    return worst


# ---------------------------------------------------------------- column

def contract_status(is_satisfied_: bool, is_blocker_: bool) -> str:
    """Color for a single contract edge, ignoring cascade.

    Green = the target's current_status matches the requirement.
    Red   = unsatisfied AND blocker.
    Yellow= unsatisfied but not blocker.
    """
    if is_satisfied_:
        return GREEN
    return RED if is_blocker_ else YELLOW


def column_view(db: TrellisDB, ref: str) -> dict[str, Any]:
    """Data for a single drill-down column showing what `ref` directly requires.

    For each outgoing contract the column item carries:
      - the basic contract data (current vs required status, blocker flag),
      - the contract's own status (`contract_status`: R/Y/G of the edge),
      - the target's aggregate readiness (`aggregate_readiness`),
      - an `effective_readiness` = worst-of(contract_status, target aggregate).
        This is what the dot in the column should colour by — it answers
        "is this dependency a problem here, or anywhere below?"
    """
    source_node = db.get_node(ref) or {}
    items: list[dict[str, Any]] = []
    for c in db.list_contracts(ref, direction="out"):
        target = c["target"]
        target_node = db.get_node(target) or {}
        sat = is_satisfied(target_node, c.get("required_status") or "")
        blk = bool(c.get("is_blocker"))
        cs  = contract_status(sat, blk)
        agg = aggregate_readiness(db, target)
        items.append({
            "ref":                 target,
            "name":                target_node.get("name") or target,
            "type":                target.split("/", 1)[0],
            "current_status":      target_node.get("current_status"),
            "required_status":     c.get("required_status"),
            "is_blocker":          blk,
            "is_satisfied":        sat,
            "contract_status":     cs,
            "direct_readiness":    direct_readiness(db, target),
            "aggregate_readiness": agg,
            "effective_readiness": _worse(cs, agg),
            "is_stub":             db.is_stub(target),
        })
    items.sort(key=lambda r: (_ORDER[r["effective_readiness"]] * -1,
                              (r["name"] or "").lower()))
    return {
        "source_ref":             ref,
        "source_name":            source_node.get("name") or ref,
        "source_type":            ref.split("/", 1)[0],
        "source_current_status":  source_node.get("current_status"),
        "source_readiness":       aggregate_readiness(db, ref),
        "items":                  items,
    }


# ---------------------------------------------------------------- top-level

def top_level_view(db: TrellisDB, type_name: str) -> list[dict[str, Any]]:
    """All nodes of `type_name` with their aggregate readiness, sorted
    worst-first then by name. Used as column 1 of the dashboard."""
    rows: list[dict[str, Any]] = []
    for nid, data in db.list_nodes(type_name).items():
        ref = f"{type_name}/{nid}"
        rows.append({
            "ref":                 ref,
            "name":                data.get("name") or nid,
            "current_status":      data.get("current_status"),
            "direct_readiness":    direct_readiness(db, ref),
            "aggregate_readiness": aggregate_readiness(db, ref),
            "is_stub":             db.is_stub(ref),
        })
    rows.sort(key=lambda r: (_ORDER[r["aggregate_readiness"]] * -1,
                             (r["name"] or "").lower()))
    return rows
