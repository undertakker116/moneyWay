import asyncio

import asyncpg

from app.core.config import Settings
from app.workers.bingx_transfers import process_next_bingx_transfer


def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


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


async def fetch_deposit_state(transaction_id: str) -> dict:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        transaction = await connection.fetchrow(
            "SELECT status FROM transactions WHERE id = $1",
            transaction_id,
        )
        kyc = await connection.fetchrow(
            "SELECT payer_name, payer_phone FROM kyc_records WHERE transaction_id = $1",
            transaction_id,
        )
        transfer = await connection.fetchrow(
            "SELECT status, recipient_uid FROM bingx_transfers WHERE transaction_id = $1",
            transaction_id,
        )
        blacklist_count = await connection.fetchval(
            "SELECT count(*) FROM blacklist WHERE value IN ('deposit@example.com', '99887766')"
        )
    finally:
        await connection.close()

    return {
        "transaction": dict(transaction) if transaction else None,
        "kyc": dict(kyc) if kyc else None,
        "transfer": dict(transfer) if transfer else None,
        "blacklist_count": blacklist_count,
    }


def test_deposit_sbp_webhook_and_chargeback_flow(client):
    tokens = register_user(client)

    create_response = client.post(
        "/api/v1/deposits",
        headers={
            **auth_headers(tokens["access_token"]),
            "X-Device-Fingerprint": "device-1",
        },
        json={"amount_rub": "10000", "bingx_uid": "99887766"},
    )
    assert create_response.status_code == 201, create_response.text
    transaction_id = create_response.json()["transaction_id"]

    paid_response = client.post(
        "/api/v1/webhooks/sbp/payment",
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
        json={"transaction_id": transaction_id, "status": "failed"},
    )
    assert late_failed_response.status_code == 200, late_failed_response.text
    assert asyncio.run(fetch_deposit_state(transaction_id))["transaction"]["status"] == "paid"

    chargeback_response = client.post(
        "/api/v1/webhooks/sbp/chargeback",
        json={"transaction_id": transaction_id, "reason": "chargeback"},
    )
    assert chargeback_response.status_code == 200, chargeback_response.text

    chargeback_state = asyncio.run(fetch_deposit_state(transaction_id))
    assert chargeback_state["transaction"]["status"] == "chargeback"
    assert chargeback_state["blacklist_count"] >= 2


class FakeBingXClient:
    async def create_internal_transfer(self, *, uid, amount_usdt):
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
    create_response = client.post(
        "/api/v1/deposits",
        headers=auth_headers(tokens["access_token"]),
        json={"amount_rub": "10000", "bingx_uid": "99887766"},
    )
    transaction_id = create_response.json()["transaction_id"]
    client.post(
        "/api/v1/webhooks/sbp/payment",
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
        },
    )

    result = asyncio.run(process_transfer_once())

    assert result["processed"] is True
    assert result["transfer"]["status"] == "completed"
    assert result["transfer"]["transaction_status"] == "completed"
    assert result["transfer"]["bingx_transfer_id"].startswith("bingx-99887766")
