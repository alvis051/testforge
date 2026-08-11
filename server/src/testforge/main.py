from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from testforge.api.routes import cases, health, insights, plans, projects, runs, suites
from testforge.config import Settings, get_settings
from testforge.db.session import create_session_factory
from testforge.errors import AppError, register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(title="TestForge", version="0.1.0")
    app.state.settings = resolved
    app.state.session_factory = create_session_factory(resolved.database_url)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(suites.router)
    app.include_router(cases.router)
    app.include_router(runs.router)
    app.include_router(plans.router)
    app.include_router(insights.router)

    dist = Path(resolved.frontend_dist) if resolved.frontend_dist else None
    if dist is not None and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str) -> FileResponse:
            """Serve the SPA shell for any path the API did not claim.

            Registered last, so every real API route matches first. `/api/*` is
            excluded explicitly: an unknown API path must 404 as JSON rather than
            returning HTML a client would try to parse.
            """
            if full_path.startswith("api/"):
                raise AppError("not_found", f"no such path: /{full_path}", 404)
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
