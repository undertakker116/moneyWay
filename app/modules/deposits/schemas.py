from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DepositCreateRequest(BaseModel):
    # max_digits/decimal_places ограничивают масштаб ещё на уровне схемы (не только
    # бизнес-лимиты в create_deposit), чтобы огромное/слишком точное число не дошло до БД.
    amount_rub: Decimal = Field(gt=0, le=Decimal("100000000"), max_digits=12, decimal_places=2)
    bingx_uid: str = Field(min_length=3, max_length=64, pattern=r"^\d+$")
    promo_code: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")

    @field_validator("bingx_uid", mode="before")
    @classmethod
    def normalize_bingx_uid(cls, value: object) -> object:
        """Обрезает пробелы вокруг BingX UID при создании пополнения."""
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("promo_code", mode="before")
    @classmethod
    def normalize_promo_code(cls, value: object) -> object:
        """Нормализует промокод: trim + upper, пустой → None."""
        if not isinstance(value, str):
            return value
        value = value.strip().upper()
        return value or None


class DepositCreateResponse(BaseModel):
    transaction_id: str
    status: str
    amount_rub: Decimal
    amount_usdt: Decimal
    commission: Decimal
    payment_url: str | None = None
