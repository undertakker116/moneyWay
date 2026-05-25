from functools import lru_cache
from typing import Any

from fastapi import Request
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MoneyWay API"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "sqlite+aiosqlite:///./.local/app.db"
    auto_create_tables: bool = False

    secret_key: str = Field(default="dev-only-change-me")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "moneyway-api"
    jwt_audience: str = "moneyway-clients"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    trusted_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    cors_origins: list[str] = []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("trusted_hosts", "cors_origins", mode="before")
    @classmethod
    def parse_csv_list(cls, value: Any) -> list[str]:
        """Преобразует строку из env вида `a,b,c` в список значений."""
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        """Проверяет, запущено ли приложение в production-окружении."""
        return self.environment.lower() == "production"

    @property
    def enable_docs(self) -> bool:
        """Включает Swagger/OpenAPI только вне production."""
        return not self.is_production

    @model_validator(mode="after")
    def validate_secure_production_settings(self) -> "Settings":
        """Запрещает небезопасные production-настройки при старте приложения."""
        if self.is_production and self.secret_key == "dev-only-change-me":
            raise ValueError("SECRET_KEY must be changed in production.")
        if self.is_production and self.auto_create_tables:
            raise ValueError("AUTO_CREATE_TABLES must be false in production; use Alembic.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Читает конфигурацию из переменных окружения и `.env`, затем кеширует ее."""
    return Settings()


def get_app_settings(request: Request) -> Settings:
    """Возвращает настройки приложения из текущего FastAPI request."""
    return request.app.state.settings
