import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.core.config import Settings, get_app_settings
from app.core.database import get_connection
from app.core.errors import BadRequestError
from app.core.http import client_ip
from app.core.ratelimit import rate_limit
from app.modules.auth.dependencies import get_current_user
from app.modules.deposits.schemas import DepositCreateRequest, DepositCreateResponse
from app.modules.deposits.service import create_deposit
from app.modules.users.models import User

router = APIRouter()

# Длина колонки transactions.device_fingerprint / audit_log.device_fingerprint.
DEVICE_FINGERPRINT_MAX_LENGTH = 128


def _device_fingerprint(request: Request) -> str | None:
    """Читает fingerprint; длиннее колонки → 400 (обрезать нельзя — это ключ совпадения)."""
    value = request.headers.get("x-device-fingerprint")
    if value is None:
        return None
    if len(value) > DEVICE_FINGERPRINT_MAX_LENGTH:
        raise BadRequestError("X-Device-Fingerprint header is too long")
    return value


@router.post(
    "",
    response_model=DepositCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create deposit",
    description="Creates a pending SBP deposit transaction for a BingX UID.",
    dependencies=[Depends(rate_limit("deposits", limit=30, window_seconds=60))],
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
        ip_address=client_ip(request, settings),
        device_fingerprint=_device_fingerprint(request),
        user_agent=request.headers.get("user-agent"),
        settings=settings,
        promo_code=payload.promo_code,
    )
    return DepositCreateResponse(**deposit)
