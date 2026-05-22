"""kandas table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kandas",
        sa.Column("id",           sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("fio",          sa.String(256),   nullable=False),
        sa.Column("iin",          sa.String(32),    nullable=True),
        sa.Column("dob",          sa.String(32),    nullable=True),   # дата рождения строкой
        sa.Column("age",          sa.SmallInteger(),nullable=True),
        sa.Column("citizenship",  sa.String(64),    nullable=True),   # страна
        sa.Column("gender",       sa.String(16),    nullable=True),
        # Адрес регистрации
        sa.Column("oblast",       sa.String(128),   nullable=True),
        sa.Column("raion",        sa.String(128),   nullable=True),
        sa.Column("city",         sa.String(128),   nullable=True),
        sa.Column("street",       sa.String(256),   nullable=True),
        sa.Column("house",        sa.String(32),    nullable=True),
        sa.Column("apt",          sa.String(32),    nullable=True),
        sa.Column("phone",        sa.String(64),    nullable=True),
        # Место работы / учёбы / льготы (любые доп данные)
        sa.Column("extra",        sa.JSON(),        nullable=True),
        # Координаты (выставляются вручную)
        sa.Column("lat",          sa.Float(),       nullable=True),
        sa.Column("lon",          sa.Float(),       nullable=True),
        sa.Column("coord_source", sa.String(16),    nullable=True, server_default="none"),
        sa.Column("edited_at",    sa.DateTime(),    nullable=True),
        sa.Column("created_at",   sa.DateTime(),    server_default=sa.text("now()")),
    )
    op.create_index("ix_kandas_iin", "kandas", ["iin"])


def downgrade() -> None:
    op.drop_index("ix_kandas_iin", "kandas")
    op.drop_table("kandas")
