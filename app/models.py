"""SQLAlchemy ORM-модели — ТОЛЬКО для admin-панели (starlette-admin).

Рантайм бэкенда работает на raw asyncpg; эти модели зеркалят схему один-в-один и
не используются в боевом пути. Менять схему по-прежнему через Alembic.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(255))
    bingx_uid: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean)
    is_verified: Mapped[bool] = mapped_column(Boolean)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    promo_code_id: Mapped[str | None] = mapped_column(String(36))
    amount_rub: Mapped[float] = mapped_column(Numeric(18, 2))
    amount_usdt: Mapped[float] = mapped_column(Numeric(18, 8))
    commission: Mapped[float] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(32))
    sbp_payment_id: Mapped[str | None] = mapped_column(String(128))
    bingx_transfer_id: Mapped[str | None] = mapped_column(String(128))
    bingx_uid: Mapped[str] = mapped_column(String(64))
    payer_ip: Mapped[str | None] = mapped_column(String(64))
    device_fingerprint: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(64))
    discount_percent: Mapped[float] = mapped_column(Numeric(5, 2))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Blacklist(Base):
    __tablename__ = "blacklist"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(255))
    comment: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FraudAlert(Base):
    __tablename__ = "fraud_alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    transaction_id: Mapped[str | None] = mapped_column(String(36))
    alert_type: Mapped[str] = mapped_column(String(64))
    # `metadata` зарезервировано в DeclarativeBase → маппим в атрибут alert_metadata.
    alert_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB)
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KycRecord(Base):
    __tablename__ = "kyc_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36))
    payer_name: Mapped[str] = mapped_column(String(255))
    payer_phone: Mapped[str] = mapped_column(String(64))
    amount_rub: Mapped[float] = mapped_column(Numeric(18, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BingxTransfer(Base):
    __tablename__ = "bingx_transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    bingx_transfer_id: Mapped[str | None] = mapped_column(String(128))
    recipient_uid: Mapped[str] = mapped_column(String(64))
    amount_usdt: Mapped[float] = mapped_column(Numeric(18, 8))
    status: Mapped[str] = mapped_column(String(32))
    attempt_number: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bingx_response: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str | None] = mapped_column(String(36))
    transaction_id: Mapped[str | None] = mapped_column(String(36))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    device_fingerprint: Mapped[str | None] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
