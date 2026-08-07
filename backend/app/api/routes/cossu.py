"""API для ЦОССУ (Центры оказания специальных социальных услуг).

Каждая строка в `cossu` = одно отделение. На фронте группируются по `org_bin`.
"""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_current_user
from app.db.models import Cossu, User, EDIT_ROLES, effective_sections

router = APIRouter(prefix="/cossu", tags=["cossu"])


# ── Схемы ────────────────────────────────────────────────────────────────────

class CossuBranch(BaseModel):
    id:                 int
    branch_id:          str | None
    org_bin:            str
    org_name:           str | None
    sobst:              str | None
    region:             str | None
    rayon:              str | None
    rayon2:             str | None
    additional_address: str | None
    fulladdress:        str | None
    otd_name:           str | None
    otd_typ:            str | None
    otd_podtyp:         str | None
    fakt_koika_mesto:   int | None
    residents_count:    int | None
    queue_count:        int | None
    lat:                float | None
    lon:                float | None
    coord_source:       str | None
    edited_at:          datetime | None

    class Config:
        from_attributes = True


class CossuInstitution(BaseModel):
    """Учреждение — объединение всех отделений с одинаковым org_bin."""
    org_bin:    str
    org_name:   str | None        # имя учреждения (берём из первой строки)
    sobst:      str | None
    region:     str | None
    rayon:      str | None
    rayon2:     str | None
    fulladdress: str | None
    # Координаты учреждения (одинаковые у всех отделений)
    lat:        float | None
    lon:        float | None
    coord_source: str | None
    # Суммы по числовым полям
    total_fakt_koika_mesto: int
    total_residents_count:  int
    total_queue_count:      int
    # Перечисления (уникальные значения)
    types:      list[str]         # уникальные otd_typ
    subtypes:   list[str]         # уникальные otd_podtyp
    # Все отделения учреждения
    branches:   list[CossuBranch]


class CoordsIn(BaseModel):
    lat: float
    lon: float


# ── Эндпоинты ────────────────────────────────────────────────────────────────

def _require_cossu_role(user: User = Depends(get_current_user)) -> User:
    """Доступ к разделу ЦОССУ (любой уровень)."""
    if "cossu" not in effective_sections(user):
        raise HTTPException(403, "Access denied: no cossu section")
    return user


@router.get("", response_model=list[CossuInstitution])
async def list_cossu(
    db: AsyncSession = Depends(get_db),
):
    """Список учреждений с вложенными отделениями.
    Сортируем по region → rayon → org_name → org_bin для удобного просмотра.

    БЕЗ авторизации: раздел ЦОССУ доступен публично (карта). Кнопки статистики
    и списка на фронте скрыты для публичного режима, но данные точек открыты.
    """
    result = await db.execute(
        select(Cossu).order_by(Cossu.region, Cossu.rayon, Cossu.org_name, Cossu.org_bin, Cossu.id)
    )
    all_rows = result.scalars().all()

    # Группируем по org_bin, сохраняя порядок появления
    by_bin: dict[str, list[Cossu]] = {}
    for row in all_rows:
        by_bin.setdefault(row.org_bin, []).append(row)

    institutions: list[CossuInstitution] = []
    for bin_, rows in by_bin.items():
        first = rows[0]
        types    = list(dict.fromkeys(r.otd_typ    for r in rows if r.otd_typ))
        subtypes = list(dict.fromkeys(r.otd_podtyp for r in rows if r.otd_podtyp))
        institutions.append(CossuInstitution(
            org_bin=bin_,
            org_name=first.org_name,
            sobst=first.sobst,
            region=first.region,
            rayon=first.rayon,
            rayon2=first.rayon2,
            fulladdress=first.fulladdress,
            lat=first.lat,
            lon=first.lon,
            coord_source=first.coord_source,
            total_fakt_koika_mesto=sum(r.fakt_koika_mesto or 0 for r in rows),
            total_residents_count=sum(r.residents_count  or 0 for r in rows),
            total_queue_count=sum(r.queue_count     or 0 for r in rows),
            types=types,
            subtypes=subtypes,
            branches=[CossuBranch.model_validate(r) for r in rows],
        ))
    return institutions


@router.put("/by-bin/{org_bin}/coords")
async def set_institution_coords(
    org_bin: str,
    body: CoordsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_cossu_role),
):
    """Установить координаты ВСЕХ отделений учреждения с этим org_bin (admin_cossu).
    Все отделения находятся по одному адресу, поэтому координата одна на учреждение.
    """
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    now = datetime.now(timezone.utc)
    result = await db.execute(
        sa_update(Cossu)
        .where(Cossu.org_bin == org_bin)
        .values(lat=body.lat, lon=body.lon, coord_source="manual", edited_at=now)
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Institution not found")
    await db.commit()
    return {"ok": True, "lat": body.lat, "lon": body.lon, "updated_branches": result.rowcount}


@router.delete("/by-bin/{org_bin}/coords")
async def clear_institution_coords(
    org_bin: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_cossu_role),
):
    """Сбросить координаты у всех отделений учреждения (admin_cossu)."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    now = datetime.now(timezone.utc)
    result = await db.execute(
        sa_update(Cossu)
        .where(Cossu.org_bin == org_bin)
        .values(lat=None, lon=None, coord_source="none", edited_at=now)
    )
    if result.rowcount == 0:
        raise HTTPException(404, "Institution not found")
    await db.commit()
    return {"ok": True}
