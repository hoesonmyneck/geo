"""Add kind discriminator to kandas (kandas | pmz)

Revision ID: 0013
Revises: 0011
Create Date: 2026-07-27

В реестр кандасов добавляется второй тип записей — обладатели статуса
постоянного резидента (pmz). Один переключатель на фронте показывает либо
кандасов, либо резидентов. Существующие строки — кандасы.

Зависит от 0011 (ЦОССУ), а НЕ от 0012 (building/person): добавление колонки
kind независимо от building/person-схемы, и это позволяет катить кандасов на
прод, не выкатывая туда незавершённую 0012.
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kandas",
        sa.Column("kind", sa.String(16), nullable=False, server_default="kandas"),
    )
    op.create_index("ix_kandas_kind", "kandas", ["kind"])


def downgrade() -> None:
    op.drop_index("ix_kandas_kind", table_name="kandas")
    op.drop_column("kandas", "kind")
