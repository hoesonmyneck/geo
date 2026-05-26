"""Create cossu table for centers of social services to the population

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-26

Каждая строка в cossu = одно отделение (otd_name). Несколько строк с одинаковым
org_bin объединяются в одно учреждение на фронте (вкладки).
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cossu",
        sa.Column("id",                    sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column("branch_id",             sa.String(32),    nullable=True),   # внешний branch_ids из xlsx
        sa.Column("org_bin",               sa.String(32),    nullable=False, index=True),  # БИН учреждения (общий ключ)
        sa.Column("org_name",              sa.Text(),        nullable=True),
        sa.Column("sobst",                 sa.String(64),    nullable=True),   # форма собственности
        sa.Column("region",                sa.String(128),   nullable=True, index=True),
        sa.Column("kato_region",           sa.String(16),    nullable=True),
        sa.Column("rayon",                 sa.String(128),   nullable=True),
        sa.Column("kato_rayon",            sa.String(16),    nullable=True),
        sa.Column("rayon2",                sa.String(128),   nullable=True),
        sa.Column("kato_rayon2",           sa.String(16),    nullable=True),
        sa.Column("additional_address",    sa.Text(),        nullable=True),
        sa.Column("fulladdress",           sa.Text(),        nullable=True),
        sa.Column("otd_name",              sa.Text(),        nullable=True),   # название отделения
        sa.Column("otd_typ",               sa.String(256),   nullable=True),   # стационар/полустационар/...
        sa.Column("otd_podtyp",            sa.String(512),   nullable=True),   # подтип отделения
        sa.Column("fakt_koika_mesto",      sa.Integer(),     nullable=True),
        sa.Column("residents_count",       sa.Integer(),     nullable=True),
        sa.Column("queue_count",           sa.Integer(),     nullable=True),
        # координаты задаются позже (пока вручную или будущий геокодинг)
        sa.Column("lat",                   sa.Float(),       nullable=True),
        sa.Column("lon",                   sa.Float(),       nullable=True),
        sa.Column("coord_source",          sa.String(16),    nullable=True, server_default="none"),
        sa.Column("edited_at",             sa.DateTime(),    nullable=True),
        sa.Column("created_at",            sa.DateTime(),    server_default=sa.text("now()")),
    )
    # branch_id уникален в рамках всего набора (внешний ID)
    op.create_index("ix_cossu_branch_id", "cossu", ["branch_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cossu_branch_id", "cossu")
    op.drop_table("cossu")
