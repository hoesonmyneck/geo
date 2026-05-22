"""API для реестра кандасов."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_current_user
from app.db.models import Kandas, User, UserRole

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

    class Config:
        from_attributes = True


class CoordsIn(BaseModel):
    lat: float
    lon: float


# ── Эндпоинты ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[KandasOut])
async def list_kandas(db: AsyncSession = Depends(get_db)):
    """Список всех кандасов."""
    result = await db.execute(select(Kandas).order_by(Kandas.id))
    return result.scalars().all()


@router.get("/{kandas_id}", response_model=KandasOut)
async def get_kandas(kandas_id: int, db: AsyncSession = Depends(get_db)):
    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")
    return k


@router.put("/{kandas_id}/coords")
async def set_coords(
    kandas_id: int,
    body: CoordsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Установить координаты кандаса вручную (editor/admin)."""
    if user.role == UserRole.viewer:
        raise HTTPException(403, "Viewers cannot edit")

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
    user: User = Depends(get_current_user),
):
    """Сбросить координаты кандаса."""
    if user.role == UserRole.viewer:
        raise HTTPException(403, "Viewers cannot edit")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.lat = k.lon = None
    k.coord_source = "none"
    k.edited_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True}
