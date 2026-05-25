from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path) -> Iterator[TestClient]:
    """Создает тестовый FastAPI-клиент с отдельной SQLite-базой на каждый тест."""
    settings = Settings(
        app_name="MoneyWay Test API",
        environment="test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key-that-is-long-enough",
        jwt_issuer="moneyway-test",
        jwt_audience="moneyway-test-clients",
        trusted_hosts=["testserver"],
        cors_origins=[],
        auto_create_tables=True,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
