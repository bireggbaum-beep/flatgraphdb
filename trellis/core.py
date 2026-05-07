"""
Trellis — typed wrapper over flatgraph.

Adds three things on top of FlatGraphDB:

1. Type discipline driven by `types.yaml` — every node belongs to a type with
   declared statuses, fields, and constitutive (Pflicht-) edges.

2. Two-class edge model — strukturelle (link-field) edges and Vertrags- (contract)
   edges with `required_status` + `is_blocker` metadata. The two are mixed at
   the flatgraph layer but kept separate in the Trellis API.

3. Stub semantics — a node may be created before all its Pflicht-Kanten are
   filled. `is_stub()` reports unfinished business. Stubs are first-class,
   not errors.

All write paths go through a flatgraph transaction so partial state can never
land on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from flatgraph import FlatGraphDB

from .config import (
    ConfigError,
    EdgeType,
    NodeType,
    PflichtKante,
    TrellisConfig,
    TrellisError,
)


# Stored on the edge `meta` dict for vertrag edges.
META_REQUIRED_STATUS = "required_status"
META_IS_BLOCKER = "is_blocker"

# Internal flatgraph fields we manage.
FIELD_CURRENT_STATUS = "current_status"
FIELD_NAME = "name"


class TrellisDB:
    """A typed graph project on disk.

    Layout:
      project_dir/
        types.yaml          ← TrellisConfig source of truth
        data/               ← flatgraph database root
          datenbank/...
          vault/...
    """

    DATA_SUBDIR = "data"
    TYPES_FILE = "types.yaml"

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        if not self.project_dir.is_dir():
            raise TrellisError(f"project directory not found: {self.project_dir}")

        types_path = self.project_dir / self.TYPES_FILE
        self.config = TrellisConfig.load(types_path)

        # Build flatgraph schemas + edge_constraints from the Trellis config so
        # flatgraph itself enforces a baseline of correctness as a second line
        # of defense.
        schemas = _config_to_flatgraph_schemas(self.config)
        edge_constraints = _config_to_edge_constraints(self.config)

        self.db = FlatGraphDB(
            root_dir=str(self.project_dir / self.DATA_SUBDIR),
            schemas=schemas,
            edge_constraints=edge_constraints,
        )

    # ------------------------------------------------------------------ nodes

    def create_node(
        self,
        type_name: str,
        fields: dict[str, Any],
        pflicht_kanten_targets: dict[str, list[str]] | None = None,
    ) -> str:
        """Create a node of `type_name` and (optionally) its Pflicht-Kanten edges.

        `fields` carries plain data fields including the required `name`.
        `pflicht_kanten_targets` is a `{edge_typ: [target_ref, ...]}` dict; any
        Pflicht-Kante that is omitted or under-filled produces a stub — that is
        not an error, the user can complete it later.

        The whole creation runs inside one flatgraph transaction.
        """
        spec = self.config.get_type(type_name)
        targets = pflicht_kanten_targets or {}

        self._validate_fields(spec, fields)
        if FIELD_CURRENT_STATUS in fields:
            self._validate_status(spec, fields[FIELD_CURRENT_STATUS])
        self._validate_pflicht_targets(spec, targets)

        node_id = self.db.next_id(type_name, spec.id_prefix, spec.id_padding)
        ref = f"{type_name}/{node_id}"

        with self.db.transaction():
            self.db.create_node(type_name, node_id, dict(fields))
            for pk in spec.pflicht_kanten:
                for target_ref in targets.get(pk.typ, []):
                    self.db.create_edge(ref, target_ref, pk.typ)
        return ref

    def get_node(self, ref: str) -> dict[str, Any] | None:
        return self.db.get_node(ref)

    def update_node(self, ref: str, fields: dict[str, Any]) -> bool:
        type_name, node_id = _split_ref(ref)
        spec = self.config.get_type(type_name)

        # Allow partial updates: only validate types of provided fields, and
        # the status if it is being changed.
        for fname, value in fields.items():
            field_spec = spec.field(fname)
            if field_spec is None and fname != FIELD_CURRENT_STATUS:
                raise TrellisError(f"{ref}: unknown field {fname!r} for type {type_name!r}")
            if field_spec is not None:
                _check_field_type(spec, field_spec, value)
        if FIELD_CURRENT_STATUS in fields:
            self._validate_status(spec, fields[FIELD_CURRENT_STATUS])

        return self.db.update_node(type_name, node_id, fields)

    def soft_delete_node(self, ref: str) -> bool:
        type_name, node_id = _split_ref(ref)
        return self.db.soft_delete(type_name, node_id)

    def restore_node(self, ref: str) -> bool:
        type_name, node_id = _split_ref(ref)
        return self.db.restore_node(type_name, node_id)

    def list_nodes(self, type_name: str, include_deleted: bool = False) -> dict[str, dict]:
        self.config.get_type(type_name)  # raises if unknown
        return self.db.list_nodes(type_name, include_deleted=include_deleted)

    # ---------------------------------------------------------------- stubs

    def is_stub(self, ref: str) -> bool:
        """A node is a stub iff at least one Pflicht-Kante is not yet satisfied."""
        type_name, _ = _split_ref(ref)
        spec = self.config.get_type(type_name)
        if not spec.pflicht_kanten:
            return False
        for pk in spec.pflicht_kanten:
            edges = self.db.get_connected_edges(ref, direction="out", rel_type=pk.typ)
            count = len(edges)
            if pk.anzahl == "genau_eine" and count != 1:
                return True
            if pk.anzahl == "mindestens_eine" and count < 1:
                return True
        return False

    def list_stubs(self, type_name: str | None = None) -> list[str]:
        types = [type_name] if type_name else self.config.type_names()
        out: list[str] = []
        for t in types:
            for nid in self.db.list_nodes(t).keys():
                ref = f"{t}/{nid}"
                if self.is_stub(ref):
                    out.append(ref)
        return out

    # ------------------------------------------------------------- contracts

    def add_contract(
        self,
        source_ref: str,
        target_ref: str,
        required_status: str,
        is_blocker: bool,
        edge_type: str = "requires",
    ) -> str:
        et = self.config.get_edge_type(edge_type)
        if et.kategorie != "vertrag":
            raise TrellisError(
                f"add_contract: edge type {edge_type!r} is not a vertrag edge "
                f"(kategorie={et.kategorie!r})"
            )
        # Verify the required_status is at least known to the target's type —
        # not a hard requirement (Wachstums-Axiom), but we warn-by-error if
        # the target type has a status list and the value is not in it. For
        # types without a declared status list we accept any string.
        target_type, _ = _split_ref(target_ref)
        target_spec = self.config.get_type(target_type)
        if target_spec.statuses and required_status not in target_spec.statuses:
            raise TrellisError(
                f"add_contract: required_status {required_status!r} is not in the "
                f"declared statuses of type {target_type!r}: {list(target_spec.statuses)}. "
                f"Add it to types.yaml first if you really want it."
            )
        if not isinstance(is_blocker, bool):
            raise TrellisError("add_contract: is_blocker must be a bool")

        return self.db.create_edge(
            source_ref,
            target_ref,
            edge_type,
            meta={
                META_REQUIRED_STATUS: required_status,
                META_IS_BLOCKER: is_blocker,
            },
        )

    def remove_contract(self, edge_id: str) -> bool:
        return self.db.delete_edge(edge_id)

    def list_contracts(self, ref: str, direction: str = "out") -> list[dict]:
        """Return all vertrag-edges incident on ref in the given direction.

        Each entry: {edge_id, source, target, type, required_status, is_blocker}.
        """
        out: list[dict] = []
        vertrag_types = [
            n for n, e in self.config.edge_types.items() if e.kategorie == "vertrag"
        ]
        for et in vertrag_types:
            for edge_id, edge in self.db.get_connected_edges(ref, direction=direction, rel_type=et):
                out.append({
                    "edge_id": edge_id,
                    "source": edge["source"],
                    "target": edge["target"],
                    "type": edge["type"],
                    META_REQUIRED_STATUS: edge.get(META_REQUIRED_STATUS),
                    META_IS_BLOCKER: edge.get(META_IS_BLOCKER),
                })
        return out

    # ------------------------------------------------- structural edges (links)

    def add_structural_edge(self, source_ref: str, target_ref: str, edge_type: str) -> str:
        """Add a strukturell edge — used for filling in Pflicht-Kante slots
        after node creation, or for any additional link-field connection."""
        et = self.config.get_edge_type(edge_type)
        if et.kategorie != "strukturell":
            raise TrellisError(
                f"add_structural_edge: edge type {edge_type!r} is not strukturell "
                f"(kategorie={et.kategorie!r})"
            )
        return self.db.create_edge(source_ref, target_ref, edge_type)

    def remove_structural_edge(self, edge_id: str) -> bool:
        return self.db.delete_edge(edge_id)

    # ------------------------------------------------------------------ search

    def search(
        self,
        query: str,
        type_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Substring search over `name` field, optionally limited to one type.

        Returns ranked results as [{ref, type, name, current_status, is_stub}, ...].
        Used to back the @-mention picker.
        """
        q = (query or "").strip().lower()
        types = [type_name] if type_name else self.config.type_names()
        hits: list[dict[str, Any]] = []
        for t in types:
            for nid, data in self.db.list_nodes(t, readonly=True).items():
                name = (data.get(FIELD_NAME) or "").lower()
                if q and q not in name:
                    continue
                ref = f"{t}/{nid}"
                hits.append({
                    "ref": ref,
                    "type": t,
                    "name": data.get(FIELD_NAME, ""),
                    FIELD_CURRENT_STATUS: data.get(FIELD_CURRENT_STATUS),
                    "is_stub": self.is_stub(ref),
                })
                if len(hits) >= limit:
                    return hits
        return hits

    # ----------------------------------------------------------------- inspector

    def inspector(self, ref: str) -> dict[str, Any]:
        """Everything the right-hand inspector panel needs in one call."""
        type_name, _ = _split_ref(ref)
        spec = self.config.get_type(type_name)
        node = self.db.get_node(ref)
        if node is None:
            raise TrellisError(f"node not found: {ref}")

        struct_out = self._grouped_structural_edges(ref, "out")
        struct_in = self._grouped_structural_edges(ref, "in")
        contracts_out = self.list_contracts(ref, direction="out")
        contracts_in = self.list_contracts(ref, direction="in")
        return {
            "ref": ref,
            "type": type_name,
            "node": node,
            "type_spec": _node_type_to_dict(spec),
            "is_stub": self.is_stub(ref),
            "structural_out": struct_out,
            "structural_in": struct_in,
            "contracts_out": contracts_out,
            "contracts_in": contracts_in,
        }

    # --------------------------------------------------------------- internals

    def _grouped_structural_edges(self, ref: str, direction: str) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for et_name, et in self.config.edge_types.items():
            if et.kategorie != "strukturell":
                continue
            edges = self.db.get_connected_edges(ref, direction=direction, rel_type=et_name)
            if not edges:
                continue
            out[et_name] = [
                {"edge_id": eid, "other": e["target"] if direction == "out" else e["source"]}
                for eid, e in edges
            ]
        return out

    def _validate_fields(self, spec: NodeType, fields: dict[str, Any]) -> None:
        provided = set(fields.keys()) - {FIELD_CURRENT_STATUS}
        declared = {f.name for f in spec.fields}
        unknown = provided - declared
        if unknown:
            raise TrellisError(
                f"type {spec.name!r}: unknown fields: {sorted(unknown)} "
                f"(declared: {sorted(declared)})"
            )
        for f in spec.fields:
            if f.required and f.name not in fields:
                raise TrellisError(f"type {spec.name!r}: required field {f.name!r} missing")
            if f.name in fields:
                _check_field_type(spec, f, fields[f.name])

    def _validate_status(self, spec: NodeType, status: Any) -> None:
        if not isinstance(status, str):
            raise TrellisError(f"type {spec.name!r}: current_status must be a string")
        if spec.statuses and status not in spec.statuses:
            raise TrellisError(
                f"type {spec.name!r}: status {status!r} not in declared statuses "
                f"{list(spec.statuses)}. Add it to types.yaml first."
            )

    def _validate_pflicht_targets(
        self,
        spec: NodeType,
        targets: dict[str, list[str]],
    ) -> None:
        declared = {pk.typ for pk in spec.pflicht_kanten}
        unknown = set(targets.keys()) - declared
        if unknown:
            raise TrellisError(
                f"type {spec.name!r}: unknown pflicht_kanten in input: {sorted(unknown)}"
            )
        for pk in spec.pflicht_kanten:
            refs = targets.get(pk.typ, [])
            if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                raise TrellisError(
                    f"type {spec.name!r}: pflicht_kante {pk.typ!r} expects a list of refs"
                )
            if pk.anzahl == "genau_eine" and len(refs) > 1:
                raise TrellisError(
                    f"type {spec.name!r}: pflicht_kante {pk.typ!r} permits exactly one target, "
                    f"got {len(refs)}"
                )
            for tref in refs:
                ttype, _ = _split_ref(tref)
                if ttype not in pk.ziel_typen:
                    raise TrellisError(
                        f"type {spec.name!r}: pflicht_kante {pk.typ!r} target {tref!r} "
                        f"has type {ttype!r}, allowed: {list(pk.ziel_typen)}"
                    )
                if self.db.get_node(tref) is None:
                    raise TrellisError(
                        f"type {spec.name!r}: pflicht_kante {pk.typ!r} target {tref!r} "
                        f"does not exist (or is soft-deleted)"
                    )


