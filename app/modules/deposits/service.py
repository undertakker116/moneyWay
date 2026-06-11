import json
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

import asyncpg

from app.core.audit import write_audit_log
from app.core.config import Settings
from app.core.errors import BadRequestError, ConflictError
from app.modules.users.models import User


def calculate_deposit_amounts(
    amount_rub: Decimal,
    settings: Settings,
    *,
    discount_percent: Decimal = Decimal("0"),
) -> tuple[Decimal, Decimal]:
    """Комиссия и USDT. Модель «из суммы»: USDT от суммы за вычетом комиссии.

    Промокод даёт скидку discount_percent на саму комиссию (как в ТЗ «Расчёт суммы USDT»).
    """
    base_commission = amount_rub * settings.deposit_commission_percent / Decimal("100")
    commission = (base_commission * (Decimal("100") - discount_percent) / Decimal("100")).quantize(
        Decimal("0.01")
    )
    net_rub = amount_rub - commission
    amount_usdt = (net_rub / settings.rub_usdt_rate).quantize(
        Decimal("0.00000001"),
        rounding=ROUND_DOWN,
    )
    return commission, amount_usdt


async def reserve_promo_code(connection: asyncpg.Connection, code: str) -> asyncpg.Record | None:
    """Атомарно резервирует одно использование промокода (инкремент под guard'ом лимита).

    UPDATE сериализует конкурентные резервы по строке: лимит max_uses не превысить.
    Возвращает (id, discount_percent) или None, если код невалиден/исчерпан/просрочен.
    """
    return await connection.fetchrow(
        """
        UPDATE promo_codes
        SET used_count = used_count + 1
        WHERE code = $1
          AND is_active
          AND (expires_at IS NULL OR expires_at > now())
          AND (max_uses IS NULL OR used_count < max_uses)
        RETURNING id, discount_percent
        """,
        code,
    )


async def release_promo_code(connection: asyncpg.Connection, transaction_id: str) -> None:
    """Возвращает использование промокода, если платёж не состоялся (failed/chargeback).

    No-op, если у транзакции нет промокода. Так лимитированный промо не выжрать
    неоплаченными/откаченными депозитами.
    """
    await connection.execute(
        """
        UPDATE promo_codes
        SET used_count = used_count - 1
        WHERE id = (SELECT promo_code_id FROM transactions WHERE id = $1)
          AND used_count > 0
        """,
        transaction_id,
    )


async def flag_device_reuse(
    connection: asyncpg.Connection,
    *,
    user: User,
    transaction_id: str,
    device_fingerprint: str,
) -> None:
    """Если тот же device_fingerprint встречался у другого аккаунта (по оплаченным/завершённым
    транзакциям) — заводим fraud_alert. Не блокируем: разбор ручной через админ-очередь."""
    other = await connection.fetchval(
        """
        SELECT user_id FROM transactions
        WHERE device_fingerprint = $1 AND user_id <> $2 AND status IN ('paid', 'completed')
        LIMIT 1
        """,
        device_fingerprint,
        user.id,
    )
    if other is None:
        return
    await connection.execute(
        """
        INSERT INTO fraud_alerts
            (id, user_id, transaction_id, alert_type, metadata, status, created_at)
        VALUES ($1, $2, $3, 'device_reuse', $4::jsonb, 'open', $5)
        """,
        str(uuid4()),
        user.id,
        transaction_id,
        json.dumps({"device_fingerprint": device_fingerprint, "other_user_id": other}),
        datetime.now(UTC),
    )


async def ensure_not_blacklisted(
    connection: asyncpg.Connection,
    *,
    ip_address: str | None,
    email: str,
    bingx_uid: str,
) -> None:
    """Проверка IP/email/UID по blacklist. Матч строго по паре (type, value)."""
    record = await connection.fetchrow(
        """
        SELECT type, value
        FROM blacklist
        WHERE (type = 'email' AND value = $1)
           OR (type = 'uid' AND value = $2)
           OR ($3::text IS NOT NULL AND type = 'ip' AND value = $3)
        LIMIT 1
        """,
        email.lower(),
        bingx_uid,
        ip_address,
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
    promo_code: str | None = None,
) -> dict:
    """Создает pending transaction, считает quote (с промокодом) и пишет audit-событие."""
    if amount_rub < settings.deposit_min_rub or amount_rub > settings.deposit_max_rub:
        raise BadRequestError("Deposit amount is outside allowed limits")

    await ensure_not_blacklisted(
        connection,
        ip_address=ip_address,
        email=user.email,
        bingx_uid=bingx_uid,
    )

    promo_code_id: str | None = None
    discount_percent = Decimal("0")
    if promo_code:
        promo = await reserve_promo_code(connection, promo_code)
        if promo is None:
            raise BadRequestError("Promo code is invalid, expired or exhausted")
        promo_code_id = promo["id"]
        discount_percent = promo["discount_percent"]

    now = datetime.now(UTC)
    transaction_id = str(uuid4())
    commission, amount_usdt = calculate_deposit_amounts(
        amount_rub, settings, discount_percent=discount_percent
    )
    if amount_usdt <= 0:
        # Защита контракта: при экзотической конфигурации курса/комиссии — 400, не 500 на CHECK.
        raise BadRequestError("Deposit amount is too low after commission")
    stored_ip = ip_address[:64] if ip_address else None
    # UID получателя может отличаться от профиля — не блокируем, фиксируем в audit.
    uid_matches_profile = user.bingx_uid is None or user.bingx_uid == bingx_uid

    await connection.execute(
        """
        INSERT INTO transactions (
            id,
            user_id,
            promo_code_id,
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
        VALUES ($1, $2, $3, $4, $5, $6, 'pending', $7, $8, $9, $10, $10)
        """,
        transaction_id,
        user.id,
        promo_code_id,
        amount_rub,
        amount_usdt,
        commission,
        bingx_uid,
        stored_ip,
        device_fingerprint,
        now,
    )
    if device_fingerprint:
        await flag_device_reuse(
            connection,
            user=user,
            transaction_id=transaction_id,
            device_fingerprint=device_fingerprint,
        )
    await write_audit_log(
        connection,
        event_type="deposit_created",
        user_id=user.id,
        transaction_id=transaction_id,
        payload={
            "amount_rub": str(amount_rub),
            "bingx_uid": bingx_uid,
            "uid_matches_profile": uid_matches_profile,
            "promo_code_id": promo_code_id,
        },
        ip_address=stored_ip,
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
