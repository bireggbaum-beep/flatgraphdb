"""
Trellis — HTTP route handlers.

Each handler is intentionally thin: parse → call domain via `views`/`forms`
→ render. New routes added in later etappes (vertrag editor, search,
settings, readiness) belong here as well.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import forms, views
from .app import templates
from .config import ConfigError, NodeType
from .core import TrellisDB, TrellisError


# ---------------------------------------------------------------- helpers

def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def _ctx(request: Request, **extra: Any) -> dict[str, Any]:
    """Build a template context: defaults → common → caller's overrides."""
    db: TrellisDB = request.app.state.db
    ctx = views.empty_state()
    ctx.update(views.common(db))
    ctx["project_name"] = request.app.state.project_dir.name
    ctx.update(extra)
    return ctx


def _render(request: Request, full: str, partial: str, ctx: dict[str, Any]) -> HTMLResponse:
    """Pick partial vs full template based on the HX-Request header."""
    template = partial if _is_htmx(request) else full
    return templates.TemplateResponse(request, template, ctx)


def _form_error_response(
    request: Request,
    db: TrellisDB,
    spec: NodeType,
    message: str,
) -> HTMLResponse:
    """Re-render the create form with a non-blocking error banner."""
    ctx = _ctx(
        request,
        selected_type=spec.name,
        form_spec=spec,
        candidates=views.candidates_for_pflicht(db, spec),
        form_error=message,
    )
    template = "_form_create.html" if _is_htmx(request) else "index.html"
    return templates.TemplateResponse(request, template, ctx, status_code=400)


# ---------------------------------------------------------------- routes

def register_routes(app: FastAPI) -> None:

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", _ctx(request))

    @app.get("/types/{type_name}", response_class=HTMLResponse)
    def list_type(request: Request, type_name: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        try:
            db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))
        rows = views.list_view(db, type_name)
        ctx = _ctx(request, selected_type=type_name, nodes=rows)
        return _render(request, "index.html", "_list.html", ctx)

    @app.get("/nodes/{type_name}/{node_id}", response_class=HTMLResponse)
    def show_node(request: Request, type_name: str, node_id: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        ref = f"{type_name}/{node_id}"
        try:
            insp = views.inspector_view(db, ref)
        except TrellisError as e:
            raise HTTPException(404, str(e))
        ctx = _ctx(request, selected_type=type_name, selected_ref=ref, inspector=insp)
        return _render(request, "index.html", "_inspector.html", ctx)

    @app.get("/new/{type_name}", response_class=HTMLResponse)
    def new_form(request: Request, type_name: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        try:
            spec = db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))
        ctx = _ctx(
            request,
            selected_type=type_name,
            form_spec=spec,
            candidates=views.candidates_for_pflicht(db, spec),
        )
        return _render(request, "index.html", "_form_create.html", ctx)

    @app.post("/new/{type_name}")
    async def new_submit(request: Request, type_name: str) -> Response:
        db: TrellisDB = request.app.state.db
        try:
            spec = db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))

        form = await request.form()
        try:
            parsed = forms.parse_node_form(form, spec)
        except forms.FormError as e:
            return _form_error_response(request, db, spec, str(e))

        fields = dict(parsed["fields"])
        if parsed["current_status"]:
            fields["current_status"] = parsed["current_status"]

        try:
            ref = db.create_node(type_name, fields, parsed["pflicht_targets"])
        except TrellisError as e:
            return _form_error_response(request, db, spec, str(e))

        # Successful create → swap inspector to the new node and signal a list refresh.
        if _is_htmx(request):
            t = ref.split("/", 1)[0]
            ctx = _ctx(
                request,
                selected_type=t,
                selected_ref=ref,
                inspector=views.inspector_view(db, ref),
            )
            resp = templates.TemplateResponse(request, "_inspector.html", ctx)
            resp.headers["HX-Trigger"] = f"refresh-list-{t}"
            return resp
        return RedirectResponse(url=f"/nodes/{ref}", status_code=303)
