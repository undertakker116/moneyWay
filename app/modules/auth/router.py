import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.core.config import Settings, get_app_settings
from app.core.database import get_connection
from app.core.errors import ConflictError, UnauthorizedError
from app.modules.auth.schemas import (
    AuthMessage,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenPair,
)
from app.modules.auth.service import issue_token_pair, revoke_refresh_token, rotate_refresh_token
from app.modules.users.service import authenticate_user, create_user, get_user_by_email

router = APIRouter()


def _request_ip(request: Request) -> str | None:
    """Достает IP клиента из HTTP request, если Starlette смог его определить."""
    return request.client.host if request.client else None


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Register user",
    description="Creates a user account and returns access and refresh JWT tokens.",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
) -> TokenPair:
    """Регистрирует нового пользователя и сразу выдает пару JWT-токенов."""
    existing_user = await get_user_by_email(connection, str(payload.email))
    if existing_user is not None:
        raise ConflictError("User with this email already exists")

    user = await create_user(
        connection,
        email=str(payload.email),
        password=payload.password,
        full_name=payload.full_name,
        bingx_uid=payload.bingx_uid,
    )
    tokens = await issue_token_pair(
        connection,
        user=user,
        settings=settings,
        ip_address=_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return tokens


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Login",
    description="Authenticates a user by email and password and returns JWT tokens.",
)
async def login(
    payload: LoginRequest,
    request: Request,
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
) -> TokenPair:
    """Проверяет email/password и при успехе создает новую JWT-сессию."""
    user = await authenticate_user(connection, email=str(payload.email), password=payload.password)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid email or password")

    tokens = await issue_token_pair(
        connection,
        user=user,
        settings=settings,
        ip_address=_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return tokens


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Refresh tokens",
    description="Rotates a refresh token and returns a new access/refresh JWT pair.",
)
async def refresh_token(
    payload: RefreshTokenRequest,
    request: Request,
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
) -> TokenPair:
    """Меняет действующий refresh token на новую пару access/refresh токенов."""
    tokens = await rotate_refresh_token(
        connection,
        refresh_token=payload.refresh_token,
        settings=settings,
        ip_address=_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return tokens


@router.post(
    "/logout",
    response_model=AuthMessage,
    summary="Logout",
    description="Revokes a refresh token so it can no longer be used.",
)
async def logout(
    payload: LogoutRequest,
    connection: asyncpg.Connection = Depends(get_connection),
    settings: Settings = Depends(get_app_settings),
) -> AuthMessage:
    """Отзывает refresh token, чтобы текущую сессию нельзя было продлить."""
    await revoke_refresh_token(connection, refresh_token=payload.refresh_token, settings=settings)
    return AuthMessage(detail="Logged out")
