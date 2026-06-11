import argparse
import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import httpx

from app.core.audit import write_audit_log
from app.core.config import Settings, get_settings
from app.core.database import NOTIFY_CHANNEL_BINGX_TRANSFERS, pool_server_settings
from app.integrations.bingx.client import (
    BingXAPIError,
    BingXClient,
    BingXConnectionError,
    BingXError,
)

logger = logging.getLogger("app")

MAX_TRANSFER_ATTEMPTS = 3


async def process_next_bingx_transfer(
    connection: asyncpg.Connection,
    *,
    client: BingXClient,
    stale_seconds: int = 0,
    retry_backoff_seconds: int = 0,
) -> bool:
    """Берёт одну задачу и шлёт перевод в BingX. Возвращает False, если очередь пуста.

    Маршрутизация ответа по схеме BingX:
    - дубликат transferClientId → перевод уже принят, считаем completed (идемпотентность);
    - insufficient balance → on_hold + low_balance alert, не сжигаем ретраи (ждём пополнения);
    - получатель не найден → failed (и транзакция failed);
    - прочая ошибка / сеть → retry до лимита, затем error на ручной разбор.
    """
    transfer = await claim_next_transfer(
        connection, stale_seconds=stale_seconds, retry_backoff_seconds=retry_backoff_seconds
    )
    if transfer is None:
        return False

    try:
        response = await client.create_internal_transfer(
            uid=transfer["recipient_uid"],
            amount_usdt=transfer["amount_usdt"],
            idempotency_key=transfer["idempotency_key"],
        )
    except BingXAPIError as exc:
        await _handle_api_error(connection, transfer, exc)
        return True
    except BingXConnectionError as exc:
        logger.warning("BingX transfer %s network error: %s", transfer["id"], exc)
        await retry_or_escalate_transfer(
            connection, transfer=transfer, error_payload={"error": str(exc)}
        )
        return True

    transfer_id = extract_transfer_id(response)
    if transfer_id is None:
        # BingX принял перевод (code 0), но id нет в теле → completed без reconciliation-ручки.
        # Не ретраим/не error (деньги могли уйти) — логируем для ручной сверки.
        logger.warning(
            "BingX transfer %s accepted but response had no transfer id: %r",
            transfer["id"],
            response,
        )
    await mark_transfer_completed(
        connection,
        transfer=transfer,
        bingx_transfer_id=transfer_id,
        response=response,
    )
    return True


async def _handle_api_error(
    connection: asyncpg.Connection,
    transfer: asyncpg.Record,
    exc: BingXAPIError,
) -> None:
    """Разводит ошибку BingX по веткам схемы (см. flowchart автоматизации)."""
    payload = exc.payload if isinstance(exc.payload, dict) else {"code": exc.code, "msg": exc.msg}

    if exc.is_duplicate:
        logger.info("BingX duplicate transferClientId %s — completed", transfer["idempotency_key"])
        await mark_transfer_completed(
            connection,
            transfer=transfer,
            bingx_transfer_id=extract_transfer_id(payload),
            response=payload,
        )
        return

    if exc.is_insufficient_balance:
        logger.error("BingX low balance, transfer %s on hold", transfer["id"])
        await hold_transfer_low_balance(connection, transfer=transfer, error_payload=payload)
        return

    if exc.is_recipient_not_found:
        logger.warning("BingX recipient %s not found, transfer failed", transfer["recipient_uid"])
        await mark_transfer_terminal_failed(connection, transfer=transfer, error_payload=payload)
        return

    logger.warning("BingX transfer %s API error: %s", transfer["id"], exc.msg)
    await retry_or_escalate_transfer(connection, transfer=transfer, error_payload=payload)


