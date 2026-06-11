from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)

Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("email", String(320), unique=True, index=True, nullable=False),
    Column("password_hash", String(255), nullable=False),
    Column("full_name", String(255), nullable=True),
    Column("bingx_uid", String(64), index=True, nullable=True),
    Column("role", Enum("user", "admin", name="user_role", native_enum=False), nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("is_verified", Boolean, nullable=False),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Table(
    "refresh_tokens",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True),
    Column("jwt_id", String(64), unique=True, index=True, nullable=False),
    Column("token_hash", String(128), unique=True, index=True, nullable=False),
    Column("family_id", String(36), index=True, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
    Column("created_by_ip", String(64), nullable=True),
    Column("user_agent", String(512), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Table(
    "transactions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "user_id",
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    ),
    Column(
        "promo_code_id",
        String(36),
        ForeignKey("promo_codes.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("amount_rub", Numeric(18, 2), nullable=False),
    Column("amount_usdt", Numeric(18, 8), nullable=False),
    Column("commission", Numeric(18, 2), nullable=False),
    Column("status", String(32), index=True, nullable=False),
    Column("sbp_payment_id", String(128), nullable=True),
    Column("bingx_transfer_id", String(128), nullable=True),
    Column("bingx_uid", String(64), index=True, nullable=False),
    Column("payer_ip", String(64), nullable=True),
    Column("device_fingerprint", String(128), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "amount_rub > 0 AND amount_usdt > 0 AND commission >= 0",
        name="amounts_positive",
    ),
    Index(
        "ix_transactions_device_fingerprint",
        "device_fingerprint",
        postgresql_where=text("device_fingerprint IS NOT NULL"),
    ),
)

Table(
    "kyc_records",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "transaction_id",
        String(36),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    ),
    Column("payer_name", String(255), nullable=False),
    Column("payer_phone", String(64), nullable=False),
    Column("amount_rub", Numeric(18, 2), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Table(
    "bingx_transfers",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "transaction_id",
        String(36),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        index=True,
    ),
    Column("idempotency_key", String(128), unique=True, nullable=False),
    Column("bingx_transfer_id", String(128), nullable=True),
    Column("recipient_uid", String(64), nullable=False),
    Column("amount_usdt", Numeric(18, 8), nullable=False),
    Column("status", String(32), index=True, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("bingx_response", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("amount_usdt > 0", name="amount_positive"),
    Index(
        "ix_bingx_transfers_claimable",
        "created_at",
        postgresql_where=text("status IN ('pending', 'processing')"),
    ),
)

Table(
    "promo_codes",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("code", String(64), unique=True, nullable=False),
    Column("discount_percent", Numeric(5, 2), nullable=False),
    Column("max_uses", Integer, nullable=True),
    Column("used_count", Integer, nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("discount_percent >= 0 AND discount_percent <= 100", name="discount_range"),
    CheckConstraint("used_count >= 0", name="used_count_non_negative"),
)

Table(
    "blacklist",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("type", String(32), nullable=False),
    Column("value", String(255), index=True, nullable=False),
    Column("reason", String(255), nullable=False),
    Column("comment", String(512), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("type", "value", name="uq_blacklist_type_value"),
)

Table(
    "fraud_alerts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
    Column(
        "transaction_id",
        String(36),
        ForeignKey("transactions.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("alert_type", String(64), nullable=False),
    Column("metadata", JSONB, nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Table(
    "audit_log",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("event_type", String(64), index=True, nullable=False),
    Column("user_id", String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True),
    Column(
        "transaction_id",
        String(36),
        ForeignKey("transactions.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    ),
    Column("payload", JSONB, nullable=True),
    Column("ip_address", String(64), nullable=True),
    Column("device_fingerprint", String(128), nullable=True),
    Column("user_agent", String(512), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
