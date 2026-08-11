"""Раздел residents (постоянные резиденты) отдельно от kandas

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-11

Кандасы и постоянные резиденты (kandas.kind='pmz') разделены на два раздела:
kandas и residents. Существующим аккаунтам, у которых уже есть доступ к kandas,
автоматически добавляем residents (чтобы не потеряли доступ к резидентам).
Только для уже созданных аккаунтов — новым доступ выдаётся вручную.
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # sections — varchar[]; сравниваем через text[]-каст, добавляем varchar-элемент.
    op.execute("""
        UPDATE users
           SET sections = array_append(sections, 'residents'::varchar)
         WHERE sections::text[] @> ARRAY['kandas']
           AND NOT (sections::text[] @> ARRAY['residents'])
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE users
           SET sections = array_remove(sections, 'residents'::varchar)
         WHERE sections::text[] @> ARRAY['residents']
    """)
