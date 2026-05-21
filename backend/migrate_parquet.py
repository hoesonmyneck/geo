"""
Разовый скрипт: мигрирует astana_results.parquet → PostgreSQL.
Запускать ВНУТРИ контейнера:
  docker compose run --rm backend python migrate_parquet.py --parquet /app/data/ast_results.parquet

Что делает:
- Создаёт ImportSnapshot с source_file=parquet
- Для каждой уникальной (city, street, house) → Place
- Для каждой строки → Person
- Ручные правки НЕ затрагивает (source=edited/manual)
"""
from __future__ import annotations

import sys
import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path

import click
import psycopg
import pyarrow.parquet as pq

sys.path.insert(0, "/app")
from app.core.config import settings

logger = logging.getLogger("migrate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

DSN = (
    f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
    f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} "
    f"password={settings.POSTGRES_PASSWORD}"
)


async def migrate(parquet_path: Path) -> None:
    logger.info("Reading parquet: %s", parquet_path)
    table = pq.read_table(str(parquet_path))
    df = table.to_pydict()
    total = len(df["sicid"])
    logger.info("Total rows: %d", total)

    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        # Создаём snapshot
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO import_snapshot (source_file, region, kind, status, progress, total, finished_at)
                VALUES (%s, 'Astana', 'house', 'done', %s, %s, NOW())
                RETURNING id
            """, (str(parquet_path), total, total))
            snap_id = (await cur.fetchone())[0]
        await conn.commit()
        logger.info("Created snapshot id=%d", snap_id)

        # Адресный словарь для дедупликации
        addr_to_place_id: dict[tuple, int] = {}
        stats = defaultdict(int)
        written_persons = 0

        for i in range(total):
            sicid   = int(df["sicid"][i])
            lat     = df["lat"][i]
            lon     = df["lon"][i]
            conf    = str(df["confidence"][i] or "miss")
            city    = str(df.get("city", [""])[i] or "Астана")
            street  = str(df.get("street_used", [""])[i] or df.get("original_street", [""])[i] or "")
            house   = str(df.get("house_used", [""])[i] or "")
            source  = str(df.get("source", ["geocoded"])[i] or "geocoded")
            osm_id  = str(df.get("raw_osm_id", [""])[i] or "")

            # Атрибуты персоны
            def _col(name, default=0):
                col = df.get(name)
                if col is None:
                    return default
                v = col[i]
                if v is None:
                    return default
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return default

            def _scol(name, default=""):
                col = df.get(name)
                if col is None:
                    return default
                v = col[i]
                return str(v) if v is not None else default

            addr_key = (city, street, house)

            if addr_key not in addr_to_place_id:
                async with conn.cursor() as cur:
                    geom_expr = f"ST_SetSRID(ST_MakePoint({lon}, {lat}), 4326)" if lat is not None and lon is not None else "NULL::geometry"
                    await cur.execute(f"""
                        INSERT INTO place
                            (kind, city, street_name, house, lat, lon, geom, confidence, source, geocoded_at)
                        VALUES ('house', %s, %s, %s, %s, %s,
                                {geom_expr},
                                %s, %s, NOW())
                        ON CONFLICT (kind, city, street_name, house) DO UPDATE
                            SET lat=EXCLUDED.lat, lon=EXCLUDED.lon, geom=EXCLUDED.geom,
                                confidence=EXCLUDED.confidence
                        RETURNING id
                    """, (city, street, house, lat, lon, conf, source))
                    place_id = (await cur.fetchone())[0]
                addr_to_place_id[addr_key] = place_id
            else:
                place_id = addr_to_place_id[addr_key]

            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO person
                        (sicid, place_id, snapshot_id, gender_id, vozrast, trud_vozrast,
                         deti_do18, working, lsi, asp, student, pensioners, ip, kandas, corpus, rainame)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    sicid, place_id, snap_id,
                    _col("gender_id"), _col("vozrast"), _col("trud_vozrast"),
                    _col("deti_do18"), _col("working"), _col("lsi"),
                    _col("asp"), _col("student"), _col("pensioners"),
                    _col("ip"), _col("kandas"),
                    _scol("corpus"), _scol("rainame"),
                ))
            stats[conf] += 1
            written_persons += 1

            if written_persons % 2000 == 0:
                await conn.commit()
                logger.info("Progress: %d/%d", written_persons, total)

        await conn.commit()

        # Обновляем stats snapshot
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE import_snapshot SET stats=%s::jsonb WHERE id=%s",
                (json.dumps(dict(stats)), snap_id),
            )
        await conn.commit()

    logger.info(
        "Done. Places: %d, Persons: %d. Stats: %s",
        len(addr_to_place_id), written_persons, dict(stats),
    )


@click.command()
@click.option("--parquet", required=True, type=click.Path(exists=True, path_type=Path))
def main(parquet: Path) -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(migrate(parquet))


if __name__ == "__main__":
    main()
