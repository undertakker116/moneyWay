"""Грубый in-process rate limit (sliding window).

Бэкстоп на одном инстансе, не замена лимита на gateway/WAF и не делится между
процессами. Выключается RATE_LIMIT_ENABLED=false.
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque

from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings, get_app_settings
from app.core.http import client_ip

# key -> очередь монотонных меток времени запросов в окне. OrderedDict как LRU:
# при переполнении выкидываем наименее свежий ключ за O(1), без скана всей мапы.
_HITS: OrderedDict[str, deque[float]] = OrderedDict()
_MAX_TRACKED_KEYS = 50_000


def _check(key: str, *, limit: int, window_seconds: float) -> bool:
    """Возвращает True, если запрос в пределах лимита, иначе False."""
    now = time.monotonic()
    hits = _HITS.get(key)
    if hits is None:
        hits = _HITS[key] = deque()
        if len(_HITS) > _MAX_TRACKED_KEYS:
            _HITS.popitem(last=False)  # вытесняем самый старый ключ (LRU), O(1)
    else:
        _HITS.move_to_end(key)
    threshold = now - window_seconds
    while hits and hits[0] <= threshold:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def reset() -> None:
    """Сбрасывает состояние (для тестов)."""
    _HITS.clear()


def rate_limit(scope: str, *, limit: int, window_seconds: float = 60.0):
    """Зависимость: не более limit запросов с IP за окно."""

    async def dependency(
        request: Request,
        settings: Settings = Depends(get_app_settings),
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        key = f"{scope}:{client_ip(request, settings) or 'unknown'}"
        if not _check(key, limit=limit, window_seconds=window_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests, slow down.",
                headers={"Retry-After": str(int(window_seconds))},
            )

    return dependency
