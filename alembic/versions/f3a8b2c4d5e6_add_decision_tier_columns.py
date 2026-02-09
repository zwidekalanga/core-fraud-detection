"""add_decision_tier_columns

Revision ID: f3a8b2c4d5e6
Revises: 29b8e9126b57
Create Date: 2026-02-05 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a8b2c4d5e6"
down_revision: Union[str, None] = "29b8e9126b57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add decision tier columns to fraud_alerts table."""
    op.add_column(
        "fraud_alerts",
        sa.Column("decision_tier", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "fraud_alerts",
        sa.Column("decision_tier_description", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    """Remove decision tier columns from fraud_alerts table."""
    op.drop_column("fraud_alerts", "decision_tier_description")
    op.drop_column("fraud_alerts", "decision_tier")
