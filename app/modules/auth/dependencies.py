from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_app_settings
from app.core.database import get_session
from app.core.errors import UnauthorizedError
from app.core.security import decode_jwt_token
from app.modules.users.models import User
from app.modules.users.service import get_user_by_id

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> User:
    """Достает текущего пользователя из Bearer JWT и проверяет, что он активен."""
    payload = decode_jwt_token(token, settings=settings, expected_type="access")
    user = await get_user_by_id(session, payload["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User is not active")
    return user
