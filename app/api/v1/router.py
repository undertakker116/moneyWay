from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.cabinet.router import router as cabinet_router
from app.modules.deposits.router import router as deposits_router
from app.modules.users.router import router as users_router
from app.modules.webhooks.router import router as webhooks_router

api_router = APIRouter()


@api_router.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="Returns a simple service health status.",
)
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(cabinet_router, prefix="/cabinet", tags=["cabinet"])
api_router.include_router(deposits_router, prefix="/deposits", tags=["deposits"])
api_router.include_router(
    webhooks_router,
    prefix="/webhooks",
    tags=["webhooks"],
    include_in_schema=False,
)
