from decimal import Decimal
from urllib.parse import parse_qs

from app.integrations.bingx.client import BingXClient, BingXCredentials


def test_bingx_signature_matches_documented_example():
    client = BingXClient(
        BingXCredentials(
            api_key="test",
            secret_key="UuGuyEGt6ZEkpUObCYCmIfh0elYsZVh80jlYwpJuRZEw70t6vomMH7Sjmf94ztSI",
        )
    )

    body = client._signed_body(
        {
            "quoteOrderQty": 20,
            "side": "BUY",
            "symbol": "ETHUSDT",
            "timestamp": 1649404670162,
            "type": "MARKET",
        }
    )

    assert parse_qs(body.decode())["signature"] == [
        "428a3c383bde514baff0d10d3c20e5adfaacaf799e324546dafe5ccc480dd827"
    ]


async def test_bingx_internal_transfer_payload(monkeypatch):
    client = BingXClient(BingXCredentials(api_key="api", secret_key="secret"))
    captured = {}

    def fake_post(path: str, body: bytes) -> dict:
        captured["path"] = path
        captured["params"] = parse_qs(body.decode())
        return {"code": 0, "data": {"transferId": "tx-1"}}

    monkeypatch.setattr(client, "_post_form", fake_post)

    response = await client.create_internal_transfer(uid="12345678", amount_usdt=Decimal("12.50"))

    assert response["code"] == 0
    assert captured["path"] == "/openApi/wallets/v1/capital/innerTransfer/apply"
    assert captured["params"]["coin"] == ["USDT"]
    assert captured["params"]["userAccountType"] == ["1"]
    assert captured["params"]["userAccount"] == ["12345678"]
    assert captured["params"]["amount"] == ["12.5"]
    assert captured["params"]["walletType"] == ["1"]
    assert captured["params"]["signature"]
