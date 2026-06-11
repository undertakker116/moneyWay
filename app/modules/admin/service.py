import json
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg

from app.core.serialization import iso_or_none


def _json(value: object) -> object:
    """asyncpg отдаёт jsonb строкой — приводим к объекту для ответа."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def get_transaction_dossier(
    connection: asyncpg.Connection, transaction_id: str
) -> dict | None:
    """Собирает полное досье транзакции для предоставления банку при споре."""
    tx = await connection.fetchrow(
        """
        SELECT t.id, t.status, t.amount_rub, t.amount_usdt, t.commission, t.bingx_uid,
               t.payer_ip, t.device_fingerprint, t.bingx_transfer_id,
               t.created_at, t.completed_at, u.email, u.id AS user_id
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        WHERE t.id = $1
        """,
        transaction_id,
    )
    if tx is None:
        return None

    kyc = await connection.fetchrow(
        "SELECT payer_name, payer_phone, amount_rub, created_at FROM kyc_records "
        "WHERE transaction_id = $1",
        transaction_id,
    )
    transfer = await connection.fetchrow(
        "SELECT status, bingx_transfer_id, amount_usdt, attempt_number, scheduled_at "
        "FROM bingx_transfers WHERE transaction_id = $1",
        transaction_id,
    )
    # LIMIT — досье ограничено сверху, чтобы один transaction_id не вытянул мегавыборку.
    alerts = await connection.fetch(
        "SELECT alert_type, status, metadata, created_at FROM fraud_alerts "
        "WHERE transaction_id = $1 ORDER BY created_at LIMIT 200",
        transaction_id,
    )
    audit = await connection.fetch(
        "SELECT event_type, ip_address, created_at FROM audit_log "
        "WHERE transaction_id = $1 ORDER BY created_at LIMIT 200",
        transaction_id,
    )

    return {
        "transaction": {
            "id": tx["id"],
            "user_id": tx["user_id"],
            "email": tx["email"],
            "status": tx["status"],
            "amount_rub": str(tx["amount_rub"]),
            "amount_usdt": str(tx["amount_usdt"]),
            "commission": str(tx["commission"]),
            "bingx_uid": tx["bingx_uid"],
            "payer_ip": tx["payer_ip"],
            "device_fingerprint": tx["device_fingerprint"],
            "bingx_transfer_id": tx["bingx_transfer_id"],
            "created_at": iso_or_none(tx["created_at"]),
            "completed_at": iso_or_none(tx["completed_at"]),
        },
        "kyc": None
        if kyc is None
        else {
            "payer_name": kyc["payer_name"],
            "payer_phone": kyc["payer_phone"],
            "amount_rub": str(kyc["amount_rub"]),
            "created_at": iso_or_none(kyc["created_at"]),
        },
        "transfer": None
        if transfer is None
        else {
            "status": transfer["status"],
            "bingx_transfer_id": transfer["bingx_transfer_id"],
            "amount_usdt": str(transfer["amount_usdt"]),
            "attempt_number": transfer["attempt_number"],
            "scheduled_at": iso_or_none(transfer["scheduled_at"]),
        },
        "fraud_alerts": [
            {
                "alert_type": row["alert_type"],
                "status": row["status"],
                "metadata": _json(row["metadata"]),
                "created_at": iso_or_none(row["created_at"]),
            }
            for row in alerts
        ],
        "audit": [
            {
                "event_type": row["event_type"],
                "ip_address": row["ip_address"],
                "created_at": iso_or_none(row["created_at"]),
            }
            for row in audit
        ],
    }


async def list_fraud_alerts(
    connection: asyncpg.Connection, *, status: str | None, limit: int
) -> list[dict]:
    """Список fraud_alerts (опционально по статусу) для ручного разбора админом."""
    rows = await connection.fetch(
        """
        SELECT id, user_id, transaction_id, alert_type, status, metadata, created_at
        FROM fraud_alerts
        WHERE ($1::text IS NULL OR status = $1)
        ORDER BY created_at DESC
        LIMIT $2
        """,
        status,
        limit,
    )
    return [
        {
            "id": row["id"],
            "user_id": row["user_id"],
            "transaction_id": row["transaction_id"],
            "alert_type": row["alert_type"],
            "status": row["status"],
            "metadata": _json(row["metadata"]),
            "created_at": iso_or_none(row["created_at"]),
        }
        for row in rows
    ]


async def add_blacklist_entry(
    connection: asyncpg.Connection,
    *,
    entry_type: str,
    value: str,
    reason: str,
    comment: str | None,
) -> str:
    """Добавляет запись в blacklist (email нормализуем в lower, как при проверке депозита)."""
    normalized = value.strip().lower() if entry_type == "email" else value.strip()
    entry_id = str(uuid4())
    await connection.execute(
        """
        INSERT INTO blacklist (id, type, value, reason, comment, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (type, value)
        DO UPDATE SET reason = EXCLUDED.reason, comment = EXCLUDED.comment
        """,
        entry_id,
        entry_type,
        normalized,
        reason,
        comment,
        datetime.now(UTC),
    )
    return entry_id


async def remove_blacklist_entry(connection: asyncpg.Connection, blacklist_id: str) -> bool:
    removed = await connection.fetchval(
        "DELETE FROM blacklist WHERE id = $1 RETURNING id", blacklist_id
    )
    return removed is not None
