"""add_system_config

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-09 12:00:00.000000

Adds system_config table for persistent key-value configuration
(auto-escalation threshold, data retention days).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_config",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Seed default configuration
    op.execute(
        "INSERT INTO system_config (key, value, description) VALUES "
        "('auto_escalation_threshold', '90', 'Risk score above which alerts auto-escalate'), "
        "('data_retention_days', '90', 'Days to retain reviewed alerts before archiving')"
    )


def downgrade() -> None:
    op.drop_table("system_config")
