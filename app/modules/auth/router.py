from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_app_settings
from app.core.database import get_session
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
    """Возвращает IP клиента из текущего HTTP-запроса, если он доступен."""
    return request.client.host if request.client else None


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> TokenPair:
    """Регистрирует пользователя и выдает JWT-пару."""
    existing_user = await get_user_by_email(session, str(payload.email))
    if existing_user is not None:
        raise ConflictError("User with this email already exists")

    user = await create_user(
        session,
        email=str(payload.email),
        password=payload.password,
        full_name=payload.full_name,
    )
    tokens = await issue_token_pair(
        session,
        user=user,
        settings=settings,
        ip_address=_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return tokens


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> TokenPair:
    """Проверяет email/пароль и выдает JWT-пару."""
    user = await authenticate_user(session, email=str(payload.email), password=payload.password)
    if user is None or not user.is_active:
        raise UnauthorizedError("Invalid email or password")

    tokens = await issue_token_pair(
        session,
        user=user,
        settings=settings,
        ip_address=_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh_token(
    payload: RefreshTokenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> TokenPair:
    """Обновляет access/refresh токены и отзывает использованный refresh-токен."""
    tokens = await rotate_refresh_token(
        session,
        refresh_token=payload.refresh_token,
        settings=settings,
        ip_address=_request_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return tokens


@router.post("/logout", response_model=AuthMessage)
async def logout(
    payload: LogoutRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> AuthMessage:
    """Отзывает refresh-токен, чтобы пользовательская сессия больше не обновлялась."""
    await revoke_refresh_token(session, refresh_token=payload.refresh_token, settings=settings)
    await session.commit()
    return AuthMessage(detail="Logged out")
