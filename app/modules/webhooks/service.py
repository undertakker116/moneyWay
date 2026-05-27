import json
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg

from app.core.errors import BadRequestError, NotFoundError
from app.modules.deposits.service import write_audit_log
from app.modules.webhooks.schemas import SBPChargebackWebhook, SBPPaymentWebhook


async def handle_sbp_payment_webhook(
    connection: asyncpg.Connection,
    *,
    payload: SBPPaymentWebhook,
) -> None:
    """Фиксирует результат СБП: paid создает KYC и pending-задачу BingX, failed закрывает платеж."""
    transaction = await connection.fetchrow(
        """
        SELECT id, user_id, amount_rub, amount_usdt, bingx_uid, status
        FROM transactions
        WHERE id = $1
        """,
        payload.transaction_id,
    )
    if transaction is None:
        raise NotFoundError("Transaction not found")

    if transaction["status"] == "chargeback":
        return

    if payload.status == "failed":
        if transaction["status"] != "pending":
            return
        await mark_transaction_failed(connection, payload)
        return

    if not payload.payer_name or not payload.payer_phone:
        raise BadRequestError("Paid SBP webhook must include payer data")

    now = datetime.now(UTC)
    await connection.execute(
        """
        UPDATE transactions
        SET status = 'paid',
            sbp_payment_id = COALESCE($2, sbp_payment_id),
            updated_at = $3
        WHERE id = $1
        """,
        payload.transaction_id,
        payload.sbp_payment_id,
        now,
    )
    await upsert_kyc_record(connection, payload=payload, amount_rub=transaction["amount_rub"])
    await schedule_bingx_transfer(connection, transaction=transaction)
    await write_audit_log(
        connection,
        event_type="sbp_payment_paid",
        user_id=transaction["user_id"],
        transaction_id=payload.transaction_id,
        payload=payload.model_dump(mode="json"),
    )


async def mark_transaction_failed(
    connection: asyncpg.Connection,
    payload: SBPPaymentWebhook,
) -> None:
    """Закрывает transaction как failed после отклоненной оплаты СБП."""
    await connection.execute(
        """
        UPDATE transactions
        SET status = 'failed',
            sbp_payment_id = COALESCE($2, sbp_payment_id),
            updated_at = $3,
            completed_at = $3
        WHERE id = $1
        """,
        payload.transaction_id,
        payload.sbp_payment_id,
        datetime.now(UTC),
    )
    await write_audit_log(
        connection,
        event_type="sbp_payment_failed",
        user_id=None,
        transaction_id=payload.transaction_id,
        payload=payload.model_dump(mode="json"),
    )


async def upsert_kyc_record(
    connection: asyncpg.Connection,
    *,
    payload: SBPPaymentWebhook,
    amount_rub,
) -> None:
    """Создает или обновляет KYC-запись плательщика по данным СБП webhook."""
    await connection.execute(
        """
        INSERT INTO kyc_records (
            id,
            transaction_id,
            payer_name,
            payer_phone,
            amount_rub,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (transaction_id)
        DO UPDATE SET
            payer_name = EXCLUDED.payer_name,
            payer_phone = EXCLUDED.payer_phone,
            amount_rub = EXCLUDED.amount_rub
        """,
        str(uuid4()),
        payload.transaction_id,
        payload.payer_name,
        payload.payer_phone,
        payload.amount_rub or amount_rub,
        datetime.now(UTC),
    )


async def schedule_bingx_transfer(
    connection: asyncpg.Connection,
    *,
    transaction: asyncpg.Record,
) -> None:
    """Создает idempotent pending-задачу на внутренний перевод USDT в BingX."""
    idempotency_key = f"bingx-transfer:{transaction['id']}"
    await connection.execute(
        """
        INSERT INTO bingx_transfers (
            id,
            transaction_id,
            idempotency_key,
            recipient_uid,
            amount_usdt,
            status,
            attempt_number,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, 'pending', 0, $6, $6)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        str(uuid4()),
        transaction["id"],
        idempotency_key,
        transaction["bingx_uid"],
        transaction["amount_usdt"],
        datetime.now(UTC),
    )


async def handle_chargeback_webhook(
    connection: asyncpg.Connection,
    *,
    payload: SBPChargebackWebhook,
) -> None:
    """Помечает транзакцию чарджбэком и сохраняет данные для антифрода/оспаривания."""
    transaction = await connection.fetchrow(
        """
        SELECT
            t.id,
            t.user_id,
            t.bingx_uid,
            t.payer_ip,
            t.amount_rub,
            t.amount_usdt,
            t.bingx_transfer_id,
            u.email
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.id = $1
        """,
        payload.transaction_id,
    )
    if transaction is None:
        raise NotFoundError("Transaction not found")

    now = datetime.now(UTC)
    await connection.execute(
        """
        UPDATE transactions
        SET status = 'chargeback', updated_at = $2
        WHERE id = $1
        """,
        payload.transaction_id,
        now,
    )
    await add_chargeback_blacklist(connection, transaction=transaction, reason=payload.reason)
    await connection.execute(
        """
        INSERT INTO fraud_alerts (
            id,
            user_id,
            transaction_id,
            alert_type,
            metadata,
            status,
            created_at
        )
        VALUES ($1, $2, $3, 'chargeback', $4::jsonb, 'open', $5)
        """,
        str(uuid4()),
        transaction["user_id"],
        transaction["id"],
        json.dumps({"reason": payload.reason, "bingx_uid": transaction["bingx_uid"]}),
        now,
    )
    await write_audit_log(
        connection,
        event_type="chargeback_received",
        user_id=transaction["user_id"],
        transaction_id=transaction["id"],
        payload=payload.model_dump(mode="json"),
    )


async def add_chargeback_blacklist(
    connection: asyncpg.Connection,
    *,
    transaction: asyncpg.Record,
    reason: str,
) -> None:
    """Добавляет email, BingX UID и IP из chargeback-транзакции в blacklist."""
    entries = [
        ("email", transaction["email"]),
        ("uid", transaction["bingx_uid"]),
        ("ip", transaction["payer_ip"]),
    ]
    for entry_type, value in entries:
        if not value:
            continue
        await connection.execute(
            """
            INSERT INTO blacklist (id, type, value, reason, comment, created_at)
            VALUES ($1, $2, $3, $4, 'chargeback webhook', $5)
            ON CONFLICT (type, value)
            DO UPDATE SET reason = EXCLUDED.reason, comment = EXCLUDED.comment
            """,
            str(uuid4()),
            entry_type,
            value,
            reason,
            datetime.now(UTC),
        )
