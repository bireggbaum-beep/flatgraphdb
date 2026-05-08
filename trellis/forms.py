"""
Trellis — form parsing and coercion.

Raw HTML form data → structured Python ready to pass to TrellisDB. All
parse-time errors are FormError, a TrellisError subclass, so route handlers
can catch one base class and treat them uniformly.

Routes never see raw `request.form()` data — they pass the FormData straight
into `parse_node_form` and get back a typed dict, or a FormError to render.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .config import NodeType
from .core import TrellisError


class FormError(TrellisError):
    """Form input cannot be parsed or coerced. Always user-facing."""


# ---------------------------------------------------------------- coercion

def coerce(raw: Any, ftype: str) -> Any:
    """Coerce a raw form string into the declared field type."""
    s = str(raw)
    if ftype in ("string", "text"):
        return s
    if ftype == "int":
        try:
            return int(s)
        except ValueError as e:
            raise FormError(f"erwartet eine ganze Zahl, bekam {s!r}") from e
    if ftype == "float":
        try:
            return float(s)
        except ValueError as e:
            raise FormError(f"erwartet eine Zahl, bekam {s!r}") from e
    if ftype == "bool":
        return s.lower() in ("1", "true", "on", "yes", "ja")
    raise FormError(f"unbekannter Feldtyp {ftype!r}")


# ---------------------------------------------------------------- helpers

def multi(form: Mapping[str, Any], key: str) -> list[str]:
    """Pick all values for `key` from a Starlette FormData (multi-valued).

    Falls back to a single-value lookup for plain Mapping inputs (so the
    parser is also testable with a normal dict)."""
    if hasattr(form, "getlist"):
        vals = form.getlist(key)
    else:
        v = form.get(key)
        vals = [v] if v else []
    return [str(v) for v in vals if v not in (None, "")]


# back-compat alias for callers that still use the underscored name
_multi = multi


# ---------------------------------------------------------------- parser

def parse_node_form(form: Mapping[str, Any], spec: NodeType) -> dict[str, Any]:
    """Parse the create-node form for `spec` into a structured dict.

    Returns:
        {
          "fields":           dict[str, Any],
          "current_status":   str | None,
          "pflicht_targets":  dict[str, list[str]],
        }

    Raises:
        FormError on missing required fields or coercion failures.

    The shape mirrors what `TrellisDB.create_node` expects, plus
    `current_status` lifted out so the caller can decide whether to fold it
    into `fields` or keep it separate.
    """
    fields: dict[str, Any] = {}
    for f in spec.fields:
        raw = form.get(f"field__{f.name}")
        if raw is None or raw == "":
            if f.required:
                raise FormError(f"Pflichtfeld fehlt: {f.name}")
            continue
        fields[f.name] = coerce(raw, f.type)

    raw_status = form.get("current_status")
    current_status: str | None = str(raw_status) if raw_status else None

    pflicht_targets: dict[str, list[str]] = {}
    for pk in spec.pflicht_kanten:
        picked = _multi(form, f"pk__{pk.typ}")
        if picked:
            pflicht_targets[pk.typ] = picked

    return {
        "fields":          fields,
        "current_status":  current_status,
        "pflicht_targets": pflicht_targets,
    }


def parse_edit_form(form: Mapping[str, Any], spec: NodeType) -> dict[str, Any]:
    """Parse the edit-form for plain fields + status. No pflicht-kanten here —
    those have their own editors.

    Returns just a dict suitable for `TrellisDB.update_node`.
    """
    fields: dict[str, Any] = {}
    for f in spec.fields:
        if f.name == "name":
            raw = form.get(f"field__{f.name}")
            if raw is None or raw == "":
                raise FormError("Pflichtfeld fehlt: name")
            fields[f.name] = coerce(raw, f.type)
            continue
        # Optional fields: empty string clears, missing means "do not change".
        if f"field__{f.name}" not in form:
            continue
        raw = form.get(f"field__{f.name}")
        if raw == "" or raw is None:
            # leave field unchanged (no clear-to-empty in this MVP)
            continue
        fields[f.name] = coerce(raw, f.type)

    raw_status = form.get("current_status")
    if raw_status is not None and raw_status != "":
        fields["current_status"] = str(raw_status)

    return fields


def parse_contract_form(form: Mapping[str, Any]) -> dict[str, Any]:
    """Parse the inline new-contract form.

    Required fields:
      source_ref         the node we're editing
      target_ref         picked via mention input (single)
      required_status    free-form string
    Optional:
      is_blocker         checkbox
    """
    source = form.get("source_ref")
    if not source:
        raise FormError("source_ref fehlt")
    target_vals = _multi(form, "target_ref")
    if not target_vals:
        raise FormError("Bitte ein Ziel auswählen.")
    target = target_vals[0]
    required_status = form.get("required_status")
    if not required_status:
        raise FormError("Bitte den geforderten Zustand angeben.")
    is_blocker = bool(form.get("is_blocker"))
    return {
        "source_ref":      str(source),
        "target_ref":      str(target),
        "required_status": str(required_status),
        "is_blocker":      is_blocker,
    }
