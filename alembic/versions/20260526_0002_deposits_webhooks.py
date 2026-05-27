from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260526_0002"
down_revision: str | None = "20260525_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("promo_code_id", sa.String(length=36), nullable=True),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount_usdt", sa.Numeric(18, 8), nullable=False),
        sa.Column("commission", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sbp_payment_id", sa.String(length=128), nullable=True),
        sa.Column("bingx_transfer_id", sa.String(length=128), nullable=True),
        sa.Column("bingx_uid", sa.String(length=64), nullable=False),
        sa.Column("payer_ip", sa.String(length=64), nullable=True),
        sa.Column("device_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_transactions_user_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transactions")),
    )
    op.create_index(op.f("ix_transactions_user_id"), "transactions", ["user_id"])
    op.create_index(op.f("ix_transactions_status"), "transactions", ["status"])
    op.create_index(op.f("ix_transactions_bingx_uid"), "transactions", ["bingx_uid"])

    op.create_table(
        "kyc_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.String(length=36), nullable=False),
        sa.Column("payer_name", sa.String(length=255), nullable=False),
        sa.Column("payer_phone", sa.String(length=64), nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_kyc_records_transaction_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kyc_records")),
        sa.UniqueConstraint("transaction_id", name=op.f("uq_kyc_records_transaction_id")),
    )

    op.create_table(
        "bingx_transfers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("transaction_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("bingx_transfer_id", sa.String(length=128), nullable=True),
        sa.Column("recipient_uid", sa.String(length=64), nullable=False),
        sa.Column("amount_usdt", sa.Numeric(18, 8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("bingx_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_bingx_transfers_transaction_id_transactions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bingx_transfers")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_bingx_transfers_idempotency_key")),
    )
    op.create_index(
        op.f("ix_bingx_transfers_transaction_id"),
        "bingx_transfers",
        ["transaction_id"],
    )
    op.create_index(op.f("ix_bingx_transfers_status"), "bingx_transfers", ["status"])

    op.create_table(
        "blacklist",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("comment", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blacklist")),
        sa.UniqueConstraint("type", "value", name="uq_blacklist_type_value"),
    )
    op.create_index(op.f("ix_blacklist_value"), "blacklist", ["value"])

    op.create_table(
        "fraud_alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("transaction_id", sa.String(length=36), nullable=True),
        sa.Column("alert_type", sa.String(length=64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_fraud_alerts_user_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_fraud_alerts_transaction_id_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fraud_alerts")),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("transaction_id", sa.String(length=36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("device_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_audit_log_user_id_users")),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_audit_log_transaction_id_transactions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_event_type"), "audit_log", ["event_type"])
    op.create_index(op.f("ix_audit_log_transaction_id"), "audit_log", ["transaction_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_transaction_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_event_type"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("fraud_alerts")
    op.drop_index(op.f("ix_blacklist_value"), table_name="blacklist")
    op.drop_table("blacklist")
    op.drop_index(op.f("ix_bingx_transfers_status"), table_name="bingx_transfers")
    op.drop_index(op.f("ix_bingx_transfers_transaction_id"), table_name="bingx_transfers")
    op.drop_table("bingx_transfers")
    op.drop_table("kyc_records")
    op.drop_index(op.f("ix_transactions_bingx_uid"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_status"), table_name="transactions")
    op.drop_index(op.f("ix_transactions_user_id"), table_name="transactions")
    op.drop_table("transactions")
