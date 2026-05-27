from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

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
    Column("role", Enum("USER", "ADMIN", name="user_role", native_enum=False), nullable=False),
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
    Column("user_id", String(36), ForeignKey("users.id"), index=True, nullable=False),
    Column("promo_code_id", String(36), nullable=True),
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
    Column("bingx_response", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
    Column("user_id", String(36), ForeignKey("users.id"), nullable=True),
    Column("transaction_id", String(36), ForeignKey("transactions.id"), nullable=True),
    Column("alert_type", String(64), nullable=False),
    Column("metadata", JSON, nullable=True),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

Table(
    "audit_log",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("event_type", String(64), index=True, nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=True),
    Column("transaction_id", String(36), ForeignKey("transactions.id"), index=True, nullable=True),
    Column("payload", JSON, nullable=True),
    Column("ip_address", String(64), nullable=True),
    Column("device_fingerprint", String(128), nullable=True),
    Column("user_agent", String(512), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
