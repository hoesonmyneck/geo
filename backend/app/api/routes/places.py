"""Places API: чтение по bbox, список, детальная карточка, редактирование координат."""
import asyncio
from datetime import datetime, timezone

import orjson
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, require_editor
from app.db.models import EditLog, Place, User, KANDAS_ROLES
from app.db.session import get_db


def _regen_static() -> None:
    """Запускается в фоновом потоке после редактирования координат."""
    try:
        from worker.gen_static import generate
        generate()
    except Exception as exc:
        import logging
        logging.getLogger("places").warning("gen_static failed: %s", exc)

router = APIRouter(prefix="/places", tags=["places"])


def orjson_response(data) -> Response:
    return Response(content=orjson.dumps(data), media_type="application/json")


# ─── Схемы ───────────────────────────────────────────────────────────────────

class PersonStats(BaseModel):
    total: int
    male: int
    female: int
    trud_vozrast: int
    deti_do18: int
    working: int
    lsi: int
    asp: int
    student: int
    pensioners: int
    ip: int
    kandas: int


class PlaceOut(BaseModel):
    id: int
    kind: str
    city: str
    street_name: str
    house: str | None
    name: str | None
    lat: float | None
    lon: float | None
    confidence: str
    source: str
    stats: PersonStats | None = None
    regname: str | None = None  # KATO_REGNAME
    rainame: str | None = None  # KATO_RAINAME

    model_config = {"from_attributes": True}


class PlaceListItem(BaseModel):
    id: int
    kind: str
    city: str
    street_name: str
    house: str | None
    lat: float | None
    lon: float | None
    confidence: str
    person_count: int
    regname: str | None = None  # KATO_REGNAME (область/город респ. значения)
    rainame: str | None = None  # KATO_RAINAME (район)


class CoordsUpdate(BaseModel):
    lat: float
    lon: float
    reason: str | None = None


# ─── GET /api/places — bbox + агрегация одним запросом ───────────────────────

