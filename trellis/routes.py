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

from . import forms, readiness, views
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
    attempt: dict[str, Any] | None = None,
) -> HTMLResponse:
    """Re-render the create form with a non-blocking error banner.

    `attempt` carries what the user typed so it survives the round-trip.
    """
    ctx = _ctx(
        request,
        selected_type=spec.name,
        form_spec=spec,
        candidates=views.candidates_for_pflicht(db, spec),
        form_error=message,
        attempt=attempt or {},
    )
    template = "_form_create.html" if _is_htmx(request) else "index.html"
    return templates.TemplateResponse(request, template, ctx, status_code=400)


def _inspector_ctx(request: Request, db: TrellisDB, ref: str, **extra: Any) -> dict[str, Any]:
    """Build the full template context needed to render the inspector.

    The inspector partial includes both the contracts section and the
    structural section, each of which needs its own auxiliary data. This
    helper centralises that gathering so individual route handlers stay
    short and consistent.
    """
    type_name, _ = ref.split("/", 1)
    return _ctx(
        request,
        selected_type=type_name,
        selected_ref=ref,
        inspector=views.inspector_view(db, ref),
        structural_state=views.structural_state(db, ref),
        known_statuses=views.collect_known_statuses(db),
        all_type_names=db.config.type_names(),
        **extra,
    )


