import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.core.config import Settings, get_app_settings
from app.core.database import get_connection
from app.modules.auth.dependencies import get_current_user
from app.modules.deposits.schemas import DepositCreateRequest, DepositCreateResponse
from app.modules.deposits.service import create_deposit
from app.modules.users.models import User

router = APIRouter()


def _request_ip(request: Request) -> str | None:
    """Достает IP клиента для антифрода и audit log."""
    return request.client.host if request.client else None


@router.post(
    "",
    response_model=DepositCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create deposit",
    description="Creates a pending SBP deposit transaction for a BingX UID.",
)
async def create_deposit_endpoint(
    payload: DepositCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
) -> DepositCreateResponse:
    """Создает pending СБП-пополнение для текущего пользователя."""
    deposit = await create_deposit(
        connection,
        user=user,
        amount_rub=payload.amount_rub,
        bingx_uid=payload.bingx_uid,
        ip_address=_request_ip(request),
        device_fingerprint=request.headers.get("x-device-fingerprint"),
        user_agent=request.headers.get("user-agent"),
        settings=settings,
    )
    return DepositCreateResponse(**deposit)
