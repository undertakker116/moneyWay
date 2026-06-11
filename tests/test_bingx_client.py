from decimal import Decimal
from urllib.parse import parse_qs

import httpx

from app.integrations.bingx.client import BingXAPIError, BingXClient, BingXCredentials


def test_bingx_signature_matches_documented_example():
    client = BingXClient(
        BingXCredentials(
            api_key="test",
            secret_key="UuGuyEGt6ZEkpUObCYCmIfh0elYsZVh80jlYwpJuRZEw70t6vomMH7Sjmf94ztSI",
        )
    )

    signed = client._signed_query(
        {
            "quoteOrderQty": 20,
            "side": "BUY",
            "symbol": "ETHUSDT",
            "timestamp": 1649404670162,
            "type": "MARKET",
        }
    )

    assert parse_qs(signed)["signature"] == [
        "428a3c383bde514baff0d10d3c20e5adfaacaf799e324546dafe5ccc480dd827"
    ]


def test_signed_query_sorts_parameters():
    client = BingXClient(BingXCredentials(api_key="api", secret_key="secret"))
    signed = client._signed_query({"b": 2, "a": 1, "c": 3})
    assert signed.startswith("a=1&b=2&c=3&signature=")


async def test_internal_transfer_sends_signed_form_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("X-BX-APIKEY")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["params"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"code": 0, "data": {"id": "tx-1"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = BingXClient(BingXCredentials(api_key="api", secret_key="secret"), client=http)
        response = await client.create_internal_transfer(
            uid="12345678", amount_usdt=Decimal("12.50"), idempotency_key="idem-1"
        )

    assert response["code"] == 0
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/openApi/wallets/v1/capital/innerTransfer/apply")
    assert captured["api_key"] == "api"
    assert captured["content_type"] == "application/x-www-form-urlencoded"
    params = captured["params"]
    assert params["coin"] == ["USDT"]
    assert params["userAccountType"] == ["1"]
    assert params["userAccount"] == ["12345678"]
    assert params["amount"] == ["12.5"]
    assert params["walletType"] == ["1"]
    assert params["transferClientId"] == ["idem-1"]
    assert params["recvWindow"] == ["5000"]
    assert params["timestamp"]
    assert params["signature"]


async def test_nonzero_code_raises_classified_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 100001, "msg": "insufficient balance"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = BingXClient(BingXCredentials(api_key="api", secret_key="secret"), client=http)
        try:
            await client.create_internal_transfer(uid="12345678", amount_usdt=Decimal("5"))
        except BingXAPIError as exc:
            assert exc.is_insufficient_balance
            assert not exc.is_recipient_not_found
        else:
            raise AssertionError("expected BingXAPIError")
