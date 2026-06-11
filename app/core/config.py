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
    # Размер пула на инстанс. Под нагрузку: max_size * число инстансов < Postgres max_connections.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    # Отдельный маленький пул для внеполосных записей (reuse-detection), чтобы они не
    # конкурировали с основным пулом и не вызывали его исчерпание.
    db_aux_pool_max_size: int = 4
    # Таймаут ожидания соединения из пула (сек): при исчерпании — 503, а не зависание.
    db_pool_acquire_timeout_seconds: float = 5.0

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
    # recvWindow для запросов BingX (мс), макс 60000 — окно валидности против replay.
    bingx_recv_window_ms: int = 5000
    bingx_http_timeout_seconds: float = 10.0
    webhook_secret: str | None = None

    # Допуск на рассинхрон часов при валидации JWT (сек).
    jwt_leeway_seconds: int = 30

    # Таймауты транзакции запроса (мс), 0 = выкл.
    db_statement_timeout_ms: int = 10000
    db_lock_timeout_ms: int = 5000
    db_idle_in_tx_timeout_ms: int = 10000

    # Порог зависшего processing-перевода для reaper (сек).
    bingx_transfer_stale_seconds: int = 300
    # Задержка перед повтором перевода (сек) — защита от retry-storm на деградации BingX.
    bingx_retry_backoff_seconds: int = 30
    # Холд перед отправкой USDT (мин) — окно на отмену по чарджбэку. 0 = без холда.
    bingx_transfer_hold_minutes: int = 0
    # Фолбэк-поллинг непрерывного воркера (сек): ловит истёкший холд/бэкофф и пропущенные NOTIFY.
    worker_poll_interval_seconds: float = 5.0
    # Запускать воркер переводов внутри API-процесса (всё в одном контейнере).
    # Несколько реплик безопасны: claim идёт через FOR UPDATE SKIP LOCKED.
    run_embedded_worker: bool = False

    # Грубый rate limit; прод дублирует на gateway/WAF.
    rate_limit_enabled: bool = True
    # За reverse-proxy брать реальный IP клиента из X-Real-IP (прокси перезаписывает заголовок
    # реальным socket-адресом). Включать ТОЛЬКО когда перед сервисом есть доверенный прокси.
    trusted_proxy: bool = False
    # Размер пула SQLAlchemy-движка админки (отдельно от db_aux_pool_max_size).
    admin_engine_pool_size: int = 3
    # Монтировать admin-панель (starlette-admin) на /admin. По умолчанию ВЫКЛ: панель —
    # мощная поверхность (CRUD по таблицам), включать осознанно + ограничить доступ
    # (VPN/IP-allowlist/reverse-proxy). Логин дополнительно throttled, cookie SameSite=strict.
    admin_enabled: bool = False
    # Отдельный секрет для admin-session cookie (key separation от JWT SECRET_KEY).
    # Если не задан — используется SECRET_KEY (допустимо для dev/local).
    admin_session_secret: str | None = None

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

    @field_validator("rub_usdt_rate")
    @classmethod
    def validate_rate(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("RUB_USDT_RATE must be positive.")
        return value

    @field_validator("deposit_commission_percent")
    @classmethod
    def validate_commission(cls, value: Decimal) -> Decimal:
        if not (0 <= value < 100):
            raise ValueError("DEPOSIT_COMMISSION_PERCENT must be in [0, 100).")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def enable_docs(self) -> bool:
        return not self.is_production

    @property
    def is_insecure_env(self) -> bool:
        """local/test — где допустимы dev-дефолты секретов."""
        return self.environment.lower() in {"local", "test"}

    @model_validator(mode="after")
    def validate_secure_production_settings(self) -> "Settings":
        # Секреты обязательны везде, кроме local/test.
        if not self.is_insecure_env:
            if self.secret_key == "dev-only-change-me" or len(self.secret_key) < 32:
                raise ValueError("SECRET_KEY must be a long random value outside local/test.")
            if not self.webhook_secret:
                raise ValueError("WEBHOOK_SECRET must be set outside local/test.")
            # Если админка включена в проде — отдельный секрет сессии обязателен (key separation).
            if self.admin_enabled and not self.admin_session_secret:
                raise ValueError("ADMIN_SESSION_SECRET must be set when admin panel is enabled.")
        if self.deposit_min_rub <= 0 or self.deposit_min_rub > self.deposit_max_rub:
            raise ValueError("DEPOSIT_MIN_RUB must be > 0 and <= DEPOSIT_MAX_RUB.")
        if self.access_token_expire_minutes <= 0 or self.refresh_token_expire_days <= 0:
            raise ValueError("Token lifetimes must be positive.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings
