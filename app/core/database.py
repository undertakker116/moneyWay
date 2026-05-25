from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def create_database_engine(database_url: str):
    """Создает async SQLAlchemy engine по строке подключения из `DATABASE_URL`."""
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Создает фабрику async-сессий для работы обработчиков с базой."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Выдает DB-сессию на время одного HTTP-запроса и закрывает ее после ответа."""
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        yield session
