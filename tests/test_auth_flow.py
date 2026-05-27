def auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def register_user(client, *, email: str = "user@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass1",
            "full_name": "Test User",
            "bingx_uid": "12345678",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_register_login_refresh_logout_flow(client):
    tokens = register_user(client)
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    me_response = client.get("/api/v1/users/me", headers=auth_headers(tokens["access_token"]))
    assert me_response.status_code == 200, me_response.text
    assert me_response.json()["email"] == "user@example.com"
    assert me_response.json()["bingx_uid"] == "12345678"

    cabinet_response = client.get(
        "/api/v1/cabinet/me",
        headers=auth_headers(tokens["access_token"]),
    )
    assert cabinet_response.status_code == 200, cabinet_response.text
    assert cabinet_response.json()["email"] == "user@example.com"
    assert cabinet_response.json()["bingx_uid"] == "12345678"

    duplicate_response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "USER@example.com",
            "password": "StrongPass1",
            "full_name": "Duplicate",
        },
    )
    assert duplicate_response.status_code == 409

    bad_login = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "wrong"},
    )
    assert bad_login.status_code == 401

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "StrongPass1"},
    )
    assert login_response.status_code == 200, login_response.text
    login_tokens = login_response.json()

    refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login_tokens["refresh_token"]},
    )
    assert refresh_response.status_code == 200, refresh_response.text
    rotated_tokens = refresh_response.json()
    assert rotated_tokens["refresh_token"] != login_tokens["refresh_token"]

    reused_refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login_tokens["refresh_token"]},
    )
    assert reused_refresh_response.status_code == 401

    logout_response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": rotated_tokens["refresh_token"]},
    )
    assert logout_response.status_code == 200, logout_response.text

    revoked_refresh_response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": rotated_tokens["refresh_token"]},
    )
    assert revoked_refresh_response.status_code == 401


def test_protected_routes_require_bearer_token(client):
    response = client.get("/api/v1/cabinet/me")
    assert response.status_code == 401


def test_blank_bingx_uid_is_not_stored(client):
    tokens = register_user(client, email="blank-uid@example.com")
    response = client.patch(
        "/api/v1/users/me",
        headers=auth_headers(tokens["access_token"]),
        json={"bingx_uid": "   "},
    )

    assert response.status_code == 200, response.text
    assert response.json()["bingx_uid"] is None
