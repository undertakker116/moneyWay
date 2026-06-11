import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

import asyncpg

from app.core.config import Settings
from app.integrations.bingx.client import BingXAPIError, BingXConnectionError
from app.modules.deposits.service import calculate_deposit_amounts
from app.workers.bingx_transfers import process_next_bingx_transfer

# Должен совпадать с TEST_WEBHOOK_SECRET в conftest.
WEBHOOK_SECRET = "test-webhook-secret"


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def webhook_headers() -> dict[str, str]:
    return {"X-Webhook-Secret": WEBHOOK_SECRET}


def register_user(client, *, email: str = "deposit@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass1",
            "full_name": "Deposit User",
            "bingx_uid": "99887766",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_deposit(client, tokens: dict, *, amount_rub: str = "10000") -> dict:
    response = client.post(
        "/api/v1/deposits",
        headers={
            **auth_headers(tokens["access_token"]),
            "X-Device-Fingerprint": "device-1",
        },
        json={"amount_rub": amount_rub, "bingx_uid": "99887766"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def fetch_deposit_state(transaction_id: str) -> dict:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        transaction = await connection.fetchrow(
            "SELECT status FROM transactions WHERE id = $1",
            transaction_id,
        )
        kyc = await connection.fetchrow(
            "SELECT payer_name, payer_phone, amount_rub FROM kyc_records WHERE transaction_id = $1",
            transaction_id,
        )
        transfer = await connection.fetchrow(
            "SELECT status, recipient_uid FROM bingx_transfers WHERE transaction_id = $1",
            transaction_id,
        )
        blacklist_count = await connection.fetchval(
            "SELECT count(*) FROM blacklist WHERE value IN ('deposit@example.com', '99887766')"
        )
        fraud_count = await connection.fetchval(
            "SELECT count(*) FROM fraud_alerts WHERE transaction_id = $1",
            transaction_id,
        )
    finally:
        await connection.close()

    return {
        "transaction": dict(transaction) if transaction else None,
        "kyc": dict(kyc) if kyc else None,
        "transfer": dict(transfer) if transfer else None,
        "blacklist_count": blacklist_count,
        "fraud_count": fraud_count,
    }


def test_deposit_sbp_webhook_and_chargeback_flow(client):
    tokens = register_user(client)
    transaction_id = create_deposit(client, tokens)["transaction_id"]

    paid_response = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "sbp_payment_id": "sbp-1",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
            "amount_rub": "10000",
        },
    )
    assert paid_response.status_code == 200, paid_response.text

    paid_state = asyncio.run(fetch_deposit_state(transaction_id))
    assert paid_state["transaction"]["status"] == "paid"
    assert paid_state["kyc"]["payer_name"] == "Ivan Ivanov"
    assert paid_state["transfer"]["status"] == "pending"
    assert paid_state["transfer"]["recipient_uid"] == "99887766"

    late_failed_response = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={"transaction_id": transaction_id, "status": "failed"},
    )
    assert late_failed_response.status_code == 200, late_failed_response.text
    assert asyncio.run(fetch_deposit_state(transaction_id))["transaction"]["status"] == "paid"

    chargeback_response = client.post(
        "/api/v1/webhooks/sbp/chargeback",
        headers=webhook_headers(),
        json={"transaction_id": transaction_id, "reason": "chargeback"},
    )
    assert chargeback_response.status_code == 200, chargeback_response.text

    chargeback_state = asyncio.run(fetch_deposit_state(transaction_id))
    assert chargeback_state["transaction"]["status"] == "chargeback"
    assert chargeback_state["blacklist_count"] >= 2
    # Чарджбэк должен отменить ещё не отправленный перевод (#7).
    assert chargeback_state["transfer"]["status"] == "cancelled"


def test_webhook_requires_valid_secret(client):
    tokens = register_user(client)
    transaction_id = create_deposit(client, tokens)["transaction_id"]

    no_secret = client.post(
        "/api/v1/webhooks/sbp/payment",
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
        },
    )
    assert no_secret.status_code == 401, no_secret.text

    wrong_secret = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers={"X-Webhook-Secret": "nope"},
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
        },
    )
    assert wrong_secret.status_code == 401, wrong_secret.text

    # Транзакция не должна перейти в paid от неаутентифицированного вебхука.
    assert asyncio.run(fetch_deposit_state(transaction_id))["transaction"]["status"] == "pending"


