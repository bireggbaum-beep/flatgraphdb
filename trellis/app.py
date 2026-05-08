"""
Trellis — FastAPI bootstrap.

Tiny on purpose. Three responsibilities:
  * load the project (TrellisDB) and stash it on app.state
  * mount /static and configure Jinja2 templates
  * wire up the route handlers from `routes.py`

Everything else lives in dedicated modules:
  * core.py     domain (TrellisDB) — no web concerns
  * config.py   types.yaml loader + validator
  * views.py    pure view-model builders
  * forms.py    form parsing + coercion
  * routes.py   HTTP handlers
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .core import TrellisDB


PACKAGE_DIR   = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR    = PACKAGE_DIR / "static"

# Module-level so `routes.py` can import it. Jinja2Templates is stateless
# beyond its directory binding, so a single instance is enough.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(project_dir: str | Path) -> FastAPI:
    """Build a FastAPI app bound to one project directory.

    One project per server process. Switching projects = restarting.
    """
    project_dir = Path(project_dir).resolve()
    db = TrellisDB(project_dir)

    app = FastAPI(title="Trellis", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.state.db = db
    app.state.project_dir = project_dir

    # Lazy import — avoids a circular dependency: routes.py imports
    # `templates` from this module, so it must be defined before routes load.
    from .routes import register_routes
    register_routes(app)
    return app
