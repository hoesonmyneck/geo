"""API для реестра кандасов."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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

def _kind_section(kind: str) -> str:
    """kandas.kind → раздел доступа: pmz → residents, иначе → kandas."""
    return "residents" if kind == "pmz" else "kandas"


def _require_kandas_role(user: User = Depends(get_current_user)) -> User:
    """Доступ к модулю кандасов/резидентов (хотя бы один из разделов)."""
    if not ({"kandas", "residents"} & effective_sections(user)):
        raise HTTPException(403, "Access denied: no kandas/residents section")
    return user


async def _load_kandas(kandas_id: int, db: AsyncSession, user: User, *, edit: bool = False) -> Kandas:
    """Загрузить запись и проверить доступ к её разделу (по kind) + при edit — уровень."""
    k = await db.get(Kandas, kandas_id)
    if not k:
        raise HTTPException(404, "Not found")
    if _kind_section(k.kind) not in effective_sections(user):
        raise HTTPException(403, "No access to this record's section")
    if edit and user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    return k


@router.get("", response_model=list[KandasOut])
async def list_kandas(
    kind: str = Query("kandas", description="Тип реестра: kandas | pmz"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Список записей реестра нужного типа.

    kind='kandas' — кандасы (раздел kandas), kind='pmz' — постоянные резиденты
    (раздел residents). Проверяем доступ к соответствующему разделу.
    """
    if _kind_section(kind) not in effective_sections(user):
        raise HTTPException(403, f"No access to section '{_kind_section(kind)}'")
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
    k = await _load_kandas(kandas_id, db, user)

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
    k = await _load_kandas(kandas_id, db, user, edit=True)

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
    k = await _load_kandas(kandas_id, db, user, edit=True)

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
    k = await _load_kandas(kandas_id, db, user, edit=True)

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
    if _kind_section(k.kind) not in effective_sections(user):
        raise HTTPException(403, "No access to this record's section")
    return Response(content=k.photo, media_type="image/jpeg")


@router.post("/{kandas_id}/photo")
async def upload_photo(
    kandas_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Загрузить фото кандаса/резидента (editor+)."""
    k = await _load_kandas(kandas_id, db, user, edit=True)
    k.photo = await file.read()
    await db.commit()
    return {"ok": True}


@router.delete("/{kandas_id}/photo")
async def delete_photo(
    kandas_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(_require_kandas_role),
):
    """Удалить фото кандаса/резидента (editor+)."""
    k = await _load_kandas(kandas_id, db, user, edit=True)
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
    k = await _load_kandas(kandas_id, db, user, edit=True)

    k.work_lat = k.work_lon = None
    await db.commit()
    return {"ok": True}


# ── Загрузка выгрузки от базистов (xlsx) ─────────────────────────────────────
# Раньше это делалось руками: scp файла на сервер + docker compose exec скрипта.
# Здесь то же самое, но кнопкой из интерфейса. Саму разборку xlsx НЕ дублируем —
# запускаем те же worker/seed_*.py, что и вручную, иначе логика разъедется.

SEEDERS = {
    "kandas": "/app/worker/seed_kandas_xlsx.py",
    "pmz":    "/app/worker/seed_pmz_xlsx.py",
}
# Колонки-маркеры: по ним отличаем выгрузку кандасов от выгрузки резидентов
# и заодно проверяем, что структура файла та, к которой привязан сидер.
REQUIRED_COLS = {
    "kandas": {"APP_ID", "IIN", "APPLICANT", "SURNAME", "FIRSTNAME"},
    "pmz":    {"IIN", "SURNAME", "FIRSTNAME", "BIRTHDATE", "PMZ_DATE"},
}


def _xlsx_header(path: str) -> set[str]:
    """Набор имён колонок из строки шапки (ищем её в первых 5 строках по IIN)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= 5:
                break
            names = {str(v).strip() for v in row if v is not None and str(v).strip()}
            if "IIN" in names:
                return names
    finally:
        wb.close()
    return set()


@router.post("/import")
async def import_registry(
    file: UploadFile = File(...),
    kind: str = Form(""),
    user: User = Depends(_require_kandas_role),
) -> dict:
    """Загрузить xlsx и обновить реестр. kind: kandas | pmz | '' (определить по файлу)."""
    if user.role not in EDIT_ROLES:
        raise HTTPException(403, "Editor access required")
    if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Нужен файл .xlsx")

    os.makedirs("/app/data/input", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(file.filename or "upload.xlsx"))
    dest = f"/app/data/input/import_{stamp}_{safe}"
    content = await file.read()
    if not content:
        raise HTTPException(400, "Файл пустой")
    with open(dest, "wb") as f:
        f.write(content)

    try:
        header = _xlsx_header(dest)
    except Exception as e:
        os.remove(dest)
        raise HTTPException(400, f"Не смог прочитать xlsx: {e}")
    if not header:
        os.remove(dest)
        raise HTTPException(400, "Не нашёл строку шапки с колонкой IIN")

    # Тип реестра определяем по маркерным колонкам, а не по имени файла
    detected = next((k for k, cols in REQUIRED_COLS.items() if cols <= header), None)
    if kind and kind not in SEEDERS:
        os.remove(dest)
        raise HTTPException(400, f"Неизвестный тип реестра: {kind}")
    if kind and detected and kind != detected:
        os.remove(dest)
        human = {"pmz": "резидентов", "kandas": "кандасов"}
        raise HTTPException(
            400,
            f"Похоже, это выгрузка «{human[detected]}», а загружаете в «{human[kind]}». "
            "Проверьте файл или раздел.",
        )
    target = kind or detected
    if not target:
        os.remove(dest)
        miss_k = sorted(REQUIRED_COLS["kandas"] - header)
        miss_p = sorted(REQUIRED_COLS["pmz"] - header)
        raise HTTPException(
            400,
            "Структура файла не совпадает ни с кандасами, ни с резидентами. "
            f"Не хватает колонок — для кандасов: {miss_k}; для резидентов: {miss_p}",
        )

    # Права на конкретный раздел (kandas / residents)
    if _kind_section(target) not in effective_sections(user):
        os.remove(dest)
        raise HTTPException(403, f"Нет доступа к разделу '{_kind_section(target)}'")

    proc = await asyncio.create_subprocess_exec(
        "python", SEEDERS[target], dest,
        cwd="/app",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "Импорт не уложился в 10 минут — файл слишком большой?")
    log = out.decode("utf-8", "replace")

    if proc.returncode != 0:
        raise HTTPException(500, "Импорт упал (код %d). Лог: %s" % (proc.returncode, log[-1500:]))

    m = re.search(r"Done:\s*(\d+)\s+inserted,\s*(\d+)\s+updated", log)
    inserted, updated = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    skipped = len(re.findall(r"нет главного кандаса", log))
    return {
        "ok": True,
        "kind": target,
        "inserted": inserted,
        "updated": updated,
        "skipped_groups": skipped,
        "file": os.path.basename(dest),
        "log": log[-4000:],
    }
