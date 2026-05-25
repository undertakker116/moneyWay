from fastapi import FastAPI, Request, Response


def add_security_headers(app: FastAPI) -> None:
    """Подключает middleware, добавляющий базовые security headers к ответам."""

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        """Добавляет защитные HTTP-заголовки к каждому ответу API."""
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        return response
