from fastapi import APIRouter, Depends

from app.modules.auth.dependencies import get_current_user
from app.modules.users.models import User
from app.modules.users.schemas import UserPublic

router = APIRouter()


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get cabinet profile",
    description="Returns the authenticated user's profile for the cabinet area.",
)
async def read_cabinet_dashboard(
    user: User = Depends(get_current_user),
) -> User:
    """Возвращает профиль текущего пользователя для личного кабинета."""
    return user
