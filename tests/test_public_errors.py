import json
import re


def test_validation_errors_use_public_error_contract(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "bad-email", "password": "short"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["message"] == "Invalid request."
    fields = payload["error"]["fields"]
    assert any(field["name"] == "email" for field in fields)
    assert any(field["name"] == "password" for field in fields)

    response_text = json.dumps(payload)
    for internal_key in ["loc", "msg", "type", "input", "ctx"]:
        assert internal_key not in response_text


def test_openapi_does_not_publish_default_validation_schema(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    schema_text = json.dumps(schema, ensure_ascii=False)

    assert "HTTPValidationError" not in schema_text
    assert "ValidationError" not in schema.get("components", {}).get("schemas", {})
    assert not re.search(r"[А-Яа-яЁё]", schema_text)

    for path_item in schema["paths"].values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                assert operation.get("responses", {}).get("422") is None


def test_openapi_uses_http_bearer_security(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    schema = response.json()
    schema_text = json.dumps(schema)
    security_schemes = schema["components"]["securitySchemes"]

    assert "OAuth2PasswordBearer" not in schema_text
    assert security_schemes["HTTPBearer"]["scheme"] == "bearer"


def test_openapi_does_not_publish_provider_webhooks(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200

    paths = response.json()["paths"]
    assert "/api/v1/webhooks/sbp/payment" not in paths
    assert "/api/v1/webhooks/sbp/chargeback" not in paths
