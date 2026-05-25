from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.modules.users.models import User
from app.modules.users.schemas import UserUpdateRequest


def normalize_email(email: str) -> str:
    """Приводит email к единому виду для поиска и уникальности."""
    return email.strip().lower()


async def get_user_by_id(session: AsyncSession, user_id: str) -> User | None:
    """Ищет пользователя по внутреннему UUID."""
    return await session.get(User, user_id)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Ищет пользователя по email без учета регистра."""
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Создает пользователя с хешированным паролем."""
    user = User(
        email=normalize_email(email),
        password_hash=hash_password(password),
        full_name=full_name,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate_user(session: AsyncSession, *, email: str, password: str) -> User | None:
    """Проверяет логин и пароль; при успехе обновляет время последнего входа."""
    user = await get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(UTC)
    return user


async def update_user_profile(
    session: AsyncSession,
    *,
    user: User,
    payload: UserUpdateRequest,
) -> User:
    """Применяет разрешенные изменения профиля к текущему пользователю."""
    if payload.full_name is not None:
        user.full_name = payload.full_name
    await session.flush()
    return user
