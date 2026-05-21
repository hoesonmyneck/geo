"""Add stats JSONB column to place for denormalized person aggregates

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-18
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Добавляем колонку stats в place
    op.add_column("place", sa.Column("stats", sa.dialects.postgresql.JSONB(), nullable=True))

    # Заполняем агрегированными данными из person
    op.execute("""
        UPDATE place p SET stats = sub.s
        FROM (
            SELECT
                place_id,
                jsonb_build_object(
                    'total',       COUNT(*),
                    'male',        SUM(CASE WHEN gender_id = 1 THEN 1 ELSE 0 END),
                    'female',      SUM(CASE WHEN gender_id = 2 THEN 1 ELSE 0 END),
                    'trud_vozrast',SUM(COALESCE(trud_vozrast, 0)),
                    'deti_do18',   SUM(COALESCE(deti_do18, 0)),
                    'working',     SUM(COALESCE(working, 0)),
                    'lsi',         SUM(COALESCE(lsi, 0)),
                    'asp',         SUM(COALESCE(asp, 0)),
                    'student',     SUM(COALESCE(student, 0)),
                    'pensioners',  SUM(COALESCE(pensioners, 0)),
                    'ip',          SUM(COALESCE(ip, 0)),
                    'kandas',      SUM(COALESCE(kandas, 0))
                ) AS s
            FROM person
            GROUP BY place_id
        ) sub
        WHERE p.id = sub.place_id
    """)


def downgrade() -> None:
    op.drop_column("place", "stats")
