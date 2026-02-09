"""add_users_table

Revision ID: a1b2c3d4e5f6
Revises: f3a8b2c4d5e6
Create Date: 2026-02-07 10:00:00.000000
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f3a8b2c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pre-computed bcrypt hashes for demo seed users
SEED_USERS = [
    {
        "id": str(uuid4()),
        "username": "admin",
        "email": "admin@capitec.co.za",
        "hashed_password": "$2b$12$xLbWBxKgZ9Qp5PWE95MtWeruOHmuw1jE4r5YxRhT8v4im6E1RHbJ2",
        "full_name": "System Administrator",
        "role": "admin",
        "is_active": True,
    },
    {
        "id": str(uuid4()),
        "username": "analyst",
        "email": "analyst@capitec.co.za",
        "hashed_password": "$2b$12$lBbpStox7587rihT1Sfm6ONOeUNUHfJpXHv33ffv46tk1tZ0YpUa6",
        "full_name": "Fraud Analyst",
        "role": "analyst",
        "is_active": True,
    },
    {
        "id": str(uuid4()),
        "username": "viewer",
        "email": "viewer@capitec.co.za",
        "hashed_password": "$2b$12$YOlbalmYLaYetVucK.mAFur10YfO.01RoJ2Pa3bWOM86qBxmdnKTe",
        "full_name": "Dashboard Viewer",
        "role": "viewer",
        "is_active": True,
    },
]


def upgrade() -> None:
    """Create users table and seed demo accounts."""
    users_table = op.create_table(
        "users",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    # Seed demo users
    op.bulk_insert(users_table, SEED_USERS)


def downgrade() -> None:
    """Drop users table."""
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
