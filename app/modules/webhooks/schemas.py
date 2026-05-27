from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SBPPaymentWebhook(BaseModel):
    transaction_id: str = Field(min_length=1)
    status: Literal["paid", "failed"]
    sbp_payment_id: str | None = Field(default=None, max_length=128)
    payer_name: str | None = Field(default=None, max_length=255)
    payer_phone: str | None = Field(default=None, max_length=64)
    amount_rub: Decimal | None = None

    model_config = ConfigDict(extra="forbid")


class SBPChargebackWebhook(BaseModel):
    transaction_id: str = Field(min_length=1)
    reason: str = Field(default="chargeback", max_length=255)

    model_config = ConfigDict(extra="forbid")


class WebhookResponse(BaseModel):
    detail: str
