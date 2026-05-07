"""
Trellis — type registry loader.

Reads `types.yaml` from a project directory and exposes a validated, queryable
view of the types and edge-types the project knows about. This module is
deliberately read-only for now: edits happen by hand. A UI-editor that
round-trips comments will be added later via ruamel.yaml.

Robustheits-Axiom: every malformed config raises an explicit, actionable error.
We never silently coerce or drop unknown keys.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml


VALID_FIELD_TYPES = {"string", "int", "float", "bool", "text"}
VALID_ANZAHL = {"genau_eine", "mindestens_eine"}
VALID_KATEGORIE = {"strukturell", "vertrag"}


class TrellisError(Exception):
    """Base class for all Trellis-raised errors. Defined here so config.py
    has no upward dependency on core.py."""


class ConfigError(TrellisError, ValueError):
    """Raised when types.yaml is malformed or internally inconsistent.
    Inherits from TrellisError so callers can catch the umbrella class,
    and from ValueError so legacy `except ValueError` still works."""


@dataclasses.dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    required: bool


@dataclasses.dataclass(frozen=True)
class PflichtKante:
    typ: str                    # edge-type name, must exist in edge_types
    ziel_typen: tuple[str, ...] # allowed target node types
    anzahl: str                 # "genau_eine" | "mindestens_eine"


@dataclasses.dataclass(frozen=True)
class NodeType:
    name: str
    description: str
    id_prefix: str
    id_padding: int
    statuses: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    pflicht_kanten: tuple[PflichtKante, ...]

    def field(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def is_known_status(self, status: str) -> bool:
        return status in self.statuses


@dataclasses.dataclass(frozen=True)
class EdgeType:
    name: str
    kategorie: str                       # "strukturell" | "vertrag"
    meta_felder: tuple[str, ...]         # only relevant for "vertrag"


class TrellisConfig:
    """In-memory, validated view of a project's `types.yaml`."""

    def __init__(self, types: dict[str, NodeType], edge_types: dict[str, EdgeType]):
        self.types = types
        self.edge_types = edge_types

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, path: str | Path) -> "TrellisConfig":
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"types.yaml not found at {path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ConfigError(f"types.yaml is not valid YAML: {e}") from e

        if not isinstance(raw, dict):
            raise ConfigError("types.yaml top-level must be a mapping")

        edge_types = _parse_edge_types(raw.get("edge_types") or {})
        types = _parse_types(raw.get("types") or {}, edge_types)
        return cls(types=types, edge_types=edge_types)

    # ------------------------------------------------------------------ query

    def get_type(self, name: str) -> NodeType:
        if name not in self.types:
            raise ConfigError(f"unknown node type: {name!r}")
        return self.types[name]

    def get_edge_type(self, name: str) -> EdgeType:
        if name not in self.edge_types:
            raise ConfigError(f"unknown edge type: {name!r}")
        return self.edge_types[name]

    def type_names(self) -> list[str]:
        return list(self.types.keys())

    def edge_type_names(self) -> list[str]:
        return list(self.edge_types.keys())


# ---------------------------------------------------------------------- parser

def _parse_edge_types(raw: dict[str, Any]) -> dict[str, EdgeType]:
    if not isinstance(raw, dict):
        raise ConfigError("`edge_types` must be a mapping")
    out: dict[str, EdgeType] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"edge_type {name!r}: must be a mapping")
        kategorie = spec.get("kategorie")
        if kategorie not in VALID_KATEGORIE:
            raise ConfigError(
                f"edge_type {name!r}: kategorie must be one of "
                f"{sorted(VALID_KATEGORIE)}, got {kategorie!r}"
            )
        meta = spec.get("meta_felder") or []
        if not isinstance(meta, list) or not all(isinstance(m, str) for m in meta):
            raise ConfigError(f"edge_type {name!r}: meta_felder must be a list of strings")
        if kategorie == "strukturell" and meta:
            raise ConfigError(
                f"edge_type {name!r}: strukturell edges must not declare meta_felder"
            )
        out[name] = EdgeType(name=name, kategorie=kategorie, meta_felder=tuple(meta))
    return out


