import json
from datetime import UTC, datetime

import asyncpg

from app.integrations.bingx.client import BingXAPIError, BingXClient

MAX_TRANSFER_ATTEMPTS = 3


async def process_next_bingx_transfer(
    connection: asyncpg.Connection,
    *,
    client: BingXClient,
) -> bool:
    """Берет один pending transfer из БД и выполняет его через BingX API.

    Эту функцию позже будет вызывать Celery task. Сейчас она уже фиксирует главный контракт:
    одна задача атомарно забирается из очереди, получает attempt_number, отправляется в BingX,
    затем обновляет `bingx_transfers` и связанную `transactions`.
    """
    transfer = await claim_next_transfer(connection)
    if transfer is None:
        return False

    try:
        response = await client.create_internal_transfer(
            uid=transfer["recipient_uid"],
            amount_usdt=transfer["amount_usdt"],
        )
    except BingXAPIError as exc:
        await mark_transfer_failed(connection, transfer=transfer, error_payload=exc.payload)
        return True

    transfer_id = extract_transfer_id(response)
    await mark_transfer_completed(
        connection,
        transfer=transfer,
        bingx_transfer_id=transfer_id,
        response=response,
    )
    return True


async def claim_next_transfer(connection: asyncpg.Connection) -> asyncpg.Record | None:
    """Атомарно забирает одну pending-задачу, чтобы два worker-а не отправили один перевод."""
    return await connection.fetchrow(
        """
        UPDATE bingx_transfers
        SET status = 'processing',
            attempt_number = attempt_number + 1,
            updated_at = $1
        WHERE id = (
            SELECT id
            FROM bingx_transfers
            WHERE status = 'pending'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, transaction_id, recipient_uid, amount_usdt, attempt_number
        """,
        datetime.now(UTC),
    )


async def mark_transfer_completed(
    connection: asyncpg.Connection,
    *,
    transfer: asyncpg.Record,
    bingx_transfer_id: str | None,
    response: dict,
) -> None:
    now = datetime.now(UTC)
    await connection.execute(
        """
        UPDATE bingx_transfers
        SET status = 'completed',
            bingx_transfer_id = $2,
            bingx_response = $3::jsonb,
            updated_at = $4
        WHERE id = $1
        """,
        transfer["id"],
        bingx_transfer_id,
        json.dumps(response),
        now,
    )
    await connection.execute(
        """
        UPDATE transactions
        SET status = 'completed',
            bingx_transfer_id = $2,
            completed_at = $3,
            updated_at = $3
        WHERE id = $1
        """,
        transfer["transaction_id"],
        bingx_transfer_id,
        now,
    )


async def mark_transfer_failed(
    connection: asyncpg.Connection,
    *,
    transfer: asyncpg.Record,
    error_payload: dict | str,
) -> None:
    """Возвращает задачу в retry до лимита, после лимита перевод требует ручной проверки."""
    next_status = "pending" if transfer["attempt_number"] < MAX_TRANSFER_ATTEMPTS else "error"
    await connection.execute(
        """
        UPDATE bingx_transfers
        SET status = $2,
            bingx_response = $3::jsonb,
            updated_at = $4
        WHERE id = $1
        """,
        transfer["id"],
        next_status,
        json.dumps(error_payload),
        datetime.now(UTC),
    )
    if next_status == "error":
        await connection.execute(
            """
            UPDATE transactions
            SET status = 'error',
                updated_at = $2
            WHERE id = $1
            """,
            transfer["transaction_id"],
            datetime.now(UTC),
        )


def extract_transfer_id(response: dict) -> str | None:
    data = response.get("data")
    if isinstance(data, dict):
        transfer_id = data.get("id") or data.get("transferId")
        return str(transfer_id) if transfer_id else None
    return None
