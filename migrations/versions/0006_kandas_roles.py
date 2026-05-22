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
    # Роль хранится как VARCHAR(16) — новые значения admin_kandas/viewer_kandas
    # допустимы сразу, дополнительных изменений схемы БД не требуется.
    pass


def downgrade() -> None:
    pass
