import json
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

import asyncpg

from app.core.config import Settings
from app.core.errors import BadRequestError, ConflictError
from app.modules.users.models import User


def calculate_deposit_amounts(amount_rub: Decimal, settings: Settings) -> tuple[Decimal, Decimal]:
    """Считает комиссию и USDT для локального quote до подключения реального курса."""
    commission = (amount_rub * settings.deposit_commission_percent / Decimal("100")).quantize(
        Decimal("0.01")
    )
    amount_usdt = (amount_rub / settings.rub_usdt_rate).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_DOWN,
    )
    return commission, amount_usdt


async def ensure_not_blacklisted(
    connection: asyncpg.Connection,
    *,
    ip_address: str | None,
    email: str,
    bingx_uid: str,
) -> None:
    """Проверяет IP/email/BingX UID по blacklist перед созданием пополнения."""
    values = [email.lower(), bingx_uid]
    if ip_address:
        values.append(ip_address)

    record = await connection.fetchrow(
        """
        SELECT type, value
        FROM blacklist
        WHERE value = ANY($1::text[])
        LIMIT 1
        """,
        values,
    )
    if record is not None:
        raise ConflictError("Deposit is blocked by antifraud rules")


async def create_deposit(
    connection: asyncpg.Connection,
    *,
    user: User,
    amount_rub: Decimal,
    bingx_uid: str,
    ip_address: str | None,
    device_fingerprint: str | None,
    user_agent: str | None,
    settings: Settings,
) -> dict:
    """Создает pending transaction, считает quote и пишет audit-событие."""
    if amount_rub < settings.deposit_min_rub or amount_rub > settings.deposit_max_rub:
        raise BadRequestError("Deposit amount is outside allowed limits")

    await ensure_not_blacklisted(
        connection,
        ip_address=ip_address,
        email=user.email,
        bingx_uid=bingx_uid,
    )

    now = datetime.now(UTC)
    transaction_id = str(uuid4())
    commission, amount_usdt = calculate_deposit_amounts(amount_rub, settings)

    await connection.execute(
        """
        INSERT INTO transactions (
            id,
            user_id,
            amount_rub,
            amount_usdt,
            commission,
            status,
            bingx_uid,
            payer_ip,
            device_fingerprint,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, 'pending', $6, $7, $8, $9, $9)
        """,
        transaction_id,
        user.id,
        amount_rub,
        amount_usdt,
        commission,
        bingx_uid,
        ip_address,
        device_fingerprint,
        now,
    )
    await write_audit_log(
        connection,
        event_type="deposit_created",
        user_id=user.id,
        transaction_id=transaction_id,
        payload={"amount_rub": str(amount_rub), "bingx_uid": bingx_uid},
        ip_address=ip_address,
        device_fingerprint=device_fingerprint,
        user_agent=user_agent,
    )
    return {
        "transaction_id": transaction_id,
        "status": "pending",
        "amount_rub": amount_rub,
        "amount_usdt": amount_usdt,
        "commission": commission,
        "payment_url": None,
    }


async def write_audit_log(
    connection: asyncpg.Connection,
    *,
    event_type: str,
    user_id: str | None,
    transaction_id: str | None,
    payload: dict | None = None,
    ip_address: str | None = None,
    device_fingerprint: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Пишет audit_log с JSON payload и техническими данными запроса."""
    await connection.execute(
        """
        INSERT INTO audit_log (
            id,
            event_type,
            user_id,
            transaction_id,
            payload,
            ip_address,
            device_fingerprint,
            user_agent,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
        """,
        str(uuid4()),
        event_type,
        user_id,
        transaction_id,
        json.dumps(payload) if payload else None,
        ip_address,
        device_fingerprint,
        user_agent[:512] if user_agent else None,
        datetime.now(UTC),
    )