async def claim_next_transfer(
    connection: asyncpg.Connection,
    *,
    stale_seconds: int = 0,
    retry_backoff_seconds: int = 0,
) -> asyncpg.Record | None:
    """Атомарно забирает одну задачу (pending или зависший processing).

    Пропускает переводы по транзакциям в chargeback/failed — их слать уже нельзя.
    Повторная попытка (attempt_number > 0) откладывается на retry_backoff_seconds, чтобы
    не молотить деградировавший BingX вплотную в одном проходе.
    """
    now = datetime.now(UTC)
    stale_cutoff = now - timedelta(seconds=stale_seconds) if stale_seconds > 0 else None
    retry_cutoff = now - timedelta(seconds=retry_backoff_seconds)
    return await connection.fetchrow(
        """
        UPDATE bingx_transfers
        SET status = 'processing',
            attempt_number = attempt_number + 1,
            updated_at = $1
        WHERE id = (
            SELECT bt.id
            FROM bingx_transfers bt
            JOIN transactions t ON t.id = bt.transaction_id
            WHERE (
                (
                    bt.status = 'pending'
                    AND bt.scheduled_at <= $1
                    AND (bt.attempt_number = 0 OR bt.updated_at <= $3)
                )
                OR (
                    $2::timestamptz IS NOT NULL
                    AND bt.status = 'processing'
                    AND bt.updated_at < $2
                )
            )
              AND t.status NOT IN ('chargeback', 'failed')
            ORDER BY bt.created_at
            FOR UPDATE OF bt SKIP LOCKED
            LIMIT 1
        )
        RETURNING id, transaction_id, recipient_uid, amount_usdt, attempt_number, idempotency_key
        """,
        now,
        stale_cutoff,
        retry_cutoff,
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
    # status='paid' guard: не перетираем параллельный chargeback завершением перевода.
    await connection.execute(
        """
        UPDATE transactions
        SET status = 'completed',
            bingx_transfer_id = $2,
            completed_at = $3,
            updated_at = $3
        WHERE id = $1 AND status = 'paid'
        """,
        transfer["transaction_id"],
        bingx_transfer_id,
        now,
    )
    await write_audit_log(
        connection,
        event_type="transfer_completed",
        user_id=None,
        transaction_id=transfer["transaction_id"],
        payload={"bingx_transfer_id": bingx_transfer_id},
    )


async def retry_or_escalate_transfer(
    connection: asyncpg.Connection,
    *,
    transfer: asyncpg.Record,
    error_payload: dict | str,
) -> None:
    """В retry (pending) до лимита попыток, после лимита — error на ручной разбор."""
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
            WHERE id = $1 AND status = 'paid'
            """,
            transfer["transaction_id"],
            datetime.now(UTC),
        )
        await write_audit_log(
            connection,
            event_type="transfer_error",
            user_id=None,
            transaction_id=transfer["transaction_id"],
            payload={"attempt_number": transfer["attempt_number"]},
        )


async def hold_transfer_low_balance(
    connection: asyncpg.Connection,
    *,
    transfer: asyncpg.Record,
    error_payload: dict,
) -> None:
    """Ставит перевод на паузу при нехватке депозита и заводит low_balance alert.

    on_hold не переотбирается воркером — админ пополняет Fund-аккаунт и возвращает в pending.
    Транзакция остаётся paid (деньги клиента получены, выплата отложена).
    """
    now = datetime.now(UTC)
    await connection.execute(
        """
        UPDATE bingx_transfers
        SET status = 'on_hold', bingx_response = $2::jsonb, updated_at = $3
        WHERE id = $1
        """,
        transfer["id"],
        json.dumps(error_payload),
        now,
    )
    await connection.execute(
        """
        INSERT INTO fraud_alerts
            (id, user_id, transaction_id, alert_type, metadata, status, created_at)
        VALUES ($1, null, $2, 'low_balance', $3::jsonb, 'open', $4)
        """,
        str(uuid4()),
        transfer["transaction_id"],
        json.dumps(
            {
                "amount_usdt": str(transfer["amount_usdt"]),
                "recipient_uid": transfer["recipient_uid"],
            }
        ),
        now,
    )
    await write_audit_log(
        connection,
        event_type="transfer_on_hold",
        user_id=None,
        transaction_id=transfer["transaction_id"],
        payload={"reason": "low_balance"},
    )


async def mark_transfer_terminal_failed(
    connection: asyncpg.Connection,
    *,
    transfer: asyncpg.Record,
    error_payload: dict,
) -> None:
    """Терминально: перевод и транзакция failed (получатель не найден — ретраи бессмысленны)."""
    now = datetime.now(UTC)
    await connection.execute(
        """
        UPDATE bingx_transfers
        SET status = 'failed', bingx_response = $2::jsonb, updated_at = $3
        WHERE id = $1
        """,
        transfer["id"],
        json.dumps(error_payload),
        now,
    )
    await connection.execute(
        """
        UPDATE transactions
        SET status = 'failed', updated_at = $2
        WHERE id = $1 AND status = 'paid'
        """,
        transfer["transaction_id"],
        now,
    )
    await write_audit_log(
        connection,
        event_type="transfer_failed",
        user_id=None,
        transaction_id=transfer["transaction_id"],
        payload={"recipient_uid": transfer["recipient_uid"]},
    )


def extract_transfer_id(response: dict) -> str | None:
    data = response.get("data")
    if isinstance(data, dict):
        transfer_id = data.get("id") or data.get("transferId")
        return str(transfer_id) if transfer_id else None
    return None


async def _process_in_transaction(
    pool: asyncpg.Pool,
    client: BingXClient,
    settings: Settings,
) -> bool:
    """Один transfer в собственной транзакции (внешний вызов + запись результата атомарны)."""
    async with pool.acquire() as connection, connection.transaction():
        return await process_next_bingx_transfer(
            connection,
            client=client,
            stale_seconds=settings.bingx_transfer_stale_seconds,
            retry_backoff_seconds=settings.bingx_retry_backoff_seconds,
        )


async def _drain(pool: asyncpg.Pool, client: BingXClient, settings: Settings) -> int:
    """Обрабатывает очередь до опустошения, возвращает число обработанных задач."""
    processed = 0
    while await _process_in_transaction(pool, client, settings):
        processed += 1
    return processed


async def _make_pool(settings: Settings) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=1,
        max_size=4,
        server_settings=pool_server_settings(settings),
    )


async def drain_bingx_transfers(settings: Settings) -> int:
    """Однократный проход (для cron/CronJob или ручного запуска). Один httpx-клиент на проход."""
    pool = await _make_pool(settings)
    try:
        async with httpx.AsyncClient(timeout=settings.bingx_http_timeout_seconds) as http:
            client = BingXClient.from_settings(settings, client=http)
            return await _drain(pool, client, settings)
    finally:
        await pool.close()


async def run_worker_forever(settings: Settings) -> None:
    """Непрерывный воркер: LISTEN/NOTIFY будит на новый перевод, поллинг ловит холд/бэкофф.

    NOTIFY даёт near-real-time выдачу; фолбэк-таймаут гарантирует, что истёкший холд,
    отложенный ретрай и пропущенные уведомления всё равно будут обработаны.
    """
    pool = await _make_pool(settings)
    wake = asyncio.Event()

    def on_notify(*_: object) -> None:
        wake.set()

    listener: asyncpg.Connection | None = None
    try:
        async with httpx.AsyncClient(timeout=settings.bingx_http_timeout_seconds) as http:
            client = BingXClient.from_settings(settings, client=http)
            while True:
                if listener is None or listener.is_closed():
                    try:
                        listener = await asyncpg.connect(dsn=settings.database_url)
                        await listener.add_listener(NOTIFY_CHANNEL_BINGX_TRANSFERS, on_notify)
                        logger.info("listening on %s", NOTIFY_CHANNEL_BINGX_TRANSFERS)
                    except (asyncpg.PostgresError, OSError) as exc:
                        logger.warning("listener connect failed: %s; retrying", exc)
                        await asyncio.sleep(settings.worker_poll_interval_seconds)
                        continue

                wake.clear()
                try:
                    await _drain(pool, client, settings)
                except Exception:  # noqa: BLE001 — единичная ошибка не должна ронять воркер
                    logger.exception("drain cycle failed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), settings.worker_poll_interval_seconds)
    finally:
        if listener is not None and not listener.is_closed():
            await listener.close()
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="BingX transfer worker")
    parser.add_argument("--once", action="store_true", help="drain the queue once and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    try:
        if args.once:
            count = asyncio.run(drain_bingx_transfers(settings))
            logger.info("Processed %d BingX transfer(s)", count)
        else:
            asyncio.run(run_worker_forever(settings))
    except BingXError as exc:
        # Чистый лог без трейсбэка при мисконфиге (нет ключей BingX) — exit 1.
        logger.error("Worker cannot start: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
