"""Мелкие хелперы сериализации для JSON-ответов/payload (общие для модулей)."""

from datetime import datetime


def str_or_none(value: object) -> str | None:
    return str(value) if value is not None else None


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
