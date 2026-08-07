"""Развязка доступа: role = только уровень (admin/editor/user), sections = разделы

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-07

Раньше role смешивал уровень доступа и раздел (admin_kandas, viewer_cossu…).
Теперь:
  - role      — только уровень: admin / editor / viewer(=пользователь)
  - sections  — массив разделов, к которым есть доступ: population/kandas/cossu

Один аккаунт может видеть несколько разделов; при >1 на фронте появляется
линза-переключатель. admin неявно видит все разделы.

Бэкфилл существующих аккаунтов без потери доступа:
  admin                     -> role=admin,  sections={population,kandas,cossu}
  editor / viewer           -> как есть,     sections={population}
  admin_kandas/viewer_kandas-> editor/viewer, sections={kandas}
  admin_cossu /viewer_cossu -> editor/viewer, sections={cossu}
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sections", ARRAY(sa.String), nullable=True))

    # Бэкфилл разделов из старой роли
    op.execute("UPDATE users SET sections = ARRAY['population'] WHERE role IN ('admin','editor','viewer')")
    op.execute("UPDATE users SET sections = ARRAY['population','kandas','cossu'] WHERE role = 'admin'")
    op.execute("UPDATE users SET sections = ARRAY['kandas'] WHERE role IN ('admin_kandas','viewer_kandas')")
    op.execute("UPDATE users SET sections = ARRAY['cossu']  WHERE role IN ('admin_cossu','viewer_cossu')")

    # Схлопываем старые секционные роли в чистый уровень доступа
    op.execute("UPDATE users SET role = 'editor' WHERE role IN ('admin_kandas','admin_cossu')")
    op.execute("UPDATE users SET role = 'viewer' WHERE role IN ('viewer_kandas','viewer_cossu')")

    # Всем без раздела — хотя бы население
    op.execute("UPDATE users SET sections = ARRAY['population'] WHERE sections IS NULL")


def downgrade() -> None:
    # Обратно роли не восстанавливаем (данных для точного отката нет) — только колонка
    op.drop_column("users", "sections")
