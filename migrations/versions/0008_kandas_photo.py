"""Add photo column to kandas

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-25
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kandas", sa.Column("photo", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("kandas", "photo")
