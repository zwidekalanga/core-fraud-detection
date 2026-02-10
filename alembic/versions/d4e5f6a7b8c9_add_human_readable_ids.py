"""add_human_readable_ids

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-10 10:00:00.000000

Adds human-readable identifiers to replace raw UUIDs in the UI:
- transactions.account_number (Capitec 10-digit format from core-banking)
- fraud_alerts.reference_number (e.g. FRD-00142)
- fraud_alerts.reviewed_by_username (display name alongside UUID)
- alert_ref_seq PostgreSQL sequence for atomic reference number generation
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add account_number to transactions
    op.add_column(
        "transactions",
        sa.Column("account_number", sa.String(20), nullable=True),
    )
    op.create_index("ix_transactions_account_number", "transactions", ["account_number"])

    # 2. Create sequence for alert reference numbers
    op.execute("CREATE SEQUENCE IF NOT EXISTS alert_ref_seq START WITH 1 INCREMENT BY 1")

    # 3. Add reference_number to fraud_alerts
    op.add_column(
        "fraud_alerts",
        sa.Column("reference_number", sa.String(20), nullable=True),
    )
    op.create_index(
        "ix_fraud_alerts_reference_number",
        "fraud_alerts",
        ["reference_number"],
        unique=True,
    )

    # 4. Add reviewed_by_username to fraud_alerts
    op.add_column(
        "fraud_alerts",
        sa.Column("reviewed_by_username", sa.String(100), nullable=True),
    )

    # 5. Backfill existing alerts with reference numbers
    op.execute(
        "UPDATE fraud_alerts "
        "SET reference_number = 'FRD-' || LPAD(nextval('alert_ref_seq')::text, 5, '0') "
        "WHERE reference_number IS NULL"
    )

    # 6. Backfill reviewed_by_username for system auto-escalation entries
    op.execute(
        "UPDATE fraud_alerts "
        "SET reviewed_by_username = 'System' "
        "WHERE reviewed_by = 'system:auto-escalation' AND reviewed_by_username IS NULL"
    )


def downgrade() -> None:
    op.drop_column("fraud_alerts", "reviewed_by_username")
    op.drop_index("ix_fraud_alerts_reference_number", table_name="fraud_alerts")
    op.drop_column("fraud_alerts", "reference_number")
    op.execute("DROP SEQUENCE IF EXISTS alert_ref_seq")
    op.drop_index("ix_transactions_account_number", table_name="transactions")
    op.drop_column("transactions", "account_number")
