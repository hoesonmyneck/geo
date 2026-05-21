"""Import API: загрузка xlsx, статус прогресса."""
import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_admin
from app.db.models import ImportSnapshot, PlaceKind, User
from app.db.session import get_db

logger = logging.getLogger("imports")

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("", dependencies=[Depends(require_admin)], status_code=202)
async def start_import(
    file: UploadFile = File(...),
    kind: str = Form("house"),
    region: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Сохраняет файл и создаёт задачу в import_snapshot.
    Реальный геокодинг выполняется worker-процессом, который читает snapshot.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only xlsx files supported")

    # Сохраняем файл в /app/data/input
    input_dir = "/app/data/input"
    os.makedirs(input_dir, exist_ok=True)
    dest = os.path.join(input_dir, file.filename)
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    snap = ImportSnapshot(
        source_file=dest,
        region=region or None,
        kind=PlaceKind(kind),
        status="pending",
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return {"snapshot_id": snap.id, "status": snap.status}


@router.get("")
async def list_imports(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[dict]:
    result = await db.execute(
        select(ImportSnapshot).order_by(ImportSnapshot.imported_at.desc()).limit(50)
    )
    return [
        {
            "id": s.id,
            "source_file": os.path.basename(s.source_file),
            "region": s.region,
            "kind": s.kind,
            "status": s.status,
            "progress": s.progress,
            "total": s.total,
            "stats": s.stats,
            "imported_at": s.imported_at.isoformat() if s.imported_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        }
        for s in result.scalars()
    ]


@router.post("/districts/refresh", dependencies=[Depends(require_admin)])
async def refresh_districts(background_tasks: BackgroundTasks) -> dict:
    """Скачивает административные границы Казахстана из Overpass и сохраняет локально."""
    def _run():
        try:
            from worker.fetch_districts import generate
            count = generate()
            logger.info("Districts refreshed: %d features", count)
        except Exception as exc:
            logger.error("Districts refresh failed: %s", exc)
    background_tasks.add_task(_run)
    return {"status": "started", "message": "Загрузка районов запущена в фоне (~1-2 мин)"}


@router.get("/{snapshot_id}")
async def get_import(
    snapshot_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    snap = await db.get(ImportSnapshot, snapshot_id)
    if not snap:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {
        "id": snap.id,
        "source_file": os.path.basename(snap.source_file),
        "region": snap.region,
        "kind": snap.kind,
        "status": snap.status,
        "progress": snap.progress,
        "total": snap.total,
        "stats": snap.stats,
        "imported_at": snap.imported_at.isoformat() if snap.imported_at else None,
        "finished_at": snap.finished_at.isoformat() if snap.finished_at else None,
    }
