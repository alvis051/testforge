from fastapi import FastAPI

from testforge.api.routes import health
from testforge.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="TestForge", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    return app
