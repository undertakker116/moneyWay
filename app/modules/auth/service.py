from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import create_jwt_token, decode_jwt_token, hash_token
from app.modules.auth.schemas import TokenPair
from app.modules.users.models import User
from app.modules.users.service import get_user_by_id


def _client_ip(ip_address: str | None) -> str | None:
    """Обрезает IP клиента до размера поля перед сохранением в refresh token."""
    if not ip_address:
        return None
    return ip_address[:64]


def _user_agent(user_agent: str | None) -> str | None:
    """Обрезает User-Agent до размера поля перед сохранением в refresh token."""
    if not user_agent:
        return None
    return user_agent[:512]


async def issue_token_pair(
    connection: asyncpg.Connection,
    *,
    user: User,
    settings: Settings,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    """Создает access JWT и refresh JWT, сохраняя хеш refresh token в базе."""
    access_token, _, _ = create_jwt_token(
        subject=user.id,
        token_type="access",
        settings=settings,
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )
    refresh_token, refresh_jti, refresh_expires_at = create_jwt_token(
        subject=user.id,
        token_type="refresh",
        settings=settings,
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )
    now = datetime.now(UTC)
    await connection.execute(
        """
        INSERT INTO refresh_tokens (
            id,
            user_id,
            jwt_id,
            token_hash,
            expires_at,
            revoked_at,
            created_by_ip,
            user_agent,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, null, $6, $7, $8, $8)
        """,
        str(uuid4()),
        user.id,
        refresh_jti,
        hash_token(refresh_token, settings),
        refresh_expires_at,
        _client_ip(ip_address),
        _user_agent(user_agent),
        now,
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def rotate_refresh_token(
    connection: asyncpg.Connection,
    *,
    refresh_token: str,
    settings: Settings,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    """Атомарно отзывает refresh token и выдает новую пару access/refresh JWT."""
    payload = decode_jwt_token(refresh_token, settings=settings, expected_type="refresh")
    token_hash = hash_token(refresh_token, settings)
    now = datetime.now(UTC)
    token_record = await connection.fetchrow(
        """
        UPDATE refresh_tokens
        SET revoked_at = $4, updated_at = $4
        WHERE jwt_id = $1
          AND token_hash = $2
          AND user_id = $3
          AND revoked_at IS NULL
          AND expires_at > $4
        RETURNING expires_at
        """,
        payload["jti"],
        token_hash,
        payload["sub"],
        now,
    )
    if token_record is None:
        raise UnauthorizedError("Refresh token is not active")

    user = await get_user_by_id(connection, payload["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User is not active")

    return await issue_token_pair(
        connection,
        user=user,
        settings=settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def revoke_refresh_token(
    connection: asyncpg.Connection,
    *,
    refresh_token: str,
    settings: Settings,
) -> None:
    """Отзывает refresh token при logout, если такой активный token найден."""
    payload = decode_jwt_token(refresh_token, settings=settings, expected_type="refresh")
    token_hash = hash_token(refresh_token, settings)
    now = datetime.now(UTC)
    await connection.execute(
        """
        UPDATE refresh_tokens
        SET revoked_at = $4, updated_at = $4
        WHERE jwt_id = $1
          AND token_hash = $2
          AND user_id = $3
          AND revoked_at IS NULL
        """,
        payload["jti"],
        token_hash,
        payload["sub"],
        now,
    )
