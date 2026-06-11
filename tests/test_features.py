"""Тесты новых фич по ТЗ: промокоды, админ-RBAC, антифрод device_fingerprint, audit воркера."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import asyncpg

from app.core.config import Settings
from app.workers.bingx_transfers import process_next_bingx_transfer

WEBHOOK_SECRET = "test-webhook-secret"
UID = "55554444"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def register(client, email: str) -> dict:
    r = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "StrongPass1", "full_name": "X", "bingx_uid": UID},
    )
    assert r.status_code == 201, r.text
    return r.json()


def deposit(client, token, *, amount="10000", promo=None, fingerprint="dev-1"):
    body = {"amount_rub": amount, "bingx_uid": UID}
    if promo is not None:
        body["promo_code"] = promo
    return client.post(
        "/api/v1/deposits",
        headers={**auth(token), "X-Device-Fingerprint": fingerprint},
        json=body,
    )


def pay(client, transaction_id):
    return client.post(
        "/api/v1/webhooks/sbp/payment",
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        json={
            "transaction_id": transaction_id,
            "status": "paid",
            "payer_name": "Ivan",
            "payer_phone": "+79990000000",
            "amount_rub": "10000",
        },
    )


async def _exec(query: str, *args):
    connection = await asyncpg.connect(Settings().database_url)
    try:
        return await connection.execute(query, *args)
    finally:
        await connection.close()


async def _fetchval(query: str, *args):
    connection = await asyncpg.connect(Settings().database_url)
    try:
        return await connection.fetchval(query, *args)
    finally:
        await connection.close()


def insert_promo(code, discount, max_uses=None):
    asyncio.run(
        _exec(
            "INSERT INTO promo_codes (id, code, discount_percent, max_uses, used_count, "
            "is_active, created_at) VALUES ($1, $2, $3, $4, 0, true, now())",
            str(uuid4()),
            code,
            Decimal(discount),
            max_uses,
        )
    )


# --- промокоды ----------------------------------------------------------------


def test_promo_code_discounts_commission(client):
    insert_promo("PROMO50", "50")
    token = register(client, "promo@example.com")["access_token"]

    body = deposit(client, token, promo="PROMO50").json()
    # базовая комиссия 200, скидка 50% → 100; usdt = (10000-100)/100 = 99
    assert body["commission"] == "100.00"
    assert body["amount_usdt"] == "99.00000000"


def test_invalid_promo_rejected(client):
    token = register(client, "promo@example.com")["access_token"]
    assert deposit(client, token, promo="DOESNOTEXIST").status_code == 400


def test_promo_usage_limit_enforced(client):
    insert_promo("PROMOONCE", "10", max_uses=1)
    token = register(client, "promo@example.com")["access_token"]

    assert deposit(client, token, promo="PROMOONCE").status_code == 201
    # лимит исчерпан → второй депозит с тем же кодом отбит
    assert deposit(client, token, promo="PROMOONCE").status_code == 400


def test_promo_released_when_payment_fails(client):
    insert_promo("PROMOONCE", "10", max_uses=1)
    token = register(client, "promo@example.com")["access_token"]
    tx = deposit(client, token, promo="PROMOONCE").json()["transaction_id"]
    assert deposit(client, token, promo="PROMOONCE").status_code == 400  # исчерпан

    # платёж отклонён → использование промокода возвращается
    failed = client.post(
        "/api/v1/webhooks/sbp/payment",
        headers={"X-Webhook-Secret": WEBHOOK_SECRET},
        json={"transaction_id": tx, "status": "failed"},
    )
    assert failed.status_code == 200
    # снова доступен
    assert deposit(client, token, promo="PROMOONCE").status_code == 201


# --- антифрод device_fingerprint ---------------------------------------------


def test_device_fingerprint_reuse_raises_alert(client):
    token_a = register(client, "fp-a@example.com")["access_token"]
    tx_a = deposit(client, token_a, fingerprint="shared-device-xyz").json()["transaction_id"]
    assert pay(client, tx_a).status_code == 200  # доводим до paid, иначе reuse не матчится

    token_b = register(client, "fp-b@example.com")["access_token"]
    tx_b = deposit(client, token_b, fingerprint="shared-device-xyz").json()["transaction_id"]

    alert = asyncio.run(
        _fetchval(
            "SELECT alert_type FROM fraud_alerts WHERE transaction_id = $1 AND alert_type = "
            "'device_reuse'",
            tx_b,
        )
    )
    assert alert == "device_reuse"


# --- audit воркера ------------------------------------------------------------


class FakeBingXClient:
    async def create_internal_transfer(self, *, uid, amount_usdt, idempotency_key=None):
        return {"code": 0, "data": {"id": f"bx-{uid}"}}


async def _run_worker_once():
    connection = await asyncpg.connect(Settings().database_url)
    try:
        return await process_next_bingx_transfer(connection, client=FakeBingXClient())
    finally:
        await connection.close()


def test_worker_writes_transfer_completed_audit(client):
    token = register(client, "int-flow@example.com")["access_token"]
    tx = deposit(client, token).json()["transaction_id"]
    assert pay(client, tx).status_code == 200
    assert asyncio.run(_run_worker_once()) is True

    event = asyncio.run(
        _fetchval(
            "SELECT event_type FROM audit_log WHERE transaction_id = $1 AND event_type = "
            "'transfer_completed'",
            tx,
        )
    )
    assert event == "transfer_completed"


# --- админ-RBAC ---------------------------------------------------------------


def make_admin(email: str) -> None:
    asyncio.run(_exec("UPDATE users SET role = 'admin' WHERE email = $1", email))


def test_admin_endpoints_require_admin_role(client):
    token = register(client, "promo@example.com")["access_token"]
    # обычный пользователь → 403
    assert client.get("/api/v1/admin/fraud-alerts", headers=auth(token)).status_code == 403


# --- admin-панель (starlette-admin) ------------------------------------------


def admin_login(client, email: str):
    return client.post(
        "/admin/login",
        data={"username": email, "password": "StrongPass1"},
        follow_redirects=False,
    )


def test_admin_panel_requires_auth(client):
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 303
    assert "/admin/login" in response.headers["location"]


def test_admin_panel_rejects_non_admin(client):
    register(client, "promo@example.com")  # обычный пользователь
    assert admin_login(client, "promo@example.com").status_code == 400
    # сессия не выдана → всё ещё под логином
    assert client.get("/admin/", follow_redirects=False).status_code == 303


def test_admin_panel_admin_can_login_and_browse(client):
    register(client, "adminpanel@example.com")
    make_admin("adminpanel@example.com")

    login = admin_login(client, "adminpanel@example.com")
    assert login.status_code == 303  # успех → редирект в панель
    # сессия активна → панель + CRUD-вью промокодов + read-only вью транзакций доступны
    assert client.get("/admin/", follow_redirects=False).status_code == 200
    assert client.get("/admin/promo-code/list", follow_redirects=False).status_code == 200
    assert client.get("/admin/transaction/list", follow_redirects=False).status_code == 200
    assert client.get("/admin/user/list", follow_redirects=False).status_code == 200


def test_admin_read_only_and_field_exclusion(client):
    register(client, "adminpanel@example.com")
    make_admin("adminpanel@example.com")
    assert admin_login(client, "adminpanel@example.com").status_code == 303

    # Финансовые вью read-only: create/edit-роуты не доступны (серверный отказ, не форма).
    for path in ("/admin/transaction/create", "/admin/bingx-transfer/create"):
        assert client.get(path, follow_redirects=False).status_code in (403, 404, 405)

    # password_hash не светится в админке.
    assert "password_hash" not in client.get("/admin/user/list").text

    # used_count исключён из формы создания промокода (системный счётчик).
    promo_create = client.get("/admin/promo-code/create")
    assert promo_create.status_code == 200
    assert "used_count" not in promo_create.text


def test_admin_login_is_rate_limited():
    from fastapi.testclient import TestClient

    from app.core import ratelimit
    from app.main import create_app

    settings = Settings(
        environment="test",
        secret_key="test-secret-key-that-is-long-enough",
        webhook_secret=WEBHOOK_SECRET,
        jwt_issuer="moneyway-test",
        jwt_audience="moneyway-test-clients",
        trusted_hosts=["testserver"],
        cors_origins=[],
        rate_limit_enabled=True,
        admin_enabled=True,
    )
    ratelimit.reset()
    app = create_app(settings)
    with TestClient(app) as cl:
        codes = [
            cl.post(
                "/admin/login",
                data={"username": "x@example.com", "password": "nope"},
                follow_redirects=False,
            ).status_code
            for _ in range(7)
        ]
    ratelimit.reset()
    # лимит 5/мин → попытки сверх лимита отбиваются 429 (брутфорс админ-пароля закрыт)
    assert 429 in codes


def test_admin_dossier_and_blacklist(client):
    user = register(client, "int-flow@example.com")
    tx = deposit(client, user["access_token"]).json()["transaction_id"]

    admin = register(client, "admin@example.com")
    make_admin("admin@example.com")
    admin_h = auth(admin["access_token"])

    # досье
    dossier = client.get(f"/api/v1/admin/transactions/{tx}/dossier", headers=admin_h)
    assert dossier.status_code == 200, dossier.text
    assert dossier.json()["transaction"]["email"] == "int-flow@example.com"
    # доступ к PII сам аудируется
    viewed = asyncio.run(
        _fetchval(
            "SELECT event_type FROM audit_log WHERE transaction_id = $1 AND "
            "event_type = 'dossier_viewed'",
            tx,
        )
    )
    assert viewed == "dossier_viewed"

    # список алертов
    assert client.get("/api/v1/admin/fraud-alerts", headers=admin_h).status_code == 200

    # добавить/снять blacklist
    added = client.post(
        "/api/v1/admin/blacklist",
        headers=admin_h,
        json={"type": "ip", "value": "1.2.3.4", "reason": "manual"},
    )
    assert added.status_code == 201, added.text
    entry_id = added.json()["id"]
    assert client.delete(f"/api/v1/admin/blacklist/{entry_id}", headers=admin_h).status_code == 200
    # повторное удаление → 404
    assert (
        client.delete(f"/api/v1/admin/blacklist/{entry_id}", headers=admin_h).status_code == 404
    )
