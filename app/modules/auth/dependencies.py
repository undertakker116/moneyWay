import asyncpg
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_app_settings
from app.core.database import get_connection
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_jwt_token
from app.modules.users.models import User, UserRole
from app.modules.users.service import get_user_by_id

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Проверяет Bearer access JWT и возвращает активного пользователя из базы."""
    if credentials is None:
        raise UnauthorizedError()

    token = credentials.credentials
    payload = decode_jwt_token(token, settings=settings, expected_type="access")
    user = await get_user_by_id(connection, payload["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User is not active")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Пускает только пользователей с ролью admin (RBAC)."""
    if user.role != UserRole.ADMIN:
        raise ForbiddenError("Admin access required")
    return user
