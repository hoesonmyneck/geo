"""SQLAlchemy ORM models.

Ключевые принципы:
- place  — универсальный географический объект (дом, школа, больница, …)
- person — запись из госбазы, привязана к place через place_id
- Когда редактор двигает точку → меняется только place.lat/lon (+ edit_log)
- Все person для этого place автоматически «переезжают»
"""
import enum
from datetime import datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey,
    Integer, SmallInteger, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─── Роли пользователей ──────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    viewer        = "viewer"
    editor        = "editor"
    admin         = "admin"
    admin_kandas  = "admin_kandas"   # видит только кандасов, может редактировать
    viewer_kandas = "viewer_kandas"  # видит только кандасов, только чтение

KANDAS_ROLES = {UserRole.admin_kandas, UserRole.viewer_kandas}


# ─── Тип географического объекта ─────────────────────────────────────────────

class PlaceKind(str, enum.Enum):
    house       = "house"
    school      = "school"
    hospital    = "hospital"
    kindergarten = "kindergarten"
    clinic      = "clinic"
    pharmacy    = "pharmacy"
    other       = "other"


# ─── Источник координаты ─────────────────────────────────────────────────────

class CoordSource(str, enum.Enum):
    geocoded = "geocoded"   # автоматически через Nominatim/Photon
    manual   = "manual"     # ручной ввод lat/lon
    edited   = "edited"     # редактор подвинул точку на карте
    osm      = "osm"        # прямо из OSM (для POI)


# ─── Пользователи ────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[UserRole] = mapped_column(String(16), nullable=False, default=UserRole.viewer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    edit_logs: Mapped[list["EditLog"]] = relationship(back_populates="user")


# ─── Географические объекты ──────────────────────────────────────────────────

class Place(Base):
    __tablename__ = "place"
    __table_args__ = (
        UniqueConstraint("kind", "city", "street_name", "house", name="uq_place_address"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[PlaceKind] = mapped_column(String(20), nullable=False, index=True, default=PlaceKind.house)

    # Адрес
    city: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    street_name: Mapped[str] = mapped_column(String(256), nullable=False)
    house: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)  # для школ/больниц

    # Координаты (PostGIS POINT + float для удобства)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    geom: Mapped[object] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326),
        nullable=True,
    )

    # Уверенность геокодера
    confidence: Mapped[str] = mapped_column(String(16), nullable=False, default="miss")
    source: Mapped[CoordSource] = mapped_column(String(16), nullable=False, default=CoordSource.geocoded)

    # Редактирование
    edited_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    geocoded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Доп. атрибуты (специфичные поля школы/больницы и т.п.)
    attrs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Денормализованная агрегация жителей (обновляется при импорте и редактировании)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    persons: Mapped[list["Person"]] = relationship(back_populates="place")
    edit_logs: Mapped[list["EditLog"]] = relationship(back_populates="place")


# ─── Записи из госбазы ───────────────────────────────────────────────────────

class Person(Base):
    __tablename__ = "person"
    __table_args__ = (
        UniqueConstraint("sicid", name="uq_person_sicid"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sicid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    place_id: Mapped[int] = mapped_column(ForeignKey("place.id"), nullable=False, index=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("import_snapshot.id"), nullable=False)

    # Демография
    gender_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    vozrast: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    trud_vozrast: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    deti_do18: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    working: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    lsi: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    asp: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    student: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    pensioners: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    ip: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    kandas: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    corpus: Mapped[str | None] = mapped_column(String(32), nullable=True)
    regname: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    rainame: Mapped[str | None] = mapped_column(String(128), nullable=True)

    place: Mapped["Place"] = relationship(back_populates="persons")
    snapshot: Mapped["ImportSnapshot"] = relationship(back_populates="persons")


# ─── Снэпшоты импорта ────────────────────────────────────────────────────────

class ImportSnapshot(Base):
    __tablename__ = "import_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_file: Mapped[str] = mapped_column(String(256), nullable=False)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[PlaceKind] = mapped_column(String(20), nullable=False, default=PlaceKind.house)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    imported_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    persons: Mapped[list["Person"]] = relationship(back_populates="snapshot")


# ─── Кандасы (отдельный реестр, координаты выставляются вручную) ─────────────

class Kandas(Base):
    __tablename__ = "kandas"

    id:           Mapped[int]        = mapped_column(Integer, primary_key=True, autoincrement=True)
    fio:          Mapped[str]        = mapped_column(String(256), nullable=False)
    iin:          Mapped[str | None] = mapped_column(String(32),  nullable=True,  index=True)
    dob:          Mapped[str | None] = mapped_column(String(32),  nullable=True)
    age:          Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    citizenship:  Mapped[str | None] = mapped_column(String(64),  nullable=True)
    gender:       Mapped[str | None] = mapped_column(String(16),  nullable=True)
    # Адрес регистрации
    oblast:  Mapped[str | None] = mapped_column(String(128), nullable=True)
    raion:   Mapped[str | None] = mapped_column(String(128), nullable=True)
    city:    Mapped[str | None] = mapped_column(String(128), nullable=True)
    street:  Mapped[str | None] = mapped_column(String(256), nullable=True)
    house:   Mapped[str | None] = mapped_column(String(32),  nullable=True)
    apt:     Mapped[str | None] = mapped_column(String(32),  nullable=True)
    phone:   Mapped[str | None] = mapped_column(String(64),  nullable=True)
    # Доп. данные (работа, учёба, льготы — всё в JSONB)
    extra:   Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Координаты
    lat:          Mapped[float | None] = mapped_column(Float,    nullable=True)
    lon:          Mapped[float | None] = mapped_column(Float,    nullable=True)
    coord_source: Mapped[str | None]   = mapped_column(String(16), nullable=True, default="none")
    edited_at:    Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at:   Mapped[datetime]        = mapped_column(DateTime, server_default=func.now())


# ─── Журнал правок координат ─────────────────────────────────────────────────

class EditLog(Base):
    __tablename__ = "edit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    place_id: Mapped[int] = mapped_column(ForeignKey("place.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    old_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    old_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_lat: Mapped[float] = mapped_column(Float, nullable=False)
    new_lon: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    place: Mapped["Place"] = relationship(back_populates="edit_logs")
    user: Mapped["User"] = relationship(back_populates="edit_logs")
