"""Население (pop_dwelling): точки в текущем окне карты (bbox) + сводка по регионам.

Карта не грузит все 1,12М точек — отдаём только попавшие в видимый bbox
(viewport-loading). Сводка по регионам — для сайдбара (агрегат по всем).
"""
from __future__ import annotations

import re

import orjson
from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.core.deps import require_section_edit
from app.db.models import User
from app.db.session import get_db

router = APIRouter(prefix="/pop", tags=["pop"])

CONF = {"высокая": "high", "средняя": "medium", "низкая": "low", "не_найдено": "miss"}
_NP = re.compile(r'(?:город(?:\s+\S+\s+значения)?|село|аул|пос[её]лок|станция|разъезд|кент)\s+([^,]+)', re.I)


def _city(addr: str | None) -> str | None:
    if not addr:
        return None
    m = _NP.search(addr)
    return m.group(1).strip() if m else None


def _resp(data) -> Response:
    return Response(content=orjson.dumps(data), media_type="application/json")


@router.get("")
async def points(
    w: float, s: float, e: float, n: float,
    limit: int = Query(15000, le=60000),
    db: AsyncSession = Depends(get_db),
):
    """Точки населения в bbox (w,s,e,n). Отдаём id, координаты, confidence,
    адрес и stats — их хватает фронту для маркеров, попапов и фильтров."""
    rows = (await db.execute(text("""
        SELECT dwelling_id, lat, lon, precision, geocode_addr, stats
          FROM pop_dwelling
         WHERE geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
           AND stats IS NOT NULL AND (stats->>'total')::int > 0
         LIMIT :lim
    """), {"w": w, "s": s, "e": e, "n": n, "lim": limit})).all()

    pts = [{
        "id": r[0], "lat": r[1], "lon": r[2],
        "confidence": CONF.get(r[3], "miss"),
        "street_name": r[4], "house": "",
        "city": _city(r[4]),
        "stats": r[5],
    } for r in rows]
    return _resp({"count": len(pts), "truncated": len(pts) >= limit, "points": pts})


class CoordsUpdate(BaseModel):
    lat: float
    lon: float
    reason: str | None = None


@router.patch("/{dwelling_id}/coords")
async def update_coords(
    dwelling_id: int,
    body: CoordsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_section_edit("population")),
):
    """Ручная правка координат жилища (pop_dwelling). Обновляем и geom —
    иначе точка не попадёт в новый bbox при перезагрузке окна карты.
    precision → «высокая» (проверено человеком), coord_source → «manual»."""
    row = (await db.execute(text("""
        UPDATE pop_dwelling
           SET lat = :lat, lon = :lon,
               geom = ST_SetSRID(ST_MakePoint(:lon, :lat), 4326),
               precision = 'высокая',
               coord_source = 'manual'
         WHERE dwelling_id = :id
     RETURNING dwelling_id, lat, lon, precision, coord_source
    """), {"id": dwelling_id, "lat": body.lat, "lon": body.lon})).first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Dwelling not found")
    await db.commit()
    return {
        "id": row[0], "lat": row[1], "lon": row[2],
        "confidence": CONF.get(row[3], "miss"), "source": row[4],
    }


# Категории, по которым можно суммировать людей на кружке (whitelist от SQL-инъекций)
CAT_KEYS = {
    "total", "male", "female", "trud_vozrast", "deti_do18", "working",
    "lsi", "asp", "student", "pensioners", "ip", "kandas",
    "mnogodetnyi", "woman_uhod_do3", "rt_unemployed",
    "foreigners", "uhod_inv", "cbd", "berem",
}