@router.get("")
async def get_places_bbox(
    min_lat: float = Query(...),
    min_lon: float = Query(...),
    max_lat: float = Query(...),
    max_lon: float = Query(...),
    kind: str = Query("house"),
    confidence: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """
    Возвращает объекты внутри bbox с агрегированной демографией.
    Один SQL-запрос с LEFT JOIN + GROUP BY — без N+1.
    """
    if current_user.role in KANDAS_ROLES:
        from fastapi import Response as FR
        return FR(content=b'{"features":[]}', media_type="application/json")
    # Строим агрегирующий запрос через text() для гибкости
    where_parts = [
        "kind = :kind",
        "lat >= :min_lat AND lat <= :max_lat",
        "lon >= :min_lon AND lon <= :max_lon",
        "lat IS NOT NULL",
        "stats IS NOT NULL",
        "(stats->>'total')::int > 0",
    ]
    params: dict = {
        "kind": kind,
        "min_lat": min_lat, "max_lat": max_lat,
        "min_lon": min_lon, "max_lon": max_lon,
    }
    if confidence:
        where_parts.append("confidence = :confidence")
        params["confidence"] = confidence

    # stats::text — PostgreSQL отдаёт JSONB как строку, orjson вставляет её как raw JSON.
    # Это исключает двойную сериализацию/десериализацию Python dict.
    sql = text(f"""
        SELECT id, city, street_name, house,
               lat, lon, confidence,
               stats::text AS stats_raw
        FROM place
        WHERE {' AND '.join(where_parts)}
        ORDER BY id
        LIMIT 100000
    """)

    result = await db.execute(sql, params)
    rows = result.fetchall()

    # Собираем JSON вручную: stats_raw вставляем без парсинга
    parts = []
    for r in rows:
        stats_raw = r[7] or "null"
        parts.append(
            b'{"id":' + orjson.dumps(r[0]) +
            b',"city":' + orjson.dumps(r[1]) +
            b',"street_name":' + orjson.dumps(r[2]) +
            b',"house":' + orjson.dumps(r[3]) +
            b',"lat":' + orjson.dumps(r[4]) +
            b',"lon":' + orjson.dumps(r[5]) +
            b',"confidence":' + orjson.dumps(r[6]) +
            b',"stats":' + stats_raw.encode() +
            b'}'
        )
    body = b'[' + b','.join(parts) + b']'
    return Response(content=body, media_type="application/json")


# ─── GET /api/places/list — полный список для попапа ─────────────────────────

@router.get("/regions")
async def get_regions(
    kind: str = Query("house"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    """Возвращает список областей (KATO_REGNAME) с уникальными районами (KATO_RAINAME)."""
    sql = text("""
        SELECT stats->>'regname' AS regname,
               ARRAY_AGG(DISTINCT stats->>'rainame' ORDER BY stats->>'rainame') AS rainames
        FROM place
        WHERE kind = :kind
          AND stats IS NOT NULL
          AND stats->>'regname' IS NOT NULL
          AND stats->>'regname' != ''
        GROUP BY stats->>'regname'
        ORDER BY stats->>'regname'
    """)
    rows = (await db.execute(sql, {"kind": kind})).fetchall()
    return [
        {"regname": r.regname, "rainames": [x for x in r.rainames if x]}
        for r in rows
        if r.regname
    ]


@router.get("/list")
async def get_places_list(
    kind: str = Query("house"),
    confidence: str | None = Query(None),
    search: str | None = Query(None),
    city: str | None = Query(None),
    rainame: str | None = Query(None),
    offset: int = Query(0),
    limit: int = Query(100, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    where_parts = ["p.kind = :kind"]
    params: dict = {"kind": kind, "offset": offset, "limit": limit}

    if confidence:
        where_parts.append("p.confidence = :confidence")
        params["confidence"] = confidence
    if search:
        where_parts.append("(LOWER(p.street_name) LIKE :search OR LOWER(p.house) LIKE :search)")
        params["search"] = f"%{search.lower()}%"
    if city:
        where_parts.append("p.stats->>'regname' = :city")
        params["city"] = city
    if rainame:
        where_parts.append("p.stats->>'rainame' = :rainame")
        params["rainame"] = rainame

    where_sql = " AND ".join(where_parts)

    count_sql = text(f"SELECT COUNT(*) FROM place p WHERE {where_sql}")
    total = (await db.execute(count_sql, params)).scalar_one()

    items_sql = text(f"""
        SELECT p.id, p.kind, p.city, p.street_name, p.house, p.lat, p.lon, p.confidence,
               COUNT(per.id) AS person_count,
               p.stats->>'regname' AS regname,
               p.stats->>'rainame' AS rainame
        FROM place p
        LEFT JOIN person per ON per.place_id = p.id
        WHERE {where_sql}
        GROUP BY p.id
        ORDER BY p.street_name, p.house
        OFFSET :offset LIMIT :limit
    """)
    rows = (await db.execute(items_sql, params)).fetchall()

    return {
        "total": total,
        "items": [
            PlaceListItem(
                id=r.id, kind=r.kind, city=r.city,
                street_name=r.street_name, house=r.house,
                lat=r.lat, lon=r.lon,
                confidence=r.confidence,
                person_count=int(r.person_count or 0),
                regname=r.regname or None,
                rainame=r.rainame or None,
            )
            for r in rows
        ],
    }


# ─── GET /api/places/{id} — детальная карточка ───────────────────────────────

@router.get("/{place_id}")
async def get_place(
    place_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> PlaceOut:
    sql = text("""
        SELECT id, kind, city, street_name, house, name, lat, lon, confidence, source, stats
        FROM place WHERE id = :id
    """)
    result = await db.execute(sql, {"id": place_id})
    r = result.fetchone()
    if not r:
        raise HTTPException(status_code=404, detail="Place not found")

    s = r.stats or {}
    stats = PersonStats(
        total=int(s.get('total', 0)), male=int(s.get('male', 0)), female=int(s.get('female', 0)),
        trud_vozrast=int(s.get('trud_vozrast', 0)), deti_do18=int(s.get('deti_do18', 0)),
        working=int(s.get('working', 0)), lsi=int(s.get('lsi', 0)), asp=int(s.get('asp', 0)),
        student=int(s.get('student', 0)), pensioners=int(s.get('pensioners', 0)),
        ip=int(s.get('ip', 0)), kandas=int(s.get('kandas', 0)),
    )
    return PlaceOut(
        id=r.id, kind=r.kind, city=r.city,
        street_name=r.street_name, house=r.house, name=r.name,
        lat=r.lat, lon=r.lon, confidence=r.confidence, source=r.source,
        stats=stats,
        regname=s.get('regname') or None,
        rainame=s.get('rainame') or None,
    )


# ─── PATCH /api/places/{id}/coords ───────────────────────────────────────────

@router.patch("/{place_id}/coords")
async def update_coords(
    place_id: int,
    body: CoordsUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_editor),
) -> PlaceOut:
    p = await db.get(Place, place_id)
    if not p:
        raise HTTPException(status_code=404, detail="Place not found")

    log = EditLog(
        place_id=place_id, user_id=current_user.id,
        old_lat=p.lat, old_lon=p.lon,
        new_lat=body.lat, new_lon=body.lon,
        reason=body.reason,
    )
    db.add(log)

    p.lat = body.lat
    p.lon = body.lon
    p.source = "edited"
    p.edited_by_user_id = current_user.id
    p.edited_at = datetime.now(timezone.utc)
    p.confidence = "high"

    await db.commit()
    await db.refresh(p)

    background_tasks.add_task(_regen_static)

    s = p.stats or {}
    stats = PersonStats(
        total=int(s.get('total', 0)), male=int(s.get('male', 0)), female=int(s.get('female', 0)),
        trud_vozrast=int(s.get('trud_vozrast', 0)), deti_do18=int(s.get('deti_do18', 0)),
        working=int(s.get('working', 0)), lsi=int(s.get('lsi', 0)), asp=int(s.get('asp', 0)),
        student=int(s.get('student', 0)), pensioners=int(s.get('pensioners', 0)),
        ip=int(s.get('ip', 0)), kandas=int(s.get('kandas', 0)),
    )
    return PlaceOut(
        id=p.id, kind=p.kind, city=p.city,
        street_name=p.street_name, house=p.house, name=p.name,
        lat=p.lat, lon=p.lon, confidence=p.confidence, source=p.source,
        stats=stats,
        regname=s.get('regname') or None,
        rainame=s.get('rainame') or None,
    )


# ─── GET /api/places/{id}/history ────────────────────────────────────────────

@router.get("/{place_id}/history")
async def get_history(
    place_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    sql = text("""
        SELECT el.id, u.login, el.old_lat, el.old_lon, el.new_lat, el.new_lon,
               el.reason, el.created_at
        FROM edit_log el
        JOIN users u ON u.id = el.user_id
        WHERE el.place_id = :id
        ORDER BY el.created_at DESC
    """)
    rows = (await db.execute(sql, {"id": place_id})).fetchall()
    return [
        {
            "id": r.id, "user": r.login,
            "old_lat": r.old_lat, "old_lon": r.old_lon,
            "new_lat": r.new_lat, "new_lon": r.new_lon,
            "reason": r.reason,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
