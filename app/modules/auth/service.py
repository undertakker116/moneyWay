from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.security import create_jwt_token, decode_jwt_token, hash_token
from app.modules.auth.models import RefreshToken
from app.modules.auth.schemas import TokenPair
from app.modules.users.models import User
from app.modules.users.service import get_user_by_id


def _client_ip(ip_address: str | None) -> str | None:
    """Обрезает IP до безопасной длины перед сохранением."""
    if not ip_address:
        return None
    return ip_address[:64]


def _user_agent(user_agent: str | None) -> str | None:
    """Обрезает User-Agent до длины поля в БД."""
    if not user_agent:
        return None
    return user_agent[:512]


async def issue_token_pair(
    session: AsyncSession,
    *,
    user: User,
    settings: Settings,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    """Создает access JWT и refresh JWT, сохраняя хеш refresh-токена в БД."""
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
    session.add(
        RefreshToken(
            user_id=user.id,
            jwt_id=refresh_jti,
            token_hash=hash_token(refresh_token, settings),
            expires_at=refresh_expires_at,
            created_by_ip=_client_ip(ip_address),
            user_agent=_user_agent(user_agent),
        )
    )
    await session.flush()
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    refresh_token: str,
    settings: Settings,
    ip_address: str | None,
    user_agent: str | None,
) -> TokenPair:
    """Проверяет refresh-токен, отзывает его и выдает новую JWT-пару."""
    payload = decode_jwt_token(refresh_token, settings=settings, expected_type="refresh")
    token_hash = hash_token(refresh_token, settings)
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.jwt_id == payload["jti"],
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == payload["sub"],
        )
    )
    token_record = result.scalar_one_or_none()
    if token_record is None or not token_record.is_active:
        raise UnauthorizedError("Refresh token is not active")

    user = await get_user_by_id(session, payload["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User is not active")

    token_record.revoked_at = datetime.now(UTC)
    return await issue_token_pair(
        session,
        user=user,
        settings=settings,
        ip_address=ip_address,
        user_agent=user_agent,
    )


async def revoke_refresh_token(
    session: AsyncSession,
    *,
    refresh_token: str,
    settings: Settings,
) -> None:
    """Отзывает конкретный refresh-токен, если он найден и еще активен."""
    payload = decode_jwt_token(refresh_token, settings=settings, expected_type="refresh")
    token_hash = hash_token(refresh_token, settings)
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.jwt_id == payload["jti"],
            RefreshToken.token_hash == token_hash,
            RefreshToken.user_id == payload["sub"],
        )
    )
    token_record = result.scalar_one_or_none()
    if token_record is not None and token_record.revoked_at is None:
        token_record.revoked_at = datetime.now(UTC)
        await session.flush()
