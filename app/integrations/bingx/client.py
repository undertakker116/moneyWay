"""Клиент BingX для внутреннего перевода USDT (innerTransfer).

Документация: POST /openApi/wallets/v1/capital/innerTransfer/apply.
Подпись: HMAC-SHA256 по querystring, параметры отсортированы по ключу, signature
дописывается в конец; для POST signed-строка идёт телом x-www-form-urlencoded.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from urllib.parse import urlencode

import httpx

from app.core.config import Settings


class BingXError(RuntimeError):
    """Базовая ошибка интеграции BingX."""


class BingXConnectionError(BingXError):
    """Сетевой сбой/таймаут — запрос можно повторить."""


class BingXAPIError(BingXError):
    """BingX вернул не-2xx или ненулевой code."""

    def __init__(
        self,
        *,
        code: object,
        msg: str,
        http_status: int | None = None,
        payload: dict | str | None = None,
    ) -> None:
        self.code = code
        self.msg = msg or ""
        self.http_status = http_status
        self.payload = payload
        super().__init__(f"BingX API error code={code} msg={msg!r}")

    def _msg_has(self, *needles: str) -> bool:
        text = self.msg.lower()
        return any(needle in text for needle in needles)

    @property
    def is_duplicate(self) -> bool:
        """Повтор transferClientId — перевод уже принят, считаем идемпотентным успехом."""
        return self._msg_has("duplicate", "repeated", "already exist", "idempotent")

    @property
    def is_insufficient_balance(self) -> bool:
        return self._msg_has("insufficient", "not enough", "balance not")

    @property
    def is_recipient_not_found(self) -> bool:
        return self._msg_has("not found", "not exist", "does not exist", "no such user")


@dataclass(frozen=True, slots=True)
class BingXCredentials:
    api_key: str
    secret_key: str
    base_url: str = "https://open-api.bingx.com"


class BingXClient:
    INNER_TRANSFER_APPLY = "/openApi/wallets/v1/capital/innerTransfer/apply"
    INNER_TRANSFER_RECORDS = "/openApi/wallets/v1/capital/innerTransfer/records"
    UID_ACCOUNT_TYPE = 1  # userAccountType=1 → получатель задан по UID
    FUND_WALLET = 1  # walletType=1 → списываем с Fund-аккаунта

    def __init__(
        self,
        credentials: BingXCredentials,
        *,
        recv_window_ms: int = 5000,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credentials = credentials
        self._recv_window_ms = recv_window_ms
        self._timeout = timeout_seconds
        self._client = client

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> BingXClient:
        if not settings.bingx_api_key or not settings.bingx_secret_key:
            raise BingXError("BingX API credentials are not configured")
        return cls(
            BingXCredentials(
                api_key=settings.bingx_api_key,
                secret_key=settings.bingx_secret_key,
                base_url=settings.bingx_base_url,
            ),
            recv_window_ms=settings.bingx_recv_window_ms,
            timeout_seconds=settings.bingx_http_timeout_seconds,
            client=client,
        )

    async def create_internal_transfer(
        self,
        *,
        uid: str,
        amount_usdt: Decimal | str,
        idempotency_key: str | None = None,
    ) -> dict:
        """Внутренний перевод USDT на UID. idempotency_key → transferClientId (дедуп BingX)."""
        params: dict[str, object] = {
            "coin": "USDT",
            "userAccountType": self.UID_ACCOUNT_TYPE,
            "userAccount": uid,
            "amount": self._format_amount(amount_usdt),
            "walletType": self.FUND_WALLET,
        }
        if idempotency_key:
            params["transferClientId"] = idempotency_key
        return await self._signed_request("POST", self.INNER_TRANSFER_APPLY, params)

    async def list_internal_transfers(self, *, coin: str = "USDT", limit: int = 100) -> dict:
        """История внутренних переводов — для ручной реконсиляции."""
        return await self._signed_request(
            "GET", self.INNER_TRANSFER_RECORDS, {"coin": coin, "limit": limit}
        )

    def _signed_query(self, params: dict[str, object]) -> str:
        """Сортирует параметры по ключу, считает HMAC-SHA256 и дописывает signature."""
        query = urlencode(sorted(params.items()))
        signature = hmac.new(
            self._credentials.secret_key.encode(), query.encode(), sha256
        ).hexdigest()
        return f"{query}&signature={signature}"

    async def _signed_request(self, method: str, path: str, params: dict[str, object]) -> dict:
        signed = self._signed_query(
            {**params, "recvWindow": self._recv_window_ms, "timestamp": int(time.time() * 1000)}
        )
        url = f"{self._credentials.base_url.rstrip('/')}{path}"
        headers = {"X-BX-APIKEY": self._credentials.api_key}
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            request_kwargs = {"content": signed}
        else:
            url = f"{url}?{signed}"
            request_kwargs = {}

        try:
            if self._client is not None:
                response = await self._client.request(
                    method, url, headers=headers, timeout=self._timeout, **request_kwargs
                )
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(method, url, headers=headers, **request_kwargs)
        except httpx.HTTPError as exc:
            raise BingXConnectionError(str(exc)) from exc

        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400 or not isinstance(payload, dict):
            raise BingXAPIError(
                code=None,
                msg=response.text[:500],
                http_status=response.status_code,
                payload=payload if isinstance(payload, dict) else response.text[:500],
            )

        code = payload.get("code")
        if code not in (None, 0, "0"):
            raise BingXAPIError(
                code=code,
                msg=str(payload.get("msg", "")),
                http_status=response.status_code,
                payload=payload,
            )
        return payload

    @staticmethod
    def _format_amount(amount: Decimal | str) -> str:
        """Нормализует Decimal в строку без научной записи и лишних нулей."""
        value = Decimal(str(amount))
        if value <= 0:
            raise ValueError("amount_usdt must be positive")
        return format(value.normalize(), "f")
