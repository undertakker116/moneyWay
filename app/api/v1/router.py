from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.cabinet.router import router as cabinet_router
from app.modules.users.router import router as users_router

api_router = APIRouter()


@api_router.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Возвращает простой статус, чтобы проверить, что API запущено."""
    return {"status": "ok"}


api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(cabinet_router, prefix="/cabinet", tags=["cabinet"])