@router.get("/clusters")
async def clusters(
    w: float, s: float, e: float, n: float, zoom: float,
    cats: str | None = None,
    conf: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Агрегированные «кружки» населения в bbox для мелкого зума: точки
    группируются в сетку с ячейкой ~70/2^zoom градусов (≈пиксельная
    кластеризация). Возвращаем центроид ячейки + сумму людей.

    cats — список категорий через запятую (lsi,ip,…). Если задан, цифра на
    кружке = сумма этих категорий, и в выборку попадают только дома, где есть
    хотя бы один человек КАЖДОЙ выбранной категории (та же AND-логика, что у
    домов на крупном зуме).

    conf — список уровней уверенности (high,medium,low,miss). Если задан, кружки
    считают только дома с этой точностью (чтобы фильтр «Уверенность» работал и
    на мелком зуме, а не только на домах)."""
    cell = 70.0 / (2 ** zoom)
    sel = [c for c in (cats or "").split(",") if c in CAT_KEYS]
    if sel:
        sum_expr = " + ".join(f"(stats->>'{c}')::int" for c in sel)
        having = " AND ".join(f"(stats->>'{c}')::int > 0" for c in sel)
        people_sql = f"sum({sum_expr})"
        extra_where = f" AND {having}"
    else:
        people_sql = "sum((stats->>'total')::int)"
        extra_where = " AND (stats->>'total')::int > 0"

    # Фильтр уверенности (precision). Значения из фиксированного словаря —
    # инъекции нет. conf передан, но пусто → ничего не показываем.
    if conf is not None:
        precisions = [CONF_REV[c] for c in conf.split(",") if c in CONF_REV]
        if not precisions:
            return _resp([])
        plist = ",".join(f"'{p}'" for p in precisions)
        extra_where += f" AND precision IN ({plist})"

    rows = (await db.execute(text(f"""
        SELECT avg(lat) AS clat, avg(lon) AS clon,
               {people_sql} AS people, count(*) AS n
          FROM pop_dwelling
         WHERE geom && ST_MakeEnvelope(:w, :s, :e, :n, 4326)
           AND stats IS NOT NULL{extra_where}
         GROUP BY floor(lat / :cell), floor(lon / :cell)
        HAVING {people_sql} > 0
         ORDER BY people DESC
         LIMIT 3000
    """), {"w": w, "s": s, "e": e, "n": n, "cell": cell})).all()
    return _resp([{"lat": r[0], "lon": r[1], "people": int(r[2] or 0), "n": r[3]} for r in rows])


@router.get("/area")
async def area(level: str, id: int, db: AsyncSession = Depends(get_db)):
    """Предпосчитанная демография внутри границы (пространственно, ST_Contains).
    level=oblast → по id_reg, level=raion → по id_rai."""
    col = "id_rai" if level == "raion" else "id_reg"
    row = (await db.execute(text(
        f"SELECT name, n, stats FROM area_stats WHERE level=:lvl AND {col}=:id LIMIT 1"
    ), {"lvl": level, "id": id})).first()
    if not row:
        return _resp({"name": None, "n": 0, "stats": {}})
    return _resp({"name": row[0], "n": row[1], "stats": row[2]})


CONF_REV = {"high": "высокая", "medium": "средняя", "low": "низкая", "miss": "не_найдено"}
_regions_cache: list | None = None


@router.get("/regions")
async def pop_regions(kind: str = Query("house"), db: AsyncSession = Depends(get_db)):
    """Области → список районов (для фильтров списка адресов). Кэш."""
    global _regions_cache
    if _regions_cache is None:
        rows = (await db.execute(text("""
            SELECT stats->>'regname' AS regname,
                   array_agg(DISTINCT stats->>'rainame')
                     FILTER (WHERE stats->>'rainame' IS NOT NULL AND stats->>'rainame' <> '') AS rainames
              FROM pop_dwelling
             WHERE stats IS NOT NULL AND stats->>'regname' IS NOT NULL AND stats->>'regname' <> ''
             GROUP BY 1 ORDER BY 1
        """))).all()
        _regions_cache = [{"regname": r[0], "rainames": sorted(r[1] or [])} for r in rows]
    return _resp(_regions_cache)


@router.get("/list")
async def pop_list(
    kind: str = Query("house"),
    confidence: str | None = None,
    search: str | None = None,
    city: str | None = None,
    rainame: str | None = None,
    offset: int = 0,
    limit: int = Query(50, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Пагинированный список жилищ (для модалки «Список адресов»)."""
    where = ["stats IS NOT NULL", "(stats->>'total')::int > 0", "lat IS NOT NULL"]
    params: dict = {"offset": offset, "limit": limit}
    if confidence in CONF_REV:
        where.append("precision = :prec"); params["prec"] = CONF_REV[confidence]
    if search:
        where.append("geocode_addr ILIKE :q"); params["q"] = f"%{search}%"
    if city:
        where.append("stats->>'regname' = :city"); params["city"] = city
    if rainame:
        where.append("stats->>'rainame' = :rai"); params["rai"] = rainame
    w = " AND ".join(where)

    total = (await db.execute(text(f"SELECT count(*) FROM pop_dwelling WHERE {w}"), params)).scalar_one()
    rows = (await db.execute(text(f"""
        SELECT dwelling_id, geocode_addr, lat, lon, precision,
               (stats->>'total')::int AS total, stats->>'regname', stats->>'rainame'
          FROM pop_dwelling WHERE {w}
         ORDER BY geocode_addr OFFSET :offset LIMIT :limit
    """), params)).all()
    items = [{
        "id": r[0], "kind": "house", "city": r[6],
        "street_name": r[1], "house": "",
        "lat": r[2], "lon": r[3], "confidence": CONF.get(r[4], "miss"),
        "person_count": r[5], "regname": r[6], "rainame": r[7],
    } for r in rows]
    return _resp({"total": total, "items": items})


_summary_cache: dict | None = None


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """Сводка по областям (regname) для сайдбара: люди + число точек. Кэш."""
    global _summary_cache
    if _summary_cache is None:
        rows = (await db.execute(text("""
            SELECT stats->>'regname' AS regname,
                   sum((stats->>'total')::int) AS people,
                   count(*) AS dwellings
              FROM pop_dwelling
             WHERE stats IS NOT NULL AND (stats->>'total')::int > 0
             GROUP BY 1 ORDER BY 2 DESC NULLS LAST
        """))).all()
        regions = [{"regname": r[0], "people": int(r[1] or 0), "dwellings": r[2]} for r in rows]
        _summary_cache = {
            "total_people": sum(x["people"] for x in regions),
            "total_dwellings": sum(x["dwellings"] for x in regions),
            "regions": regions,
        }
    return _resp(_summary_cache)
