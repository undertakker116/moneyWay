from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.auth.dependencies import get_current_user
from app.modules.users.models import User
from app.modules.users.schemas import UserPublic, UserUpdateRequest
from app.modules.users.service import update_user_profile

router = APIRouter()


@router.get("/me", response_model=UserPublic)
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    """Возвращает публичные данные текущего авторизованного пользователя."""
    return user


@router.patch("/me", response_model=UserPublic)
async def update_current_user(
    payload: UserUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Обновляет профиль текущего пользователя."""
    updated_user = await update_user_profile(session, user=user, payload=payload)
    await session.commit()
    return updated_user
