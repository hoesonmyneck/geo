"""Add regname (KATO_REGNAME) column to person table

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-21
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("person", sa.Column("regname", sa.String(128), nullable=True))
    op.create_index("ix_person_regname", "person", ["regname"])


def downgrade() -> None:
    op.drop_index("ix_person_regname", table_name="person")
    op.drop_column("person", "regname")