def _with_list_refresh(ctx: dict[str, Any], db: TrellisDB, type_name: str,
                       selected_ref: str | None = None) -> dict[str, Any]:
    """Decorate an inspector / structural-section context with an OOB list
    refresh, so the middle column auto-updates after a mutation."""
    ctx["oob_list_type"]    = type_name
    ctx["oob_list_rows"]    = views.list_view(db, type_name)
    ctx["oob_selected_ref"] = selected_ref
    return ctx


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
            ctx = _inspector_ctx(request, db, ref)
        except TrellisError as e:
            raise HTTPException(404, str(e))
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
        attempt = forms.attempt_from_form(form, spec)
        try:
            parsed = forms.parse_node_form(form, spec)
        except forms.FormError as e:
            return _form_error_response(request, db, spec, str(e), attempt=attempt)

        fields = dict(parsed["fields"])
        if parsed["current_status"]:
            fields["current_status"] = parsed["current_status"]

        try:
            ref = db.create_node(type_name, fields, parsed["pflicht_targets"])
        except TrellisError as e:
            return _form_error_response(request, db, spec, str(e), attempt=attempt)

        # Successful create → swap inspector AND OOB-refresh the middle column.
        if _is_htmx(request):
            t = ref.split("/", 1)[0]
            ctx = _inspector_ctx(request, db, ref)
            _with_list_refresh(ctx, db, t, selected_ref=ref)
            return templates.TemplateResponse(request, "_inspector.html", ctx)
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
            # Preserve the user's typed values rather than re-loading the
            # saved node — otherwise a typo causes silent input loss.
            attempted_node = forms.attempt_from_form(form, spec)
            ctx = _ctx(
                request,
                selected_type=type_name, selected_ref=ref,
                form_spec=spec, edit_node=attempted_node, edit_ref=ref,
                form_error=str(e),
            )
            template = "_form_edit.html" if _is_htmx(request) else "index.html"
            return templates.TemplateResponse(request, template, ctx, status_code=400)

        if _is_htmx(request):
            ctx = _inspector_ctx(request, db, ref)
            _with_list_refresh(ctx, db, type_name, selected_ref=ref)
            return templates.TemplateResponse(request, "_inspector.html", ctx)
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
            ctx = _inspector_ctx(request, db, source, contract_form_open=True)
        except TrellisError as e:
            raise HTTPException(404, str(e))
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
            if not source:
                raise HTTPException(400, str(e))
            ctx = _inspector_ctx(
                request, db, source,
                contract_form_open=True,
                contract_form_error=str(e),
            )
            return templates.TemplateResponse(
                request, "_contracts_section.html", ctx, status_code=400
            )
        # Keep the form open after a successful add so the user can stack
        # the next contract without re-clicking "+ Neue Voraussetzung".
        # "Abbrechen" closes it explicitly.
        ctx = _inspector_ctx(request, db, parsed["source_ref"], contract_form_open=True)
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
        ctx = _inspector_ctx(request, db, source)
        return templates.TemplateResponse(request, "_contracts_section.html", ctx)

    # ---------------- structural edges (link-fields, post-hoc) ----------------

    @app.get("/structural/new", response_class=HTMLResponse)
    def structural_new_form(
        request: Request,
        source: str = Query(...),
        pk: str = Query(...),
    ) -> HTMLResponse:
        """Open the inline mention input for a single Pflicht-Kante slot."""
        db: TrellisDB = request.app.state.db
        try:
            ctx = _inspector_ctx(request, db, source, structural_form_pk=pk)
        except TrellisError as e:
            raise HTTPException(404, str(e))
        return templates.TemplateResponse(request, "_structural_section.html", ctx)

    @app.post("/structural")
    async def structural_create(request: Request) -> Response:
        db: TrellisDB = request.app.state.db
        form = await request.form()
        source = (form.get("source_ref") or "").strip()
        edge_type = (form.get("edge_type") or "").strip()
        targets = forms.multi(form, "target_ref")
        if not source or not edge_type:
            raise HTTPException(400, "source_ref und edge_type sind Pflicht")
        if not targets:
            return _structural_response(request, db, source, "Bitte ein Ziel auswählen.")
        try:
            for tref in targets:
                db.add_structural_edge(source, tref, edge_type)
        except TrellisError as e:
            return _structural_response(request, db, source, str(e), status=400)
        return _structural_response(request, db, source, None)

    @app.post("/structural/{edge_id}/delete")
    async def structural_delete(request: Request, edge_id: str) -> Response:
        db: TrellisDB = request.app.state.db
        form = await request.form()
        source = (form.get("source_ref") or "").strip()
        try:
            db.remove_structural_edge(edge_id)
        except TrellisError as e:
            raise HTTPException(400, str(e))
        if not source:
            return Response(status_code=204)
        return _structural_response(request, db, source, None)

    # ---------------- soft-delete / restore ----------------

    @app.post("/soft-delete/{type_name}/{node_id}")
    def soft_delete(request: Request, type_name: str, node_id: str) -> Response:
        db: TrellisDB = request.app.state.db
        ref = f"{type_name}/{node_id}"
        try:
            db.soft_delete_node(ref)
        except TrellisError as e:
            raise HTTPException(400, str(e))
        if _is_htmx(request):
            ctx = _ctx(request, selected_type=type_name, deleted_ref=ref)
            _with_list_refresh(ctx, db, type_name, selected_ref=None)
            return templates.TemplateResponse(
                request, "_deleted_placeholder.html", ctx,
            )
        return RedirectResponse(url=f"/types/{type_name}", status_code=303)

    @app.post("/restore/{type_name}/{node_id}")
    def restore(request: Request, type_name: str, node_id: str) -> Response:
        db: TrellisDB = request.app.state.db
        ref = f"{type_name}/{node_id}"
        try:
            db.restore_node(ref)
        except TrellisError as e:
            raise HTTPException(400, str(e))
        if _is_htmx(request):
            ctx = _inspector_ctx(request, db, ref)
            _with_list_refresh(ctx, db, type_name, selected_ref=ref)
            return templates.TemplateResponse(request, "_inspector.html", ctx)
        return RedirectResponse(url=f"/nodes/{ref}", status_code=303)

    # ---------------- readiness dashboard ----------------

    @app.get("/readiness", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        type: str = Query("Phase"),
        path: str = Query(""),
    ) -> HTMLResponse:
        db: TrellisDB = request.app.state.db
        if type not in db.config.types:
            type = next(iter(db.config.types.keys()))
        refs = [r for r in (path or "").split(",") if r and r in _all_refs_set(db)]
        columns = views.build_dashboard_columns(db, type, refs)
        ctx = _ctx(
            request,
            active_tab="readiness",
            dashboard_type=type,
            dashboard_columns=columns,
        )
        if _is_htmx(request):
            return templates.TemplateResponse(request, "_dashboard.html", ctx)
        return templates.TemplateResponse(request, "index.html", ctx)

    # ---------------- settings (read-only types.yaml) ----------------

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request) -> HTMLResponse:
        path = request.app.state.project_dir / "types.yaml"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = "(types.yaml nicht lesbar)"
        ctx = _ctx(request, active_tab="settings", settings_yaml=text)
        return templates.TemplateResponse(request, "index.html", ctx)


# ---------------------------------------------------------------- helpers (cont.)

def _all_refs_set(db: TrellisDB) -> set[str]:
    """All currently existing node refs (for path validation in /readiness)."""
    out: set[str] = set()
    for t in db.config.type_names():
        for nid in db.list_nodes(t).keys():
            out.add(f"{t}/{nid}")
    return out


def _structural_response(
    request: Request,
    db: TrellisDB,
    source_ref: str,
    error: str | None,
    status: int = 200,
) -> HTMLResponse:
    """Re-render the structural section after an add/delete (success or error).

    A successful structural mutation can flip a node's stub state, so we
    also OOB-refresh the middle column to keep its stub-dot honest.
    """
    type_name, _ = source_ref.split("/", 1)
    ctx = _inspector_ctx(request, db, source_ref, structural_error=error)
    if status == 200:
        _with_list_refresh(ctx, db, type_name, selected_ref=source_ref)
    return templates.TemplateResponse(
        request, "_structural_section.html", ctx, status_code=status,
    )
