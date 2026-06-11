from fastapi import FastAPI, Request, Response

# Swagger/ReDoc/admin-UI подтягивают свои JS/CSS — на них строгий CSP не вешаем.
# (JSON-API под /api/v1/admin не попадает: путь начинается с /api, не с /admin.)
_DOCS_PATHS = ("/docs", "/redoc", "/admin")


def add_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        # HSTS: браузер ходит на домен только по HTTPS. Полезен, только когда TLS
        # терминируется выше (reverse-proxy/CDN) — но дёшев и безопасен как дефолт.
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
        # JSON-API ничего не рендерит — запрещаем любой ресурс/встраивание.
        if not request.url.path.startswith(_DOCS_PATHS):
            response.headers.setdefault(
                "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
            )
        return response
