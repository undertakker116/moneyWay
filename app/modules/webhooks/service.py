import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import asyncpg

from app.core.audit import write_audit_log
from app.core.database import NOTIFY_CHANNEL_BINGX_TRANSFERS
from app.core.errors import BadRequestError
from app.core.serialization import iso_or_none, str_or_none
from app.modules.deposits.service import release_promo_code
from app.modules.webhooks.schemas import SBPChargebackWebhook, SBPPaymentWebhook

# Статусы, при которых RUB-платёж уже состоялся и чарджбэк по нему имеет смысл.
CHARGEBACK_ELIGIBLE_STATUSES = {"paid", "completed", "error"}


async def handle_sbp_payment_webhook(
    connection: asyncpg.Connection,
    *,
    payload: SBPPaymentWebhook,
    hold_minutes: int = 0,
) -> None:
    """Фиксирует результат СБП: paid создает KYC и pending-задачу BingX, failed закрывает платеж."""
    transaction = await connection.fetchrow(
        """
        SELECT id, user_id, amount_rub, amount_usdt, bingx_uid, status
        FROM transactions
        WHERE id = $1
        FOR UPDATE
        """,
        payload.transaction_id,
    )
    if transaction is None:
        # Неизвестный id: 404 спровоцировал бы retry-штормы. Логируем и тихо принимаем.
        await write_audit_log(
            connection,
            event_type="sbp_payment_unmatched",
            user_id=None,
            transaction_id=None,
            payload={"requested_transaction_id": payload.transaction_id},
        )
        return

    if transaction["status"] == "chargeback":
        return

    if payload.status == "failed":
        if transaction["status"] != "pending":
            return
        await mark_transaction_failed(connection, payload)
        return

    # paid только из pending. Дубликат/поздний paid по терминальной транзакции игнорируем.
    if transaction["status"] != "pending":
        return

    if not payload.payer_name or not payload.payer_phone:
        raise BadRequestError("Paid SBP webhook must include payer data")

    # Сумма обязательна на paid: без неё нечего сверять — нельзя выпускать выплату.
    if payload.amount_rub is None:
        raise BadRequestError("Paid SBP webhook must include amount_rub")

    # Сверка суммы: источник истины — транзакция. Расхождение → fraud_alert, без выплаты.
    if payload.amount_rub != transaction["amount_rub"]:
        await record_amount_mismatch(connection, transaction=transaction, payload=payload)
        return

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
    # KYC пишем по доверенной сумме транзакции, а не по сумме из вебхука.
    await upsert_kyc_record(connection, payload=payload, amount_rub=transaction["amount_rub"])
    await schedule_bingx_transfer(connection, transaction=transaction, hold_minutes=hold_minutes)
    await write_audit_log(
        connection,
        event_type="sbp_payment_paid",
        user_id=transaction["user_id"],
        transaction_id=payload.transaction_id,
        payload=payload.model_dump(mode="json"),
    )


async def record_amount_mismatch(
    connection: asyncpg.Connection,
    *,
    transaction: asyncpg.Record,
    payload: SBPPaymentWebhook,
) -> None:
    """Заводит fraud_alert при расхождении оплаченной и ожидаемой суммы."""
    now = datetime.now(UTC)
    await connection.execute(
        """
        INSERT INTO fraud_alerts (
            id, user_id, transaction_id, alert_type, metadata, status, created_at
        )
        VALUES ($1, $2, $3, 'amount_mismatch', $4::jsonb, 'open', $5)
        """,
        str(uuid4()),
        transaction["user_id"],
        transaction["id"],
        json.dumps(
            {
                "expected_amount_rub": str(transaction["amount_rub"]),
                "reported_amount_rub": str(payload.amount_rub),
            }
        ),
        now,
    )
    await write_audit_log(
        connection,
        event_type="sbp_payment_amount_mismatch",
        user_id=transaction["user_id"],
        transaction_id=transaction["id"],
        payload={
            "expected_amount_rub": str(transaction["amount_rub"]),
            "reported_amount_rub": str(payload.amount_rub),
        },
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
    await release_promo_code(connection, payload.transaction_id)
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
    amount_rub: Decimal,
) -> None:
    """KYC-запись плательщика. Сумма — из транзакции, не из тела вебхука."""
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
        amount_rub,
        datetime.now(UTC),
    )


