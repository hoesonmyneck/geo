"""API для реестра кандасов."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_current_user
from app.db.models import Kandas, User, UserRole, KANDAS_ROLES

router = APIRouter(prefix="/kandas", tags=["kandas"])


# ── Схемы ────────────────────────────────────────────────────────────────────

class KandasOut(BaseModel):
    id:          int
    fio:         str
    iin:         str | None
    dob:         str | None
    age:         int | None
    citizenship: str | None
    gender:      str | None
    oblast:      str | None
    raion:       str | None
    city:        str | None
    street:      str | None
    house:       str | None
    apt:         str | None
    phone:       str | None
    extra:       dict | None
    lat:         float | None
    lon:         float | None
    coord_source: str | None
    edited_at:   datetime | None
    work_lat:    float | None
    work_lon:    float | None

    class Config:
        from_attributes = True


class CoordsIn(BaseModel):
    lat: float
    lon: float


# ── Эндпоинты ────────────────────────────────────────────────────────────────

def _require_kandas_role(user: User = Depends(get_current_user)) -> User:
    if user.role not in KANDAS_ROLES:
        raise HTTPException(403, "Access denied: kandas roles only")
    return user


@router.get("", response_model=list[KandasOut])
async def list_kandas(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Список всех кандасов (только для ролей kandas)."""
    result = await db.execute(select(Kandas).order_by(Kandas.id))
    return result.scalars().all()


@router.get("/{kandas_id}", response_model=KandasOut)
async def get_kandas(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")
    return k


@router.put("/{kandas_id}/coords")
async def set_coords(
    kandas_id: int,
    body: CoordsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Установить координаты кандаса вручную (admin_kandas)."""
    if user.role != UserRole.admin_kandas:
        raise HTTPException(403, "Only admin_kandas can edit")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.lat          = body.lat
    k.lon          = body.lon
    k.coord_source = "manual"
    k.edited_at    = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "lat": k.lat, "lon": k.lon}


@router.delete("/{kandas_id}/coords")
async def clear_coords(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Сбросить координаты кандаса (admin_kandas)."""
    if user.role != UserRole.admin_kandas:
        raise HTTPException(403, "Only admin_kandas can edit")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.lat = k.lon = None
    k.coord_source = "none"
    k.edited_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}


@router.put("/{kandas_id}/work_coords")
async def set_work_coords(
    kandas_id: int,
    body: CoordsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Установить координаты места работы (admin_kandas)."""
    if user.role != UserRole.admin_kandas:
        raise HTTPException(403, "Only admin_kandas can edit")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.work_lat = body.lat
    k.work_lon = body.lon
    await db.commit()
    return {"ok": True, "work_lat": k.work_lat, "work_lon": k.work_lon}


@router.delete("/{kandas_id}/work_coords")
async def clear_work_coords(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Сбросить координаты места работы (admin_kandas)."""
    if user.role != UserRole.admin_kandas:
        raise HTTPException(403, "Only admin_kandas can edit")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.work_lat = k.work_lon = None
    await db.commit()
    return {"ok": True}
