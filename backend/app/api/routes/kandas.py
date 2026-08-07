"""API для реестра кандасов."""
from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import undefer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_current_user
from app.db.models import Kandas, User, EDIT_ROLES, effective_sections

router = APIRouter(prefix="/kandas", tags=["kandas"])


# ── Схемы ────────────────────────────────────────────────────────────────────

class KandasOut(BaseModel):
    id:          int
    kind:        str = "kandas"
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
    has_photo:   bool = False

    class Config:
        from_attributes = True


class CoordsIn(BaseModel):
    lat: float
    lon: float


# ── Эндпоинты ────────────────────────────────────────────────────────────────

def _require_kandas_role(user: User = Depends(get_current_user)) -> User:
    """Доступ к разделу кандасов (любой уровень)."""
    if "kandas" not in effective_sections(user):
        raise HTTPException(403, "Access denied: no kandas section")
    return user


def _require_kandas_edit(user: User = Depends(_require_kandas_role)) -> User:
    """Правки кандасов: раздел kandas + уровень editor/admin."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    return user


@router.get("", response_model=list[KandasOut])
async def list_kandas(
    kind: str = Query("kandas", description="Тип реестра: kandas | pmz"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Список записей реестра нужного типа (только для ролей kandas).

    kind='kandas' — кандасы (по умолчанию), kind='pmz' — постоянные резиденты.
    """
    result = await db.execute(
        select(Kandas).where(Kandas.kind == kind).order_by(Kandas.id)
    )
    kandas_list = result.scalars().all()

    photo_result = await db.execute(
        select(Kandas.id).where(Kandas.photo != None, Kandas.kind == kind)
    )
    photo_ids = {row[0] for row in photo_result}

    return [
        KandasOut.model_validate(k).model_copy(update={"has_photo": k.id in photo_ids})
        for k in kandas_list
    ]


@router.get("/{kandas_id}", response_model=KandasOut)
async def get_kandas(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    photo_result = await db.execute(
        select(Kandas.id).where(Kandas.id == kandas_id, Kandas.photo != None)
    )
    has_photo = photo_result.scalar_one_or_none() is not None

    return KandasOut.model_validate(k).model_copy(update={"has_photo": has_photo})


@router.put("/{kandas_id}/coords")
async def set_coords(
    kandas_id: int,
    body: CoordsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Установить координаты кандаса вручную (admin_kandas)."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")

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
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")

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
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.work_lat = body.lat
    k.work_lon = body.lon
    await db.commit()
    return {"ok": True, "work_lat": k.work_lat, "work_lon": k.work_lon}


@router.get("/{kandas_id}/photo")
async def get_photo(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Получить фото кандаса."""
    result = await db.execute(
        select(Kandas).where(Kandas.id == kandas_id).options(undefer(Kandas.photo))
    )
    k = result.scalar_one_or_none()
    if not k or not k.photo:
        raise HTTPException(404, "No photo")
    return Response(content=k.photo, media_type="image/jpeg")


@router.post("/{kandas_id}/photo")
async def upload_photo(
    kandas_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Загрузить фото кандаса (только admin_kandas)."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")
    k.photo = await file.read()
    await db.commit()
    return {"ok": True}


@router.delete("/{kandas_id}/photo")
async def delete_photo(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Удалить фото кандаса (только admin_kandas)."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")
    k.photo = None
    await db.commit()
    return {"ok": True}


@router.delete("/{kandas_id}/work_coords")
async def clear_work_coords(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Сбросить координаты места работы (admin_kandas)."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")

    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")

    k.work_lat = k.work_lon = None
    await db.commit()
    return {"ok": True}
