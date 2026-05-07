"""
Trellis — FastAPI/HTMX web layer.

This is the user-facing skin over the typed graph backend in `trellis.core`.

Conventions:
  * One project per server process. The directory is supplied via CLI/env
    and resolved once at startup; switching projects = restarting.
  * HTMX-aware: every route returns either a full page or a partial,
    depending on the `HX-Request` header. Partials use the same templates
    via `{% include %}`.
  * No JSON API for the UI — the response is always HTML. A separate JSON
    surface can be added later if needed for scripting.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .core import TrellisDB, TrellisError
from .config import ConfigError


# ---------------------------------------------------------------- bootstrap

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(project_dir: str | Path) -> FastAPI:
    """Build the FastAPI app bound to a single project directory.

    Kept as a factory so tests can spin up isolated instances.
    """
    project_dir = Path(project_dir).resolve()
    db = TrellisDB(project_dir)

    app = FastAPI(title="Trellis", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.db = db
    app.state.project_dir = project_dir

    _register_routes(app)
    return app


# ---------------------------------------------------------------- helpers

def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def _common_context(request: Request) -> dict[str, Any]:
    db: TrellisDB = request.app.state.db
    return {
        "request":      request,
        "project_name": request.app.state.project_dir.name,
        "type_names":   db.config.type_names(),
        "config":       db.config,
    }


def _render(
    request: Request,
    full_template: str,
    partial_template: str,
    extra: dict[str, Any],
) -> HTMLResponse:
    """Pick partial or full template depending on whether HTMX requested it."""
    ctx = _common_context(request)
    ctx.update(extra)
    template = partial_template if _is_htmx(request) else full_template
    return templates.TemplateResponse(request, template, ctx)


def _split_ref(ref: str) -> tuple[str, str]:
    if "/" not in ref:
        raise HTTPException(400, f"invalid ref: {ref!r}")
    t, nid = ref.split("/", 1)
    return t, nid


# ---------------------------------------------------------------- routes

def _register_routes(app: FastAPI) -> None:

    # -------- index -------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        ctx = _common_context(request)
        ctx["selected_type"] = None
        ctx["nodes"] = {}
        ctx["selected_ref"] = None
        ctx["inspector"] = None
        return templates.TemplateResponse(request, "index.html", ctx)

    # -------- list nodes of a type ---------------------------------------

    @app.get("/types/{type_name}", response_class=HTMLResponse)
    def list_type(request: Request, type_name: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        try:
            db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))

        nodes = db.list_nodes(type_name)
        rows = []
        for nid, data in nodes.items():
            ref = f"{type_name}/{nid}"
            rows.append({
                "ref":            ref,
                "id":             nid,
                "name":           data.get("name", nid),
                "current_status": data.get("current_status"),
                "is_stub":        db.is_stub(ref),
            })
        rows.sort(key=lambda r: r["name"].lower())

        return _render(
            request,
            full_template="index.html",
            partial_template="_list.html",
            extra={
                "selected_type": type_name,
                "nodes":         rows,
                "selected_ref":  None,
                "inspector":     None,
            },
        )

    # -------- inspector for one node -------------------------------------

    @app.get("/nodes/{type_name}/{node_id}", response_class=HTMLResponse)
    def show_node(request: Request, type_name: str, node_id: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        ref = f"{type_name}/{node_id}"
        try:
            insp = db.inspector(ref)
        except TrellisError as e:
            raise HTTPException(404, str(e))

        return _render(
            request,
            full_template="index.html",
            partial_template="_inspector.html",
            extra={
                "selected_type": type_name,
                "selected_ref":  ref,
                "nodes":         [],
                "inspector":     insp,
            },
        )

    # -------- create-node form -------------------------------------------

    @app.get("/new/{type_name}", response_class=HTMLResponse)
    def new_form(request: Request, type_name: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        try:
            spec = db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))

        # For each pflicht-kante, prepare candidate-target lists so the form
        # can render a dropdown / datalist of allowed existing nodes.
        candidates: dict[str, list[dict[str, Any]]] = {}
        for pk in spec.pflicht_kanten:
            opts: list[dict[str, Any]] = []
            for ttype in pk.ziel_typen:
                for nid, data in db.list_nodes(ttype).items():
                    opts.append({
                        "ref":  f"{ttype}/{nid}",
                        "type": ttype,
                        "name": data.get("name", nid),
                    })
            opts.sort(key=lambda o: (o["type"], o["name"].lower()))
            candidates[pk.typ] = opts

        return _render(
            request,
            full_template="index.html",
            partial_template="_form_create.html",
            extra={
                "selected_type": type_name,
                "form_spec":     spec,
                "candidates":    candidates,
                "form_error":    None,
                "selected_ref":  None,
                "nodes":         [],
                "inspector":     None,
            },
        )

    # -------- create-node submit -----------------------------------------

    @app.post("/new/{type_name}")
    async def new_submit(request: Request, type_name: str) -> Response:
        db: TrellisDB = request.app.state.db
        try:
            spec = db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))

        form = await request.form()
        fields: dict[str, Any] = {}
        for f in spec.fields:
            raw = form.get(f"field__{f.name}")
            if raw is None or raw == "":
                if f.required:
                    return _form_error(request, spec, db, "Pflichtfeld fehlt: " + f.name)
                continue
            fields[f.name] = _coerce(raw, f.type)

        status = form.get("current_status")
        if status:
            fields["current_status"] = str(status)

        pflicht_targets: dict[str, list[str]] = {}
        for pk in spec.pflicht_kanten:
            picked = form.getlist(f"pk__{pk.typ}")
            picked = [p for p in picked if p]
            if picked:
                pflicht_targets[pk.typ] = picked

        try:
            ref = db.create_node(type_name, fields, pflicht_targets)
        except TrellisError as e:
            return _form_error(request, spec, db, str(e))

        # Successful create: tell HTMX to swap the inspector into view AND
        # refresh the middle column, then return the inspector partial.
        t, nid = _split_ref(ref)
        if _is_htmx(request):
            insp = db.inspector(ref)
            ctx = _common_context(request)
            ctx.update({
                "selected_type": t,
                "selected_ref":  ref,
                "inspector":     insp,
                "nodes":         [],
            })
            resp = templates.TemplateResponse(request, "_inspector.html", ctx)
            resp.headers["HX-Trigger"] = f"refresh-list-{t}"
            return resp
        return RedirectResponse(url=f"/nodes/{t}/{nid}", status_code=303)


def _coerce(raw: Any, ftype: str) -> Any:
    s = str(raw)
    if ftype in ("string", "text"):
        return s
    if ftype == "int":
        try:
            return int(s)
        except ValueError as e:
            raise TrellisError(f"expected an integer, got {s!r}") from e
    if ftype == "float":
        try:
            return float(s)
        except ValueError as e:
            raise TrellisError(f"expected a number, got {s!r}") from e
    if ftype == "bool":
        return s.lower() in ("1", "true", "on", "yes", "ja")
    raise TrellisError(f"unknown field type {ftype!r}")


def _form_error(request: Request, spec, db: TrellisDB, message: str) -> HTMLResponse:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for pk in spec.pflicht_kanten:
        opts: list[dict[str, Any]] = []
        for ttype in pk.ziel_typen:
            for nid, data in db.list_nodes(ttype).items():
                opts.append({
                    "ref":  f"{ttype}/{nid}",
                    "type": ttype,
                    "name": data.get("name", nid),
                })
        candidates[pk.typ] = opts
    ctx = _common_context(request)
    ctx.update({
        "selected_type": spec.name,
        "form_spec":     spec,
        "candidates":    candidates,
        "form_error":    message,
        "selected_ref":  None,
        "nodes":         [],
        "inspector":     None,
    })
    template = "_form_create.html" if _is_htmx(request) else "index.html"
    return templates.TemplateResponse(request, template, ctx, status_code=400)
