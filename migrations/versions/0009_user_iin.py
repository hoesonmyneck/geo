"""Add iin column to users, make password_hash nullable for EDS auth

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("iin", sa.String(32), nullable=True))
    op.create_unique_constraint("uq_users_iin", "users", ["iin"])
    op.create_index("ix_users_iin", "users", ["iin"])
    op.alter_column("users", "password_hash", nullable=True)


def downgrade() -> None:
    op.alter_column("users", "password_hash", nullable=False)
    op.drop_index("ix_users_iin", "users")
    op.drop_constraint("uq_users_iin", "users", type_="unique")
    op.drop_column("users", "iin")
