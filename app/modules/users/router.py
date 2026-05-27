import asyncpg
from fastapi import APIRouter, Depends

from app.core.database import get_connection
from app.modules.auth.dependencies import get_current_user
from app.modules.users.models import User
from app.modules.users.schemas import UserPublic, UserUpdateRequest
from app.modules.users.service import update_user_profile

router = APIRouter()


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get current user",
    description="Returns the public profile of the authenticated user.",
)
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    """Возвращает публичный профиль текущего авторизованного пользователя."""
    return user


@router.patch(
    "/me",
    response_model=UserPublic,
    summary="Update current user",
    description="Updates editable profile fields for the authenticated user.",
)
async def update_current_user(
    payload: UserUpdateRequest,
    user: User = Depends(get_current_user),
    connection: asyncpg.Connection = Depends(get_connection),
) -> User:
    """Обновляет редактируемые поля профиля текущего пользователя."""
    return await update_user_profile(connection, user=user, payload=payload)
