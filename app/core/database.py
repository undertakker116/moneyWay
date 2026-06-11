from collections.abc import AsyncIterator

import asyncpg
from fastapi import HTTPException, Request, status

from app.core.config import Settings

# Канал Postgres NOTIFY: продьюсер (вебхук) будит непрерывный воркер на новый перевод.
NOTIFY_CHANNEL_BINGX_TRANSFERS = "bingx_transfers"


def pool_server_settings(settings: Settings) -> dict[str, str]:
    """GUC-таймауты на уровне соединения — ставятся один раз при коннекте, а не на каждый запрос.

    Зависший запрос/блокировка не держат соединение из пула бесконечно.
    """
    server_settings: dict[str, str] = {}
    if settings.db_statement_timeout_ms > 0:
        server_settings["statement_timeout"] = str(settings.db_statement_timeout_ms)
    if settings.db_lock_timeout_ms > 0:
        server_settings["lock_timeout"] = str(settings.db_lock_timeout_ms)
    if settings.db_idle_in_tx_timeout_ms > 0:
        server_settings["idle_in_transaction_session_timeout"] = str(
            settings.db_idle_in_tx_timeout_ms
        )
    return server_settings


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    """Одно соединение из пула в транзакции на запрос: commit при успехе, rollback при ошибке.

    acquire с таймаутом: при исчерпании пула под нагрузкой быстрый 503, а не вечное ожидание.
    """
    pool: asyncpg.Pool = request.app.state.db_pool
    settings: Settings = request.app.state.settings
    try:
        connection = await pool.acquire(timeout=settings.db_pool_acquire_timeout_seconds)
    except (TimeoutError, asyncpg.TooManyConnectionsError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is busy, retry later",
        ) from exc
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    except Exception:
        await transaction.rollback()
        raise
    else:
        await transaction.commit()
    finally:
        await pool.release(connection)
