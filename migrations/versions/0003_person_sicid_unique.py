"""Add UNIQUE constraint on person.sicid for deduplication on re-upload

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-18
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Удаляем дубли по sicid, оставляем запись с наибольшим id (последняя загрузка)
    op.execute("""
        DELETE FROM person p1
        USING person p2
        WHERE p1.sicid = p2.sicid
          AND p1.id < p2.id
    """)

    # Добавляем уникальный индекс
    op.create_index("uq_person_sicid", "person", ["sicid"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_person_sicid", table_name="person")
