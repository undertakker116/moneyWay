"""Интеграционные тесты: реальный API + Postgres + воркер, сквозные сценарии и контракты."""

import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core import ratelimit
from app.core.config import Settings
from app.main import create_app
from app.workers.bingx_transfers import process_next_bingx_transfer

WEBHOOK_SECRET = "test-webhook-secret"  # совпадает с conftest TEST_WEBHOOK_SECRET
UID = "55554444"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def webhook_headers() -> dict[str, str]:
    return {"X-Webhook-Secret": WEBHOOK_SECRET}


def register(client, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1", "full_name": "Int", "bingx_uid": UID},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_deposit(client, token: str, *, amount: str = "10000", uid: str = UID):
    return client.post(
        "/api/v1/deposits",
        headers={**auth(token), "X-Device-Fingerprint": "device-int"},
        json={"amount_rub": amount, "bingx_uid": uid},
    )


def pay(client, transaction_id: str, *, amount: str = "10000"):
    return client.post(
        "/api/v1/webhooks/sbp/payment",
        headers=webhook_headers(),
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan Ivanov",
            "payer_phone": "+79990000000",
            "amount_rub": amount,
        },
    )


class FakeBingXClient:
    async def create_internal_transfer(self, *, uid, amount_usdt, idempotency_key=None):
        return {"code": 0, "data": {"id": f"bx-{uid}"}}


async def run_worker_once() -> bool:
    connection = await asyncpg.connect(Settings().database_url)
    try:
        return await process_next_bingx_transfer(connection, client=FakeBingXClient())
    finally:
        await connection.close()


async def db_row(query: str, *args):
    connection = await asyncpg.connect(Settings().database_url)
    try:
        return await connection.fetchrow(query, *args)
    finally:
        await connection.close()


# --- сквозной денежный путь ---------------------------------------------------


def test_full_deposit_lifecycle_to_completed(client):
    tokens = register(client, "int-flow@example.com")

    deposit = create_deposit(client, tokens["access_token"])
    assert deposit.status_code == 201, deposit.text
    body = deposit.json()
    assert body["amount_usdt"] == "98.00000000"  # 10000 - 2% комиссии, / курс 100
    assert body["commission"] == "200.00"
    transaction_id = body["transaction_id"]

    assert pay(client, transaction_id).status_code == 200

    # paid → KYC + pending transfer
    state = asyncio.run(
        db_row(
            """
            SELECT t.status AS tx, bt.status AS bt, k.payer_name
            FROM transactions t
            LEFT JOIN bingx_transfers bt ON bt.transaction_id = t.id
            LEFT JOIN kyc_records k ON k.transaction_id = t.id
            WHERE t.id = $1
            """,
            transaction_id,
        )
    )
    assert state["tx"] == "paid"
    assert state["bt"] == "pending"
    assert state["payer_name"] == "Ivan Ivanov"

    # воркер отправляет USDT → completed
    assert asyncio.run(run_worker_once()) is True
    final = asyncio.run(
        db_row(
            """
            SELECT t.status AS tx, bt.status AS bt, bt.bingx_transfer_id
            FROM transactions t JOIN bingx_transfers bt ON bt.transaction_id = t.id
            WHERE t.id = $1
            """,
            transaction_id,
        )
    )
    assert final["tx"] == "completed"
    assert final["bt"] == "completed"
    assert final["bingx_transfer_id"] == f"bx-{UID}"


def test_chargeback_blacklists_and_blocks_redeposit(client):
    tokens = register(client, "int-blacklist@example.com")
    transaction_id = create_deposit(client, tokens["access_token"]).json()["transaction_id"]
    assert pay(client, transaction_id).status_code == 200

    chargeback = client.post(
        "/api/v1/webhooks/sbp/chargeback",
        headers=webhook_headers(),
        json={"transaction_id": transaction_id, "reason": "fraud"},
    )
    assert chargeback.status_code == 200

    state = asyncio.run(
        db_row(
            "SELECT t.status AS tx, bt.status AS bt FROM transactions t "
            "JOIN bingx_transfers bt ON bt.transaction_id = t.id WHERE t.id = $1",
            transaction_id,
        )
    )
    assert state["tx"] == "chargeback"
    assert state["bt"] == "cancelled"  # ещё не отправленный перевод отменён

    # повторный депозит с забаненным uid → 409
    blocked = create_deposit(client, tokens["access_token"])
    assert blocked.status_code == 409, blocked.text


# --- контракты и безопасность -------------------------------------------------


def test_security_headers_present(client):
    headers = client.get("/api/v1/health").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in headers["content-security-policy"]
    assert "max-age" in headers["strict-transport-security"]


def test_health_and_readiness(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/health/ready").json()["status"] == "ready"


def test_unknown_route_uses_error_contract(client):
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_method_not_allowed(client):
    response = client.post("/api/v1/health")
    assert response.status_code == 405
    assert "error" in response.json()


def test_protected_route_rejects_missing_and_bad_token(client):
    assert client.get("/api/v1/users/me").status_code == 401
    assert client.get("/api/v1/users/me", headers=auth("not-a-jwt")).status_code == 401


def test_deposit_validation_edges(client):
    token = register(client, "int-valid@example.com")["access_token"]

    # ниже минимума и выше максимума
    assert create_deposit(client, token, amount="1000").status_code == 400
    assert create_deposit(client, token, amount="60000").status_code == 400
    # нечисловой uid
    assert create_deposit(client, token, uid="abc123").status_code == 400
    # лишнее поле
    extra = client.post(
        "/api/v1/deposits",
        headers=auth(token),
        json={"amount_rub": "10000", "bingx_uid": UID, "evil": 1},
    )
    assert extra.status_code == 400
    # переразмерный fingerprint
    long_fp = client.post(
        "/api/v1/deposits",
        headers={**auth(token), "X-Device-Fingerprint": "x" * 200},
        json={"amount_rub": "10000", "bingx_uid": UID},
    )
    assert long_fp.status_code == 400


def test_webhook_rejects_unauthenticated(client):
    # секрет задан в тестовых настройках → без заголовка 401
    response = client.post(
        "/api/v1/webhooks/sbp/payment",
        json={
            "transaction_id": "x",
            "status": "paid",
            "payer_name": "A",
            "payer_phone": "+7",
            "amount_rub": "10000",
        },
    )
    assert response.status_code == 401


# --- rate limiting (отдельный клиент с включённым лимитом) --------------------


@pytest.fixture()
def rate_limited_client():
    settings = Settings(
        environment="test",
        secret_key="test-secret-key-that-is-long-enough",
        webhook_secret=WEBHOOK_SECRET,
        jwt_issuer="moneyway-test",
        jwt_audience="moneyway-test-clients",
        trusted_hosts=["testserver"],
        cors_origins=[],
        rate_limit_enabled=True,
    )
    ratelimit.reset()
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
    ratelimit.reset()


def test_login_rate_limit_returns_429(rate_limited_client):
    # лимит login — 10/мин на IP; 11-я попытка отбивается 429 (до проверки пароля)
    codes = [
        rate_limited_client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "whatever1A"},
        ).status_code
        for _ in range(12)
    ]
    assert 429 in codes
    assert codes[-1] == 429
