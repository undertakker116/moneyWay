import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.config import Settings
from app.core.errors import UnauthorizedError

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return password_hash.verify(password, hashed_password)


def hash_token(token: str, settings: Settings) -> str:
    return hmac.new(settings.secret_key.encode(), token.encode(), sha256).hexdigest()


def create_jwt_token(
    *,
    subject: str,
    token_type: str,
    settings: Settings,
    expires_delta: timedelta,
    token_id: str | None = None,
) -> tuple[str, str, datetime]:
    now = datetime.now(UTC)
    expires_at = now + expires_delta
    jti = token_id or str(uuid4())
    payload = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, jti, expires_at


def decode_jwt_token(token: str, *, settings: Settings, expected_type: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "type", "jti", "exp", "iat", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc

    if payload.get("type") != expected_type:
        raise UnauthorizedError("Invalid token type")
    return payload
