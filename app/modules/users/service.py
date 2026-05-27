from datetime import UTC, datetime
from uuid import uuid4

import asyncpg

from app.core.security import hash_password, verify_password
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import UserUpdateRequest

USER_COLUMNS = """
    id,
    email,
    password_hash,
    full_name,
    bingx_uid,
    role,
    is_active,
    is_verified,
    created_at,
    updated_at,
    last_login_at
"""


def normalize_email(email: str) -> str:
    """Приводит email к единому виду для поиска и уникальности."""
    return email.strip().lower()


def user_from_record(record: asyncpg.Record | None) -> User | None:
    """Преобразует строку asyncpg из таблицы users в доменный объект User."""
    if record is None:
        return None
    role = record["role"]
    return User(
        id=record["id"],
        email=record["email"],
        password_hash=record["password_hash"],
        full_name=record["full_name"],
        bingx_uid=record["bingx_uid"],
        role=UserRole[role] if role in UserRole.__members__ else UserRole(role),
        is_active=record["is_active"],
        is_verified=record["is_verified"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        last_login_at=record["last_login_at"],
    )


async def get_user_by_id(connection: asyncpg.Connection, user_id: str) -> User | None:
    """Ищет пользователя по внутреннему UUID в нашей базе."""
    record = await connection.fetchrow(
        f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
        user_id,
    )
    return user_from_record(record)


async def get_user_by_email(connection: asyncpg.Connection, email: str) -> User | None:
    """Ищет пользователя по нормализованному email."""
    record = await connection.fetchrow(
        f"SELECT {USER_COLUMNS} FROM users WHERE email = $1",
        normalize_email(email),
    )
    return user_from_record(record)


async def create_user(
    connection: asyncpg.Connection,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
    bingx_uid: str | None = None,
) -> User:
    """Создает пользователя с хешированным паролем и базовым role=USER."""
    now = datetime.now(UTC)
    record = await connection.fetchrow(
        f"""
        INSERT INTO users (
            id,
            email,
            password_hash,
            full_name,
            bingx_uid,
            role,
            is_active,
            is_verified,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, $6, true, false, $7, $7)
        RETURNING {USER_COLUMNS}
        """,
        str(uuid4()),
        normalize_email(email),
        hash_password(password),
        full_name,
        bingx_uid,
        "USER",
        now,
    )
    user = user_from_record(record)
    assert user is not None
    return user


async def authenticate_user(
    connection: asyncpg.Connection,
    *,
    email: str,
    password: str,
) -> User | None:
    """Проверяет email/password и обновляет last_login_at при успешном входе."""
    user = await get_user_by_email(connection, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    now = datetime.now(UTC)
    record = await connection.fetchrow(
        f"""
        UPDATE users
        SET last_login_at = $2, updated_at = $2
        WHERE id = $1
        RETURNING {USER_COLUMNS}
        """,
        user.id,
        now,
    )
    return user_from_record(record)


async def update_user_profile(
    connection: asyncpg.Connection,
    *,
    user: User,
    payload: UserUpdateRequest,
) -> User:
    """Обновляет только явно переданные поля профиля пользователя."""
    full_name = user.full_name
    bingx_uid = user.bingx_uid
    if "full_name" in payload.model_fields_set:
        full_name = payload.full_name
    if "bingx_uid" in payload.model_fields_set:
        bingx_uid = payload.bingx_uid

    record = await connection.fetchrow(
        f"""
        UPDATE users
        SET full_name = $2, bingx_uid = $3, updated_at = $4
        WHERE id = $1
        RETURNING {USER_COLUMNS}
        """,
        user.id,
        full_name,
        bingx_uid,
        datetime.now(UTC),
    )
    updated_user = user_from_record(record)
    assert updated_user is not None
    return updated_user
