import asyncio
from collections.abc import Iterator

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

TEST_EMAILS = [
    "user@example.com",
    "blank-uid@example.com",
    "deposit@example.com",
]
TEST_BLACKLIST_VALUES = [
    "12345678",
    "99887766",
    "127.0.0.1",
    "testclient",
]


async def delete_test_users(database_url: str) -> None:
    connection = await asyncpg.connect(dsn=database_url)
    try:
        user_ids = await connection.fetch(
            "SELECT id FROM users WHERE email = ANY($1::text[])",
            TEST_EMAILS,
        )
        ids = [record["id"] for record in user_ids]
        if ids:
            transaction_ids = await connection.fetch(
                "SELECT id FROM transactions WHERE user_id = ANY($1::text[])",
                ids,
            )
            tx_ids = [record["id"] for record in transaction_ids]
            if tx_ids:
                await connection.execute(
                    "DELETE FROM audit_log WHERE transaction_id = ANY($1::text[])",
                    tx_ids,
                )
                await connection.execute(
                    "DELETE FROM fraud_alerts WHERE transaction_id = ANY($1::text[])",
                    tx_ids,
                )
            await connection.execute(
                "DELETE FROM transactions WHERE user_id = ANY($1::text[])",
                ids,
            )
        await connection.execute("DELETE FROM blacklist WHERE value = ANY($1::text[])", TEST_EMAILS)
        await connection.execute(
            "DELETE FROM blacklist WHERE value = ANY($1::text[])",
            TEST_BLACKLIST_VALUES,
        )
        await connection.execute("DELETE FROM users WHERE email = ANY($1::text[])", TEST_EMAILS)
    finally:
        await connection.close()


@pytest.fixture()
def client() -> Iterator[TestClient]:
    settings = Settings(
        app_name="MoneyWay Test API",
        environment="test",
        secret_key="test-secret-key-that-is-long-enough",
        jwt_issuer="moneyway-test",
        jwt_audience="moneyway-test-clients",
        trusted_hosts=["testserver"],
        cors_origins=[],
    )
    asyncio.run(delete_test_users(settings.database_url))

    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
