"""
Trellis — HTTP route handlers.

Each handler is intentionally thin: parse → call domain via `views`/`forms`
→ render. New routes added in later etappes (vertrag editor, search,
settings, readiness) belong here as well.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

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

    # ---------------- edit (plain fields + status) ----------------

    @app.get("/edit/{type_name}/{node_id}", response_class=HTMLResponse)
    def edit_form(request: Request, type_name: str, node_id: str) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        ref = f"{type_name}/{node_id}"
        try:
            spec = db.config.get_type(type_name)
            node = db.get_node(ref)
        except ConfigError as e:
            raise HTTPException(404, str(e))
        if node is None:
            raise HTTPException(404, f"node not found: {ref}")
        ctx = _ctx(
            request,
            selected_type=type_name,
            selected_ref=ref,
            form_spec=spec,
            edit_node=node,
            edit_ref=ref,
            form_error=None,
        )
        return _render(request, "index.html", "_form_edit.html", ctx)

    @app.post("/edit/{type_name}/{node_id}")
    async def edit_submit(request: Request, type_name: str, node_id: str) -> Response:
        db: TrellisDB = request.app.state.db
        ref = f"{type_name}/{node_id}"
        try:
            spec = db.config.get_type(type_name)
        except ConfigError as e:
            raise HTTPException(404, str(e))

        form = await request.form()
        try:
            update = forms.parse_edit_form(form, spec)
            db.update_node(ref, update)
        except (forms.FormError, TrellisError) as e:
            node = db.get_node(ref) or {}
            ctx = _ctx(
                request,
                selected_type=type_name, selected_ref=ref,
                form_spec=spec, edit_node=node, edit_ref=ref,
                form_error=str(e),
            )
            template = "_form_edit.html" if _is_htmx(request) else "index.html"
            return templates.TemplateResponse(request, template, ctx, status_code=400)

        if _is_htmx(request):
            ctx = _ctx(
                request,
                selected_type=type_name, selected_ref=ref,
                inspector=views.inspector_view(db, ref),
            )
            resp = templates.TemplateResponse(request, "_inspector.html", ctx)
            resp.headers["HX-Trigger"] = f"refresh-list-{type_name}"
            return resp
        return RedirectResponse(url=f"/nodes/{ref}", status_code=303)

    # ---------------- search (mention picker) ----------------

    @app.get("/search", response_class=HTMLResponse)
    def search(
        request: Request,
        q: str = Query("", min_length=0, max_length=200),
        types: str = Query(""),
    ) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        q_clean = (q or "").strip()
        types_list = [t.strip() for t in (types or "").split(",") if t.strip()]
        if not q_clean:
            ctx = _ctx(request, hits=[], create_for=[], query="")
            return templates.TemplateResponse(request, "_mention_popup.html", ctx)
        data = views.mention_results(db, q_clean, types_list)
        ctx = _ctx(request, **data)
        return templates.TemplateResponse(request, "_mention_popup.html", ctx)

    # ---------------- inline quick-create from mention popup ----------------

    @app.post("/quick-create")
    async def quick_create(request: Request) -> JSONResponse:
        db: TrellisDB = request.app.state.db
        form = await request.form()
        type_name = (form.get("type_name") or "").strip()
        name = (form.get("name") or "").strip()
        if not type_name or not name:
            raise HTTPException(400, "type_name und name sind Pflicht")
        try:
            db.config.get_type(type_name)
            ref = db.create_node(type_name, {"name": name})
        except TrellisError as e:
            raise HTTPException(400, str(e))
        return JSONResponse({"ref": ref, "name": name})

    # ---------------- contracts (vertrag-edges) ----------------

    @app.get("/contract/new", response_class=HTMLResponse)
    def contract_new_form(request: Request, source: str = Query(...)) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        try:
            insp = views.inspector_view(db, source)
        except TrellisError as e:
            raise HTTPException(404, str(e))
        ctx = _ctx(
            request,
            inspector=insp,
            contract_form_open=True,
            known_statuses=views.collect_known_statuses(db),
            all_type_names=db.config.type_names(),
        )
        return templates.TemplateResponse(request, "_contracts_section.html", ctx)

    @app.post("/contract")
    async def contract_create(request: Request) -> Response:
        db: TrellisDB = request.app.state.db
        form = await request.form()
        try:
            parsed = forms.parse_contract_form(form)
            db.add_contract(
                source_ref=parsed["source_ref"],
                target_ref=parsed["target_ref"],
                required_status=parsed["required_status"],
                is_blocker=parsed["is_blocker"],
            )
        except (forms.FormError, TrellisError) as e:
            source = form.get("source_ref") or ""
            insp = views.inspector_view(db, source) if source else None
            ctx = _ctx(
                request,
                inspector=insp,
                contract_form_open=True,
                contract_form_error=str(e),
                known_statuses=views.collect_known_statuses(db),
                all_type_names=db.config.type_names(),
            )
            return templates.TemplateResponse(
                request, "_contracts_section.html", ctx, status_code=400
            )
        insp = views.inspector_view(db, parsed["source_ref"])
        ctx = _ctx(request, inspector=insp,
                   known_statuses=views.collect_known_statuses(db),
                   all_type_names=db.config.type_names())
        return templates.TemplateResponse(request, "_contracts_section.html", ctx)

    @app.post("/contract/{edge_id}/delete")
    async def contract_delete(request: Request, edge_id: str) -> Response:
        db: TrellisDB = request.app.state.db
        form = await request.form()
        source = form.get("source_ref") or ""
        try:
            db.remove_contract(edge_id)
        except TrellisError as e:
            raise HTTPException(400, str(e))
        if not source:
            return Response(status_code=204)
        insp = views.inspector_view(db, source)
        ctx = _ctx(request, inspector=insp,
                   known_statuses=views.collect_known_statuses(db),
                   all_type_names=db.config.type_names())
        return templates.TemplateResponse(request, "_contracts_section.html", ctx)
