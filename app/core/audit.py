import json
from datetime import UTC, datetime
from uuid import uuid4

import asyncpg


async def write_audit_log(
    connection: asyncpg.Connection,
    *,
    event_type: str,
    user_id: str | None,
    transaction_id: str | None,
    payload: dict | None = None,
    ip_address: str | None = None,
    device_fingerprint: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Пишет audit_log с JSON payload и техническими данными запроса."""
    await connection.execute(
        """
        INSERT INTO audit_log (
            id,
            event_type,
            user_id,
            transaction_id,
            payload,
            ip_address,
            device_fingerprint,
            user_agent,
            created_at
        )
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
        """,
        str(uuid4()),
        event_type,
        user_id,
        transaction_id,
        json.dumps(payload) if payload else None,
        ip_address,
        device_fingerprint,
        user_agent[:512] if user_agent else None,
        datetime.now(UTC),
    )
