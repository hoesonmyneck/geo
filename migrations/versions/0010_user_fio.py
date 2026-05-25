"""Add fio column to users for displaying real name (from EDS cert)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("fio", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "fio")