def test_unknown_transaction_webhook_accepted_not_404(client):
    register_user(client)
    response = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": "00000000-0000-0000-0000-000000000000",
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
        },
    )
    assert response.status_code == 200, response.text


def test_commission_is_deducted_from_usdt(client):
    tokens = register_user(client, email="commission@example.com")
    deposit = create_deposit(client, tokens, amount_rub="10000")
    # 2% от 10000 = 200 комиссия; USDT считается от 9800 при курсе 100 => 98.
    assert Decimal(deposit["commission"]) == Decimal("200.00")
    assert Decimal(deposit["amount_usdt"]) == Decimal("98")


def test_amount_mismatch_blocks_payout(client):
    tokens = register_user(client, email="regression@example.com")
    transaction_id = create_deposit(client, tokens, amount_rub="10000")["transaction_id"]

    response = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
            "amount_rub": "9999",
        },
    )
    assert response.status_code == 200, response.text

    state = asyncio.run(fetch_deposit_state(transaction_id))
    # Несведённая сумма не двигает деньги: остаёмся pending, перевод не создан, есть алерт.
    assert state["transaction"]["status"] == "pending"
    assert state["transfer"] is None
    assert state["fraud_count"] >= 1

    alert = asyncio.run(fetch_latest_alert(transaction_id))
    assert alert["alert_type"] == "amount_mismatch"
    assert Decimal(alert["metadata"]["expected_amount_rub"]) == Decimal("10000")
    assert Decimal(alert["metadata"]["reported_amount_rub"]) == Decimal("9999")


def test_paid_webhook_requires_amount(client):
    tokens = register_user(client, email="commission@example.com")
    transaction_id = create_deposit(client, tokens, amount_rub="10000")["transaction_id"]

    # paid без amount_rub нечем сверять → 400, без выплаты.
    response = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
        },
    )
    assert response.status_code == 400, response.text

    state = asyncio.run(fetch_deposit_state(transaction_id))
    assert state["transaction"]["status"] == "pending"
    assert state["transfer"] is None


def test_repeated_paid_does_not_regress_status(client):
    tokens = register_user(client)
    transaction_id = create_deposit(client, tokens)["transaction_id"]

    paid_body = {
        "transaction_id": transaction_id,
        "status": "paid",
        "payer_name": "Ivan Ivanov",
        "payer_phone": "+79990000000",
        "amount_rub": "10000",
    }
    first = client.post("/api/v1/webhooks/sbp/payment", headers=webhook_headers(), json=paid_body)
    assert first.status_code == 200, first.text

    # Доводим перевод до completed.
    assert asyncio.run(process_transfer_once())["transfer"]["transaction_status"] == "completed"

    # Поздний дубликат paid не должен откатывать терминальный completed обратно в paid.
    duplicate = client.post(
        "/api/v1/webhooks/sbp/payment", headers=webhook_headers(), json=paid_body
    )
    assert duplicate.status_code == 200, duplicate.text
    assert asyncio.run(fetch_deposit_state(transaction_id))["transaction"]["status"] == "completed"


class FakeBingXClient:
    async def create_internal_transfer(self, *, uid, amount_usdt, idempotency_key=None):
        return {"code": 0, "data": {"transferId": f"bingx-{uid}-{amount_usdt}"}}


async def process_transfer_once() -> dict:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        processed = await process_next_bingx_transfer(connection, client=FakeBingXClient())
        transfer = await connection.fetchrow(
            """
            SELECT bt.status, bt.bingx_transfer_id, t.status AS transaction_status
            FROM bingx_transfers bt
            JOIN transactions t ON t.id = bt.transaction_id
            ORDER BY bt.created_at DESC
            LIMIT 1
            """
        )
    finally:
        await connection.close()

    return {"processed": processed, "transfer": dict(transfer)}


def test_bingx_worker_processes_pending_transfer(client):
    tokens = register_user(client)
    transaction_id = create_deposit(client, tokens)["transaction_id"]
    client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
            "amount_rub": "10000",
        },
    )

    result = asyncio.run(process_transfer_once())

    assert result["processed"] is True
    assert result["transfer"]["status"] == "completed"
    assert result["transfer"]["transaction_status"] == "completed"
    assert result["transfer"]["bingx_transfer_id"].startswith("bingx-99887766")


