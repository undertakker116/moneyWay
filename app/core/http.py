from fastapi import Request

from app.core.config import Settings


def client_ip(request: Request, settings: Settings) -> str | None:
    """IP клиента для антифрода/rate-limit/audit.

    За доверенным прокси берём X-Real-IP (nginx/traefik перезаписывают его реальным
    socket-адресом клиента — не подделать). Иначе — адрес соединения.
    """
    if settings.trusted_proxy:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64]
    return request.client.host if request.client else None
