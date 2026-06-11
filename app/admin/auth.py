import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.requests import Request
from starlette.responses import Response
from starlette_admin.auth import AdminUser, AuthProvider
from starlette_admin.exceptions import LoginFailed

from app.core.security import verify_password
from app.models import User

ADMIN_SESSION_KEY = "admin_user_id"


class AdminAuthProvider(AuthProvider):
    """Сессионная авторизация админки: вход по email+пароль, только role=admin.

    Отдельный от API контур (как в Django-admin). Пароль проверяется argon2 в потоке.
    """

    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        super().__init__()
        self._sessionmaker = sessionmaker

    async def _load(self, *, email: str | None = None, user_id: str | None = None) -> User | None:
        async with self._sessionmaker() as session:
            stmt = select(User)
            stmt = stmt.where(User.email == email) if email else stmt.where(User.id == user_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def login(
        self,
        username: str,
        password: str,
        remember_me: bool,
        request: Request,
        response: Response,
    ) -> Response:
        user = await self._load(email=username.strip().lower())
        if user is None or user.role != "admin" or not user.is_active:
            raise LoginFailed("Invalid credentials")
        if not await asyncio.to_thread(verify_password, password, user.password_hash):
            raise LoginFailed("Invalid credentials")
        request.session.clear()  # ротация сессии при логине (анти-fixation)
        request.session[ADMIN_SESSION_KEY] = user.id
        return response

    async def is_authenticated(self, request: Request) -> bool:
        user_id = request.session.get(ADMIN_SESSION_KEY)
        if not user_id:
            return False
        user = await self._load(user_id=user_id)
        if user is not None and user.role == "admin" and user.is_active:
            request.state.admin_user = user
            return True
        return False

    def get_admin_user(self, request: Request) -> AdminUser | None:
        user = getattr(request.state, "admin_user", None)
        return AdminUser(username=user.email) if user is not None else None

    async def logout(self, request: Request, response: Response) -> Response:
        request.session.clear()
        return response
