"""Initial schema: users, place, person, import_snapshot, edit_log

Revision ID: 0001
Revises:
Create Date: 2026-05-18
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geometry

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostGIS extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="viewer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_users_login", "users", ["login"])

    # import_snapshot (создаём раньше place, т.к. person ссылается на оба)
    op.create_table(
        "import_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_file", sa.String(256), nullable=False),
        sa.Column("region", sa.String(128), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="house"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("imported_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("imported_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )

    # place — универсальный гео-объект
    op.create_table(
        "place",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="house"),
        sa.Column("city", sa.String(128), nullable=False),
        sa.Column("street_name", sa.String(256), nullable=False),
        sa.Column("house", sa.String(32), nullable=True),
        sa.Column("name", sa.String(256), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("geom", Geometry(geometry_type="POINT", srid=4326), nullable=True),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="miss"),
        sa.Column("source", sa.String(16), nullable=False, server_default="geocoded"),
        sa.Column("edited_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("geocoded_at", sa.DateTime(), nullable=True),
        sa.Column("attrs", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_place_kind", "place", ["kind"])
    op.create_index("ix_place_city", "place", ["city"])
    op.create_index("ix_place_confidence", "place", ["confidence"])
    op.create_index("ix_place_geom", "place", ["geom"], postgresql_using="gist")
    op.create_unique_constraint(
        "uq_place_address", "place", ["kind", "city", "street_name", "house"]
    )

    # person
    op.create_table(
        "person",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("sicid", sa.BigInteger(), nullable=False),
        sa.Column("place_id", sa.BigInteger(), sa.ForeignKey("place.id"), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("import_snapshot.id"), nullable=False),
        sa.Column("gender_id", sa.SmallInteger(), nullable=True),
        sa.Column("vozrast", sa.SmallInteger(), nullable=True),
        sa.Column("trud_vozrast", sa.SmallInteger(), nullable=True),
        sa.Column("deti_do18", sa.SmallInteger(), nullable=True),
        sa.Column("working", sa.SmallInteger(), nullable=True),
        sa.Column("lsi", sa.SmallInteger(), nullable=True),
        sa.Column("asp", sa.SmallInteger(), nullable=True),
        sa.Column("student", sa.SmallInteger(), nullable=True),
        sa.Column("pensioners", sa.SmallInteger(), nullable=True),
        sa.Column("ip", sa.SmallInteger(), nullable=True),
        sa.Column("kandas", sa.SmallInteger(), nullable=True),
        sa.Column("corpus", sa.String(32), nullable=True),
        sa.Column("rainame", sa.String(128), nullable=True),
    )
    op.create_index("ix_person_place_id", "person", ["place_id"])
    op.create_index("ix_person_sicid", "person", ["sicid"])
    op.create_index("ix_person_snapshot_id", "person", ["snapshot_id"])

    # edit_log
    op.create_table(
        "edit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("place_id", sa.BigInteger(), sa.ForeignKey("place.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("old_lat", sa.Float(), nullable=True),
        sa.Column("old_lon", sa.Float(), nullable=True),
        sa.Column("new_lat", sa.Float(), nullable=False),
        sa.Column("new_lon", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_edit_log_place_id", "edit_log", ["place_id"])


def downgrade() -> None:
    op.drop_table("edit_log")
    op.drop_table("person")
    op.drop_table("place")
    op.drop_table("import_snapshot")
    op.drop_table("users")
