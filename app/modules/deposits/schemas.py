from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DepositCreateRequest(BaseModel):
    amount_rub: Decimal = Field(gt=0)
    bingx_uid: str = Field(min_length=3, max_length=64)

    model_config = ConfigDict(extra="forbid")

    @field_validator("bingx_uid", mode="before")
    @classmethod
    def normalize_bingx_uid(cls, value: object) -> object:
        """Обрезает пробелы вокруг BingX UID при создании пополнения."""
        if not isinstance(value, str):
            return value
        return value.strip()


class DepositCreateResponse(BaseModel):
    transaction_id: str
    status: str
    amount_rub: Decimal
    amount_usdt: Decimal
    commission: Decimal
    payment_url: str | None = None
