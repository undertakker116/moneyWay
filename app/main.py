from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

import app.db.models  # noqa: F401
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import create_database_engine, create_session_factory
from app.core.middleware import add_security_headers
from app.db.base import Base


def create_app(settings: Settings | None = None) -> FastAPI:
    """Создает FastAPI-приложение, подключает БД, middleware и API-роуты."""
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Открывает подключение к БД на старте и закрывает его при остановке."""
        engine = create_database_engine(app_settings.database_url)
        app.state.db_engine = engine
        app.state.session_factory = create_session_factory(engine)

        if app_settings.auto_create_tables:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if app_settings.enable_docs else None,
        redoc_url="/redoc" if app_settings.enable_docs else None,
        openapi_url="/openapi.json" if app_settings.enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = app_settings

    if app_settings.trusted_hosts and "*" not in app_settings.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=app_settings.trusted_hosts)

    if app_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=app_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Device-Fingerprint"],
        )

    add_security_headers(app)
    app.include_router(api_router, prefix=app_settings.api_v1_prefix)
    return app


app = create_app()
