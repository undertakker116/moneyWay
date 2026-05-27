from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from hmac import new as hmac_new
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BingXAPIError(RuntimeError):
    def __init__(self, status_code: int, payload: dict | str) -> None:
        self.status_code = status_code
        self.payload = payload
        super().__init__(f"BingX API request failed with status {status_code}")


@dataclass(frozen=True, slots=True)
class BingXCredentials:
    api_key: str
    secret_key: str
    base_url: str = "https://open-api.bingx.com"


class BingXClient:
    INNER_TRANSFER_PATH = "/openApi/wallets/v1/capital/innerTransfer/apply"

    def __init__(self, credentials: BingXCredentials) -> None:
        self.credentials = credentials

    async def create_internal_transfer(
        self,
        *,
        uid: str,
        amount_usdt: Decimal | str,
    ) -> dict:
        """Создает внутренний перевод USDT на UID BingX через signed wallet endpoint."""
        body = self._signed_body(
            {
                "coin": "USDT",
                "userAccountType": 1,
                "userAccount": uid,
                "amount": self._amount(amount_usdt),
                "walletType": 1,
                "timestamp": int(time.time() * 1000),
            }
        )
        return await asyncio.to_thread(self._post_form, self.INNER_TRANSFER_PATH, body)

    def _signed_body(self, params: dict[str, object]) -> bytes:
        """Собирает form body и добавляет HMAC SHA256 signature по правилам BingX."""
        payload = urlencode(params)
        # BingX подписывает ровно ту form-строку, к которой потом добавляется signature.
        signature = hmac_new(
            self.credentials.secret_key.encode(),
            payload.encode(),
            sha256,
        ).hexdigest()
        return f"{payload}&signature={signature}".encode()

    def _post_form(self, path: str, body: bytes) -> dict:
        """Отправляет signed form request и превращает HTTP/API ошибку BingX в исключение."""
        request = Request(
            f"{self.credentials.base_url.rstrip('/')}{path}",
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-BX-APIKEY": self.credentials.api_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode())
                if isinstance(payload, dict) and payload.get("code") not in (None, 0, "0"):
                    raise BingXAPIError(response.status, payload)
                return payload
        except HTTPError as exc:
            payload = exc.read().decode()
            try:
                decoded_payload: dict | str = json.loads(payload)
            except json.JSONDecodeError:
                decoded_payload = payload
            raise BingXAPIError(exc.code, decoded_payload) from exc

    def _amount(self, amount: Decimal | str) -> str:
        """Нормализует Decimal в строку без лишних нулей для API BingX."""
        value = Decimal(str(amount))
        if value <= 0:
            raise ValueError("amount_usdt must be positive")
        return format(value.normalize(), "f")
