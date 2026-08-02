from fastapi import FastAPI

from testforge.api.routes import health, projects, suites
from testforge.config import Settings, get_settings
from testforge.db.session import create_session_factory
from testforge.errors import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    app = FastAPI(title="TestForge", version="0.1.0")
    app.state.settings = resolved
    app.state.session_factory = create_session_factory(resolved.database_url)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(projects.router)
    app.include_router(suites.router)
    return app
