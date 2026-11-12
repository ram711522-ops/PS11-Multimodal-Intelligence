"""FastAPI application factory and ASGI entry point."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from backend.app.config import Settings
from backend.app.database import initialize_database
from backend.app.errors import install_error_handlers
from backend.app.routes.investigations import router as investigations_router
from backend.app.routes.health import router as health_router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the application with injectable settings for isolated tests."""

    app_settings = settings or Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_database(app.state.settings.database_path)
        yield

    app = FastAPI(
        title="PS11 Investigation API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.include_router(health_router)
    app.include_router(investigations_router)
    install_error_handlers(app)
    return app


app = create_app()