# --------------------------------------------------------------------- helpers


def _split_ref(ref: str) -> tuple[str, str]:
    if not isinstance(ref, str) or "/" not in ref:
        raise TrellisError(f"invalid node ref: {ref!r} (expected 'Type/id')")
    type_name, node_id = ref.split("/", 1)
    if not type_name or not node_id:
        raise TrellisError(f"invalid node ref: {ref!r}")
    return type_name, node_id


def _check_field_type(spec: NodeType, f, value: Any) -> None:
    type_map = {
        "string": str,
        "text":   str,
        "int":    int,
        "float":  (int, float),
        "bool":   bool,
    }
    expected = type_map[f.type]
    if not isinstance(value, expected):
        raise TrellisError(
            f"type {spec.name!r}: field {f.name!r} expects {f.type!r}, got "
            f"{type(value).__name__}"
        )


def _node_type_to_dict(spec: NodeType) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "statuses": list(spec.statuses),
        "fields": [
            {"name": f.name, "type": f.type, "required": f.required}
            for f in spec.fields
        ],
        "pflicht_kanten": [
            {"typ": pk.typ, "ziel_typen": list(pk.ziel_typen), "anzahl": pk.anzahl}
            for pk in spec.pflicht_kanten
        ],
    }


def _config_to_flatgraph_schemas(config: TrellisConfig) -> dict[str, dict]:
    """Translate Trellis field specs into flatgraph schemas — the second line
    of defense. We only declare hard, non-optional fields here; Trellis itself
    does the richer validation."""
    out: dict[str, dict] = {}
    type_to_python = {
        "string": str,
        "text":   str,
        "int":    int,
        "float":  float,
        "bool":   bool,
    }
    for tname, spec in config.types.items():
        schema: dict[str, Any] = {}
        for f in spec.fields:
            if f.required:
                schema[f.name] = type_to_python[f.type]
        if schema:
            out[tname] = schema
    return out


def _config_to_edge_constraints(config: TrellisConfig) -> dict[str, list[tuple[str, str]]]:
    """For every Pflicht-Kante (and only those), declare the allowed
    (source_type, target_type) pairs to flatgraph. Vertrags- and free
    strukturelle edges remain unconstrained at the flatgraph layer; Trellis
    enforces them via add_contract / add_structural_edge."""
    out: dict[str, list[tuple[str, str]]] = {}
    for tname, spec in config.types.items():
        for pk in spec.pflicht_kanten:
            out.setdefault(pk.typ, [])
            for ziel in pk.ziel_typen:
                pair = (tname, ziel)
                if pair not in out[pk.typ]:
                    out[pk.typ].append(pair)
    return out
