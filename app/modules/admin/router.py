import asyncpg
from fastapi import APIRouter, Depends, Query, status

from app.core.audit import write_audit_log
from app.core.database import get_connection
from app.core.errors import NotFoundError
from app.modules.admin.schemas import (
    BlacklistCreatedResponse,
    BlacklistCreateRequest,
    MessageResponse,
)
from app.modules.admin.service import (
    add_blacklist_entry,
    get_transaction_dossier,
    list_fraud_alerts,
    remove_blacklist_entry,
)
from app.modules.auth.dependencies import get_current_admin
from app.modules.users.models import User

router = APIRouter()


@router.get(
    "/transactions/{transaction_id}/dossier",
    summary="Transaction dossier",
    description="Full evidence bundle for a transaction (KYC, transfer, fraud alerts, audit).",
)
async def transaction_dossier(
    transaction_id: str,
    _admin: User = Depends(get_current_admin),
    connection: asyncpg.Connection = Depends(get_connection),
) -> dict:
    """Досье транзакции для оспаривания чарджбэка в банке."""
    dossier = await get_transaction_dossier(connection, transaction_id)
    if dossier is None:
        raise NotFoundError("Transaction not found")
    # Доступ к PII сам по себе аудируем (break-glass): кто и какую транзакцию смотрел.
    await write_audit_log(
        connection,
        event_type="dossier_viewed",
        user_id=_admin.id,
        transaction_id=transaction_id,
    )
    return dossier


@router.get(
    "/fraud-alerts",
    summary="List fraud alerts",
    description="Lists fraud alerts, optionally filtered by status.",
)
async def fraud_alerts(
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    _admin: User = Depends(get_current_admin),
    connection: asyncpg.Connection = Depends(get_connection),
) -> list[dict]:
    """Список антифрод-алертов на разбор."""
    return await list_fraud_alerts(connection, status=status_filter, limit=limit)


@router.post(
    "/blacklist",
    response_model=BlacklistCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add blacklist entry",
    description="Manually adds a blacklist entry (e.g. an IP after chargeback review).",
)
async def blacklist_add(
    payload: BlacklistCreateRequest,
    _admin: User = Depends(get_current_admin),
    connection: asyncpg.Connection = Depends(get_connection),
) -> BlacklistCreatedResponse:
    """Ручное добавление в blacklist (например, IP после разбора чарджбэка)."""
    entry_id = await add_blacklist_entry(
        connection,
        entry_type=payload.type,
        value=payload.value,
        reason=payload.reason,
        comment=payload.comment,
    )
    return BlacklistCreatedResponse(id=entry_id)


@router.delete(
    "/blacklist/{blacklist_id}",
    response_model=MessageResponse,
    summary="Remove blacklist entry",
    description="Removes a blacklist entry (false-positive ban).",
)
async def blacklist_remove(
    blacklist_id: str,
    _admin: User = Depends(get_current_admin),
    connection: asyncpg.Connection = Depends(get_connection),
) -> MessageResponse:
    """Снятие записи из blacklist (ложный бан)."""
    if not await remove_blacklist_entry(connection, blacklist_id):
        raise NotFoundError("Blacklist entry not found")
    return MessageResponse(detail="removed")
