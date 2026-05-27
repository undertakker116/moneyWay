import hmac

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.config import Settings, get_app_settings
from app.core.database import get_connection
from app.modules.webhooks.schemas import SBPChargebackWebhook, SBPPaymentWebhook, WebhookResponse
from app.modules.webhooks.service import handle_chargeback_webhook, handle_sbp_payment_webhook

router = APIRouter()


def verify_webhook_secret(settings: Settings, provided_secret: str | None) -> None:
    """Если WEBHOOK_SECRET задан, webhook без совпадающего header не принимается."""
    if not settings.webhook_secret:
        return
    if provided_secret is None or not hmac.compare_digest(provided_secret, settings.webhook_secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )


@router.post(
    "/sbp/payment",
    response_model=WebhookResponse,
    summary="Handle SBP payment webhook",
    description="Stores SBP payment result, payer KYC data and schedules a BingX transfer.",
)
async def sbp_payment_webhook(
    payload: SBPPaymentWebhook,
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
    x_webhook_secret: str | None = Header(default=None),
) -> WebhookResponse:
    """Принимает webhook успешной/ошибочной оплаты от СБП-провайдера."""
    verify_webhook_secret(settings, x_webhook_secret)
    await handle_sbp_payment_webhook(connection, payload=payload)
    return WebhookResponse(detail="accepted")


@router.post(
    "/sbp/chargeback",
    response_model=WebhookResponse,
    summary="Handle SBP chargeback webhook",
    description="Marks a transaction as chargeback and records antifraud evidence.",
)
async def sbp_chargeback_webhook(
    payload: SBPChargebackWebhook,
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
    x_webhook_secret: str | None = Header(default=None),
) -> WebhookResponse:
    """Принимает webhook чарджбэка и запускает антифрод-фиксацию."""
    verify_webhook_secret(settings, x_webhook_secret)
    await handle_chargeback_webhook(connection, payload=payload)
    return WebhookResponse(detail="accepted")