class RaisingBingXClient:
    def __init__(self, exc: Exception):
        self._exc = exc

    async def create_internal_transfer(self, *, uid, amount_usdt, idempotency_key=None):
        raise self._exc


async def process_transfer_with(client) -> None:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        await process_next_bingx_transfer(connection, client=client)
    finally:
        await connection.close()


def _paid_pending_transfer(client) -> str:
    tokens = register_user(client)
    transaction_id = create_deposit(client, tokens)["transaction_id"]
    client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
            "amount_rub": "10000",
        },
    )
    return transaction_id


async def fetch_audit_event(transaction_id: str, event_type: str):
    connection = await asyncpg.connect(Settings().database_url)
    try:
        return await connection.fetchval(
            "SELECT event_type FROM audit_log WHERE transaction_id = $1 AND event_type = $2",
            transaction_id,
            event_type,
        )
    finally:
        await connection.close()


async def fetch_latest_alert(transaction_id: str) -> dict | None:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        row = await connection.fetchrow(
            "SELECT alert_type, metadata FROM fraud_alerts WHERE transaction_id = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            transaction_id,
        )
    finally:
        await connection.close()
    if row is None:
        return None
    metadata = row["metadata"]
    return {"alert_type": row["alert_type"], "metadata": json.loads(metadata) if metadata else {}}


def test_worker_holds_transfer_on_insufficient_balance(client):
    transaction_id = _paid_pending_transfer(client)
    exc = BingXAPIError(code=100001, msg="insufficient balance")
    asyncio.run(process_transfer_with(RaisingBingXClient(exc)))

    state = asyncio.run(fetch_deposit_state(transaction_id))
    assert state["transfer"]["status"] == "on_hold"
    assert state["transaction"]["status"] == "paid"  # деньги клиента у нас, выплата отложена
    assert state["fraud_count"] >= 1

    alert = asyncio.run(fetch_latest_alert(transaction_id))
    assert alert["alert_type"] == "low_balance"
    assert "amount_usdt" in alert["metadata"] and "recipient_uid" in alert["metadata"]
    assert asyncio.run(fetch_audit_event(transaction_id, "transfer_on_hold")) == "transfer_on_hold"


def test_worker_fails_transfer_on_recipient_not_found(client):
    transaction_id = _paid_pending_transfer(client)
    exc = BingXAPIError(code=100400, msg="user not found")
    asyncio.run(process_transfer_with(RaisingBingXClient(exc)))

    state = asyncio.run(fetch_deposit_state(transaction_id))
    assert state["transfer"]["status"] == "failed"
    assert state["transaction"]["status"] == "failed"
    assert asyncio.run(fetch_audit_event(transaction_id, "transfer_failed")) == "transfer_failed"


def test_worker_escalates_to_error_after_retries(client):
    transaction_id = _paid_pending_transfer(client)
    raising = RaisingBingXClient(BingXConnectionError("network down"))
    for _ in range(3):  # MAX_TRANSFER_ATTEMPTS
        asyncio.run(process_transfer_with(raising))

    state = asyncio.run(fetch_deposit_state(transaction_id))
    assert state["transfer"]["status"] == "error"
    assert state["transaction"]["status"] == "error"
    assert asyncio.run(fetch_audit_event(transaction_id, "transfer_error")) == "transfer_error"


class CountingFakeBingXClient:
    def __init__(self) -> None:
        self.calls = 0

    async def create_internal_transfer(self, *, uid, amount_usdt, idempotency_key=None):
        self.calls += 1
        await asyncio.sleep(0.05)  # расширяем окно гонки
        return {"code": 0, "data": {"id": f"bx-{uid}"}}


async def _claim_and_process(shared_client) -> bool:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        async with connection.transaction():
            return await process_next_bingx_transfer(connection, client=shared_client)
    finally:
        await connection.close()


