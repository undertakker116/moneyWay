import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin.setup import mount_admin
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.database import pool_server_settings
from app.core.errors import add_exception_handlers
from app.core.middleware import add_security_headers
from app.core.openapi import install_openapi
from app.workers.bingx_transfers import run_worker_forever

logger = logging.getLogger("app")


def _start_embedded_worker(settings: Settings) -> asyncio.Task | None:
    """Запускает воркер переводов фоновой задачей в этом же процессе (всё в одном контейнере)."""
    if not settings.run_embedded_worker:
        return None
    if not settings.bingx_api_key or not settings.bingx_secret_key:
        logger.warning("RUN_EMBEDDED_WORKER=true, но BingX creds не заданы — воркер не запущен")
        return None

    task = asyncio.create_task(run_worker_forever(settings))

    def _on_done(finished: asyncio.Task) -> None:
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error("Встроенный воркер аварийно завершился: %s", exc)

    task.add_done_callback(_on_done)
    logger.info("Встроенный воркер переводов запущен")
    return task


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        server_settings = pool_server_settings(app_settings)
        pool = await asyncpg.create_pool(
            dsn=app_settings.database_url,
            min_size=app_settings.db_pool_min_size,
            max_size=app_settings.db_pool_max_size,
            server_settings=server_settings,
        )
        # Отдельный пул для внеполосных записей (reuse-detection), чтобы вложенный
        # acquire не исчерпывал основной пул под нагрузкой.
        aux_pool = await asyncpg.create_pool(
            dsn=app_settings.database_url,
            min_size=1,
            max_size=app_settings.db_aux_pool_max_size,
            server_settings=server_settings,
        )
        app.state.db_pool = pool
        app.state.aux_pool = aux_pool

        worker_task = _start_embedded_worker(app_settings)

        try:
            yield
        finally:
            if worker_task is not None:
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
            await pool.close()
            await aux_pool.close()
            admin_engine = getattr(app.state, "admin_engine", None)
            if admin_engine is not None:
                await admin_engine.dispose()

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

    app.state.admin_engine = None
    if app_settings.admin_enabled:
        # Отдельный async-движок SQLAlchemy под admin (рантайм-бэкенд остаётся на asyncpg).
        admin_engine = create_async_engine(
            app_settings.database_url.replace("postgresql://", "postgresql+asyncpg://", 1),
            pool_size=app_settings.admin_engine_pool_size,
            pool_pre_ping=True,
        )
        app.state.admin_engine = admin_engine
        mount_admin(app, admin_engine, app_settings)

    return app


app = create_app()