def _parse_types(raw: dict[str, Any], edge_types: dict[str, EdgeType]) -> dict[str, NodeType]:
    if not isinstance(raw, dict):
        raise ConfigError("`types` must be a mapping")

    type_names = set(raw.keys())
    out: dict[str, NodeType] = {}

    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"type {name!r}: must be a mapping")

        description = str(spec.get("description") or "").strip()
        id_prefix = spec.get("id_prefix")
        if not isinstance(id_prefix, str) or not id_prefix:
            raise ConfigError(f"type {name!r}: id_prefix is required and must be a non-empty string")

        id_padding = spec.get("id_padding", 0)
        if not isinstance(id_padding, int) or id_padding < 0:
            raise ConfigError(f"type {name!r}: id_padding must be a non-negative integer")

        statuses = spec.get("statuses") or []
        if not isinstance(statuses, list) or not all(isinstance(s, str) for s in statuses):
            raise ConfigError(f"type {name!r}: statuses must be a list of strings")
        if len(statuses) != len(set(statuses)):
            raise ConfigError(f"type {name!r}: statuses contains duplicates")

        fields = _parse_fields(name, spec.get("fields") or [])
        if not any(f.name == "name" and f.required for f in fields):
            raise ConfigError(
                f"type {name!r}: must declare a required `name` field "
                f"(string). This is a hard convention, not a flatgraph constraint."
            )

        pflicht_kanten = _parse_pflicht_kanten(name, spec.get("pflicht_kanten") or [],
                                               edge_types, type_names)

        out[name] = NodeType(
            name=name,
            description=description,
            id_prefix=id_prefix,
            id_padding=id_padding,
            statuses=tuple(statuses),
            fields=tuple(fields),
            pflicht_kanten=tuple(pflicht_kanten),
        )

    return out


def _parse_fields(type_name: str, raw: list[Any]) -> list[FieldSpec]:
    if not isinstance(raw, list):
        raise ConfigError(f"type {type_name!r}: fields must be a list")
    seen: set[str] = set()
    out: list[FieldSpec] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"type {type_name!r}: each field must be a mapping")
        fname = entry.get("name")
        ftype = entry.get("type")
        frequired = entry.get("required", False)
        if not isinstance(fname, str) or not fname:
            raise ConfigError(f"type {type_name!r}: field `name` is required")
        if fname in seen:
            raise ConfigError(f"type {type_name!r}: duplicate field {fname!r}")
        seen.add(fname)
        if ftype not in VALID_FIELD_TYPES:
            raise ConfigError(
                f"type {type_name!r}: field {fname!r} has unknown type {ftype!r} "
                f"(valid: {sorted(VALID_FIELD_TYPES)})"
            )
        if not isinstance(frequired, bool):
            raise ConfigError(f"type {type_name!r}: field {fname!r} `required` must be a boolean")
        out.append(FieldSpec(name=fname, type=ftype, required=frequired))
    return out


def _parse_pflicht_kanten(type_name: str, raw: list[Any],
                          edge_types: dict[str, EdgeType],
                          type_names: set[str]) -> list[PflichtKante]:
    if not isinstance(raw, list):
        raise ConfigError(f"type {type_name!r}: pflicht_kanten must be a list")
    out: list[PflichtKante] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ConfigError(f"type {type_name!r}: each pflicht_kante must be a mapping")
        e_typ = entry.get("typ")
        if e_typ not in edge_types:
            raise ConfigError(
                f"type {type_name!r}: pflicht_kante refers to unknown edge type {e_typ!r}"
            )
        if edge_types[e_typ].kategorie != "strukturell":
            raise ConfigError(
                f"type {type_name!r}: pflicht_kante {e_typ!r} must be of kategorie 'strukturell', "
                f"got {edge_types[e_typ].kategorie!r}"
            )
        ziel_typen = entry.get("ziel_typen") or []
        if not isinstance(ziel_typen, list) or not all(isinstance(t, str) for t in ziel_typen):
            raise ConfigError(
                f"type {type_name!r}: pflicht_kante {e_typ!r}: ziel_typen must be a list of strings"
            )
        unknown = [t for t in ziel_typen if t not in type_names]
        if unknown:
            raise ConfigError(
                f"type {type_name!r}: pflicht_kante {e_typ!r} references unknown target types: {unknown}"
            )
        anzahl = entry.get("anzahl")
        if anzahl not in VALID_ANZAHL:
            raise ConfigError(
                f"type {type_name!r}: pflicht_kante {e_typ!r}: anzahl must be one of "
                f"{sorted(VALID_ANZAHL)}, got {anzahl!r}"
            )
        out.append(PflichtKante(typ=e_typ, ziel_typen=tuple(ziel_typen), anzahl=anzahl))
    return out
