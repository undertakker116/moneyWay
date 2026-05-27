from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Request
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MoneyWay API"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql://moneyway:moneyway@localhost:5432/moneyway"

    secret_key: str = Field(default="dev-only-change-me")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "moneyway-api"
    jwt_audience: str = "moneyway-clients"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30

    deposit_min_rub: Decimal = Decimal("5000")
    deposit_max_rub: Decimal = Decimal("50000")
    deposit_commission_percent: Decimal = Decimal("2")
    rub_usdt_rate: Decimal = Decimal("100")

    bingx_api_key: str | None = None
    bingx_secret_key: str | None = None
    bingx_base_url: str = "https://open-api.bingx.com"
    webhook_secret: str | None = None

    trusted_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "testserver"]
    cors_origins: Annotated[list[str], NoDecode] = []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("trusted_hosts", "cors_origins", mode="before")
    @classmethod
    def parse_csv_list(cls, value: Any) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def validate_jwt_algorithm(cls, value: str) -> str:
        allowed_algorithms = {"HS256", "HS384", "HS512"}
        if value not in allowed_algorithms:
            raise ValueError("JWT_ALGORITHM must be one of HS256, HS384, HS512.")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def enable_docs(self) -> bool:
        return not self.is_production

    @model_validator(mode="after")
    def validate_secure_production_settings(self) -> "Settings":
        if self.is_production and self.secret_key == "dev-only-change-me":
            raise ValueError("SECRET_KEY must be changed in production.")
        if self.is_production and not self.webhook_secret:
            raise ValueError("WEBHOOK_SECRET must be set in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
