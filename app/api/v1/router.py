import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.ratelimit import rate_limit
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.cabinet.router import router as cabinet_router
from app.modules.deposits.router import router as deposits_router
from app.modules.users.router import router as users_router
from app.modules.webhooks.router import router as webhooks_router

api_router = APIRouter()


@api_router.get(
    "/health",
    tags=["health"],
    summary="Liveness check",
    description="Liveness probe — does not touch the database.",
)
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get(
    "/health/ready",
    tags=["health"],
    summary="Readiness check",
    description="Readiness probe — verifies the database is reachable.",
)
async def readiness_check(request: Request) -> dict[str, str]:
    """Пингует БД с коротким таймаутом: при недоступности — 503, чтобы LB не слал трафик."""
    pool = request.app.state.db_pool
    try:
        async with asyncio.timeout(2):
            async with pool.acquire() as connection:
                await connection.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from exc
    return {"status": "ready"}


api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(users_router, prefix="/users", tags=["users"])
api_router.include_router(cabinet_router, prefix="/cabinet", tags=["cabinet"])
api_router.include_router(deposits_router, prefix="/deposits", tags=["deposits"])
api_router.include_router(
    admin_router,
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(rate_limit("admin_api", limit=60, window_seconds=60))],
)
api_router.include_router(
    webhooks_router,
    prefix="/webhooks",
    tags=["webhooks"],
    include_in_schema=False,
)