def test_concurrent_claims_send_exactly_once(client):
    # Одна pending-задача, два конкурентных воркера → SKIP LOCKED + переход pending→processing
    # должны дать ровно одну отправку (не двойная выплата).
    transaction_id = _paid_pending_transfer(client)
    shared = CountingFakeBingXClient()

    async def run_two():
        return await asyncio.gather(_claim_and_process(shared), _claim_and_process(shared))

    results = asyncio.run(run_two())

    assert shared.calls == 1  # BingX вызван ровно раз — нет двойной отправки
    assert sorted(results) == [False, True]  # один обработал, второй пропустил залоченную строку
    state = asyncio.run(fetch_deposit_state(transaction_id))
    assert state["transfer"]["status"] == "completed"
    assert state["transaction"]["status"] == "completed"


def test_calculate_deposit_amounts():
    settings = Settings(
        environment="test",
        secret_key="x" * 32,
        webhook_secret="w",
        deposit_commission_percent=Decimal("2"),
        rub_usdt_rate=Decimal("100"),
    )
    amount = Decimal("10000")
    calc = calculate_deposit_amounts
    # без промокода: комиссия 2% = 200, usdt = (10000-200)/100 = 98
    assert calc(amount, settings) == (Decimal("200.00"), Decimal("98.00000000"))
    # скидка 50% на комиссию → 100, usdt = 99
    assert calc(amount, settings, discount_percent=Decimal("50")) == (
        Decimal("100.00"),
        Decimal("99.00000000"),
    )
    # скидка 100% → комиссия 0, usdt = 100
    assert calc(amount, settings, discount_percent=Decimal("100")) == (
        Decimal("0.00"),
        Decimal("100.00000000"),
    )
    # ROUND_DOWN: usdt усекается к 8 знакам, не округляется вверх
    commission, usdt = calc(Decimal("12345"), settings)
    assert usdt == ((Decimal("12345") - commission) / Decimal("100")).quantize(
        Decimal("0.00000001"), rounding=ROUND_DOWN
    )


def test_duplicate_chargeback_is_idempotent(client):
    transaction_id = _paid_pending_transfer(client)
    body = {"transaction_id": transaction_id, "reason": "fraud"}
    first = client.post("/api/v1/webhooks/sbp/chargeback", headers=webhook_headers(), json=body)
    assert first.status_code == 200
    after_first = asyncio.run(fetch_deposit_state(transaction_id))

    second = client.post("/api/v1/webhooks/sbp/chargeback", headers=webhook_headers(), json=body)
    assert second.status_code == 200
    after_second = asyncio.run(fetch_deposit_state(transaction_id))

    # повтор не плодит fraud_alert / blacklist и не меняет статус
    assert after_second["transaction"]["status"] == "chargeback"
    assert after_second["fraud_count"] == after_first["fraud_count"]
    assert after_second["blacklist_count"] == after_first["blacklist_count"]


def test_worker_treats_duplicate_as_completed(client):
    transaction_id = _paid_pending_transfer(client)
    exc = BingXAPIError(code=100001, msg="duplicate transferClientId")
    asyncio.run(process_transfer_with(RaisingBingXClient(exc)))

    state = asyncio.run(fetch_deposit_state(transaction_id))
    assert state["transfer"]["status"] == "completed"
    assert state["transaction"]["status"] == "completed"


async def set_scheduled_at(transaction_id: str, when: datetime) -> None:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        await connection.execute(
            "UPDATE bingx_transfers SET scheduled_at = $2 WHERE transaction_id = $1",
            transaction_id,
            when,
        )
    finally:
        await connection.close()


def test_hold_delays_transfer_until_scheduled(client):
    transaction_id = _paid_pending_transfer(client)

    # Холд ещё не истёк → перевод не берётся, остаётся pending.
    asyncio.run(set_scheduled_at(transaction_id, datetime.now(UTC) + timedelta(hours=1)))
    asyncio.run(process_transfer_with(FakeBingXClient()))
    assert asyncio.run(fetch_deposit_state(transaction_id))["transfer"]["status"] == "pending"

    # Холд истёк → перевод обрабатывается.
    asyncio.run(set_scheduled_at(transaction_id, datetime.now(UTC) - timedelta(seconds=1)))
    asyncio.run(process_transfer_with(FakeBingXClient()))
    assert asyncio.run(fetch_deposit_state(transaction_id))["transfer"]["status"] == "completed"
