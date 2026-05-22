"""Add kandas user roles

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-22
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL позволяет добавлять значения в enum без пересоздания
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'admin_kandas'")
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'viewer_kandas'")


def downgrade() -> None:
    # Удаление значений из enum в PostgreSQL не поддерживается без пересоздания типа
    pass
