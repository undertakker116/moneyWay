from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings
from app.core.http import client_ip
from app.core.ratelimit import _check


class AdminLoginRateLimitMiddleware:
    """Троттлит POST /admin/login по IP — брутфорс админ-пароля (API-лимитер сюда не достаёт)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        limit: int = 5,
        window_seconds: float = 60.0,
    ) -> None:
        self.app = app
        self._settings = settings
        self._limit = limit
        self._window = window_seconds

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and self._settings.rate_limit_enabled:
            request = Request(scope, receive=receive)
            if request.method == "POST" and request.url.path.endswith("/login"):
                ip = client_ip(request, self._settings) or "unknown"
                if not _check(f"admin_login:{ip}", limit=self._limit, window_seconds=self._window):
                    response: Response = PlainTextResponse(
                        "Too many login attempts, slow down.",
                        status_code=429,
                        headers={"Retry-After": str(int(self._window))},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