async def schedule_bingx_transfer(
    connection: asyncpg.Connection,
    *,
    transaction: asyncpg.Record,
    hold_minutes: int = 0,
) -> None:
    """Создает idempotent pending-задачу на перевод USDT с холдом против чарджбэка.

    scheduled_at = now + hold: воркер не возьмёт перевод раньше окончания холда.
    NOTIFY будит непрерывный воркер сразу после коммита (low-latency без поллинга).
    """
    now = datetime.now(UTC)
    scheduled_at = now + timedelta(minutes=hold_minutes)
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
            scheduled_at,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, $4, $5, 'pending', 0, $6, $7, $7)
        ON CONFLICT (idempotency_key) DO NOTHING
        """,
        str(uuid4()),
        transaction["id"],
        idempotency_key,
        transaction["bingx_uid"],
        transaction["amount_usdt"],
        scheduled_at,
        now,
    )
    # Транзакционный NOTIFY: доставится получателю при commit запроса.
    await connection.execute(f"NOTIFY {NOTIFY_CHANNEL_BINGX_TRANSFERS}")


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
            t.device_fingerprint,
            t.amount_rub,
            t.amount_usdt,
            t.sbp_payment_id,
            t.bingx_transfer_id,
            t.status,
            t.created_at,
            t.completed_at,
            u.email,
            k.payer_name,
            k.payer_phone
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN kyc_records k ON k.transaction_id = t.id
        WHERE t.id = $1
        FOR UPDATE OF t
        """,
        payload.transaction_id,
    )
    if transaction is None:
        await write_audit_log(
            connection,
            event_type="chargeback_unmatched",
            user_id=None,
            transaction_id=None,
            payload={"requested_transaction_id": payload.transaction_id, "reason": payload.reason},
        )
        return

    # Чарджбэк только если RUB-платёж реально прошёл; по дубликату/pending/failed — ничего.
    if transaction["status"] == "chargeback":
        return
    if transaction["status"] not in CHARGEBACK_ELIGIBLE_STATUSES:
        await write_audit_log(
            connection,
            event_type="chargeback_ineligible",
            user_id=transaction["user_id"],
            transaction_id=transaction["id"],
            payload={"status": transaction["status"], "reason": payload.reason},
        )
        return

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
    # Останавливаем ещё не отправленный перевод: иначе USDT уходит по оспоренному платежу.
    cancel_result = await connection.execute(
        """
        UPDATE bingx_transfers
        SET status = 'cancelled', updated_at = $2
        WHERE transaction_id = $1 AND status IN ('pending', 'processing')
        """,
        payload.transaction_id,
        now,
    )
    cancelled_count = _affected_rows(cancel_result)
    await release_promo_code(connection, transaction["id"])
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
        json.dumps(_chargeback_dossier(transaction, reason=payload.reason)),
        now,
    )
    await write_audit_log(
        connection,
        event_type="chargeback_received",
        user_id=transaction["user_id"],
        transaction_id=transaction["id"],
        payload={**payload.model_dump(mode="json"), "cancelled_transfers": cancelled_count},
    )


def _chargeback_dossier(transaction: asyncpg.Record, *, reason: str) -> dict:
    """Собирает единое досье чарджбэка для оспаривания в банке."""
    return {
        "reason": reason,
        "email": transaction["email"],
        "bingx_uid": transaction["bingx_uid"],
        "payer_ip": transaction["payer_ip"],
        "device_fingerprint": transaction["device_fingerprint"],
        "payer_name": transaction["payer_name"],
        "payer_phone": transaction["payer_phone"],
        "amount_rub": str_or_none(transaction["amount_rub"]),
        "amount_usdt": str_or_none(transaction["amount_usdt"]),
        "sbp_payment_id": transaction["sbp_payment_id"],
        "bingx_transfer_id": transaction["bingx_transfer_id"],
        "created_at": iso_or_none(transaction["created_at"]),
        "completed_at": iso_or_none(transaction["completed_at"]),
    }


def _affected_rows(command_tag: str) -> int:
    """Достаёт число затронутых строк из тега asyncpg (например, 'UPDATE 3')."""
    parts = command_tag.split()
    return int(parts[-1]) if parts and parts[-1].isdigit() else 0


async def add_chargeback_blacklist(
    connection: asyncpg.Connection,
    *,
    transaction: asyncpg.Record,
    reason: str,
) -> None:
    """В blacklist только email и UID. IP не автобаним (CGNAT) — он в досье fraud_alert."""
    entries = [
        ("email", transaction["email"]),
        ("uid", transaction["bingx_uid"]),
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
