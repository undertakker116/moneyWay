from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import add_exception_handlers
from app.core.middleware import add_security_headers
from app.core.openapi import install_openapi


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = await asyncpg.create_pool(
            dsn=app_settings.database_url,
            min_size=1,
            max_size=10,
        )
        app.state.db_pool = pool

        try:
            yield
        finally:
            await pool.close()

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
    add_exception_handlers(app)
    app.include_router(api_router, prefix=app_settings.api_v1_prefix)
    install_openapi(app)
    return app


app = create_app()
