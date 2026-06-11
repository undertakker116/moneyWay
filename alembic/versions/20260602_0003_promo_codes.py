from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260602_0003"
down_revision: str | None = "20260526_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promo_codes")),
        sa.UniqueConstraint("code", name=op.f("uq_promo_codes_code")),
        sa.CheckConstraint(
            "discount_percent >= 0 AND discount_percent <= 100", name="discount_range"
        ),
        sa.CheckConstraint("used_count >= 0", name="used_count_non_negative"),
    )
    # transactions.promo_code_id уже есть с миграции 0002 — добавляем FK теперь, когда есть таблица.
    op.create_foreign_key(
        op.f("fk_transactions_promo_code_id_promo_codes"),
        "transactions",
        "promo_codes",
        ["promo_code_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Индекс под антифрод-проверку device_fingerprint (иначе seq scan на каждый депозит).
    op.create_index(
        "ix_transactions_device_fingerprint",
        "transactions",
        ["device_fingerprint"],
        postgresql_where=sa.text("device_fingerprint IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_device_fingerprint", table_name="transactions")
    op.drop_constraint(
        op.f("fk_transactions_promo_code_id_promo_codes"),
        "transactions",
        type_="foreignkey",
    )
    op.drop_table("promo_codes")
