from collections.abc import AsyncIterator

import asyncpg
from fastapi import Request


async def get_connection(request: Request) -> AsyncIterator[asyncpg.Connection]:
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as connection:
        transaction = connection.transaction()
        await transaction.start()
        try:
            yield connection
        except Exception:
            await transaction.rollback()
            raise
        else:
            await transaction.commit()
