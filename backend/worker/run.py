"""
Worker: читает незакрытые ImportSnapshot и запускает геокодирующий pipeline.
Запускать: python -m worker.run [--snapshot-id N] [--concurrency 100]
Или через docker compose run worker
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import click
import httpx
import psycopg
from tqdm.asyncio import tqdm

# ── Чтобы импорты из app/ работали (PATH = /app внутри контейнера)
sys.path.insert(0, "/app")

from app.core.config import settings
from app.db.models import ImportSnapshot, Person, Place, PlaceKind, CoordSource

logger = logging.getLogger("worker")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

# Кэш геокодера (sqlite, опциональный)
CACHE_PATH = Path("/app/data/cache/geocoder.sqlite")


# ─── Утилиты для чтения xlsx (копия из src/pipeline.py) ─────────────────────

def _iter_xlsx(path: Path) -> Iterator[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    ws = wb.active
    header: list[str] = []
    for raw_row in ws.iter_rows(values_only=True):
        if not header:
            header = [str(c).strip() if c is not None else "" for c in raw_row]
            continue
        yield dict(zip(header, raw_row))
    wb.close()


def _int(v) -> int:
    try:
        return int(float(str(v))) if v is not None and str(v).strip() not in ("", "nan") else 0
    except (ValueError, TypeError):
        return 0


# ─── Основной цикл ───────────────────────────────────────────────────────────

async def process_snapshot(snap_id: int, concurrency: int) -> None:
    """Геокодирует один snapshot и записывает результаты в БД."""
    # psycopg async соединение (используем settings)
    dsn = (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )

    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        # Загружаем snapshot
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, source_file, kind FROM import_snapshot WHERE id = %s", (snap_id,)
            )
            row = await cur.fetchone()
            if not row:
                logger.error("Snapshot %d not found", snap_id)
                return
            _, source_file, kind_str = row

        logger.info("Processing snapshot %d: %s (kind=%s)", snap_id, source_file, kind_str)

        # Статус → running
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE import_snapshot SET status='running' WHERE id=%s", (snap_id,)
            )
        await conn.commit()

        # Читаем xlsx
        records_raw = list(_iter_xlsx(Path(source_file)))
        total = len(records_raw)

        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE import_snapshot SET total=%s WHERE id=%s", (total, snap_id)
            )
        await conn.commit()

        logger.info("Loaded %d rows from %s", total, source_file)

        # Ленивый импорт геокодера (он зависит от src/ но мы скопировали в worker/)
        # Используем оригинальный src/ через PYTHONPATH
        from app.geocoder.geocode import geocode, GeoResult
        from app.geocoder.normalize import normalize_row
        from app.geocoder.cache import make_hash

        # Дедупликация по адресу
        unique_map: dict[str, object] = {}
        sid_to_key: dict[int, str] = {}
        all_rows = []
        sid_attrs: dict[int, dict] = {}

        for i, row in enumerate(records_raw):
            # Используем реальный SICID из файла если есть, иначе порядковый номер
            raw_sid = row.get("SICID") or row.get("sicid") or row.get("SicId")
            sid = int(raw_sid) if raw_sid else (i + 1)
            if "KATO_REGNAME" in row:
                regname  = str(row.get("KATO_REGNAME") or "")
                rainame  = str(row.get("KATO_RAINAME") or "")
                street   = str(row.get("REG_ADDRESS_STREET") or "")
                building = str(row.get("REG_ADDRESS_BUILDING") or "")
                corpus   = str(row.get("REG_ADDRESS_CORPUS") or "").strip()
                if corpus:
                    building = f"{building} {corpus}".strip()
                sid_attrs[sid] = {
                    "corpus": corpus, "regname": regname, "rainame": rainame,
                    "gender_id": _int(row.get("GENDER_ID")),
                    "vozrast": _int(row.get("VOZRAST")),
                    "trud_vozrast": _int(row.get("TRUD_VOZRAST")),
                    "deti_do18": _int(row.get("DETI_DO18")),
                    "working": _int(row.get("WORKING")),
                    "lsi": _int(row.get("LSI")),
                    "asp": _int(row.get("ASP")),
                    "student": _int(row.get("STUDENT")),
                    "pensioners": _int(row.get("PENSIONERS")),
                    "ip": _int(row.get("IP")),
                    "kandas": _int(row.get("KANDAS")),
                }
            else:
                regname  = str(row.get("REGNAME") or "")
                rainame  = str(row.get("RAINAME") or "")
                street   = str(row.get("REG_ADDRESS_STREET") or "")
                building = str(row.get("REG_ADDRESS_BUILDING") or "")
                corpus   = ""
                sid_attrs[sid] = {"corpus": corpus, "regname": regname, "rainame": rainame}

            try:
                rec = normalize_row(sid, regname, rainame, street, building)
                key = make_hash(rec.street_name, rec.house, rec.city)
                sid_to_key[sid] = key
                if key not in unique_map:
                    unique_map[key] = rec
                all_rows.append((sid, rec))
            except Exception as exc:
                logger.warning("Row %d normalization error: %s", i, exc)

        unique_records = list(unique_map.values())
        logger.info("Unique addresses: %d (dedup %.1fx)", len(unique_records), total / max(len(unique_records), 1))

        # Геокодируем
        semaphore = asyncio.Semaphore(concurrency)
        geo_results: dict[str, GeoResult] = {}
        stats: dict[str, int] = defaultdict(int)
        progress = 0

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_connections=concurrency + 10, max_keepalive_connections=concurrency),
        ) as client:
            async def process_one(rec) -> tuple[str, GeoResult]:
                async with semaphore:
                    result = await geocode(rec, client)
                key = make_hash(rec.street_name, rec.house, rec.city)
                return key, result

            pbar = tqdm(total=len(unique_records), desc=f"Snap {snap_id}", unit="addr", dynamic_ncols=True)
            tasks = [asyncio.create_task(process_one(r)) for r in unique_records]
            for coro in asyncio.as_completed(tasks):
                try:
                    key, result = await coro
                except Exception as exc:
                    logger.warning("Geocode task failed: %s", exc)
                    pbar.update(1)
                    continue
                geo_results[key] = result
                stats[result.confidence] += 1
                progress += 1
                pbar.update(1)
                pbar.set_postfix({"high": stats["high"], "med": stats["medium"],
                                  "low": stats["low"], "miss": stats["miss"]})

                # Каждые 50 адресов обновляем прогресс в БД
                if progress % 50 == 0:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE import_snapshot SET progress=%s WHERE id=%s",
                            (progress, snap_id),
                        )
                    await conn.commit()
            pbar.close()

        # Для идемпотентности повторного запуска того же снапшота:
        # upsert по sicid сам перезапишет данные, дополнительно DELETE не нужен.
        # Однако если sicid — порядковый номер (нет колонки SICID в файле),
        # выполняем DELETE чтобы не копить строки с одинаковыми sid=1,2,3...
        _has_sicid_col = any(
            ("SICID" in r or "sicid" in r or "SicId" in r) for r in records_raw[:1]
        )
        if not _has_sicid_col:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM person WHERE snapshot_id = %s", (snap_id,))
                deleted = cur.rowcount
                if deleted:
                    logger.info(
                        "No SICID column — deleted %d old records for snapshot %d",
                        deleted, snap_id,
                    )
            await conn.commit()

        # Записываем place и person в БД
        logger.info("Writing results to PostgreSQL...")
        written_places = 0
        written_persons = 0

        for sid, rec in all_rows:
            key = sid_to_key.get(sid)
            if not key:
                continue
            geo = geo_results.get(key)
            if not geo:
                continue

            attrs = sid_attrs.get(sid, {})
            lat = geo.lat
            lon = geo.lon
            conf = geo.confidence

            # Upsert place (по адресу, не трогаем если source=edited/manual)
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO place (kind, city, street_name, house, lat, lon, geom, confidence, source, geocoded_at)
                    VALUES (%s, %s, %s, %s, %s, %s,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                            %s, 'geocoded', NOW())
                    ON CONFLICT (kind, city, street_name, house) DO UPDATE SET
                        lat         = CASE WHEN place.source IN ('edited','manual') THEN place.lat ELSE EXCLUDED.lat END,
                        lon         = CASE WHEN place.source IN ('edited','manual') THEN place.lon ELSE EXCLUDED.lon END,
                        geom        = CASE WHEN place.source IN ('edited','manual') THEN place.geom ELSE EXCLUDED.geom END,
                        confidence  = CASE WHEN place.source IN ('edited','manual') THEN place.confidence ELSE EXCLUDED.confidence END,
                        geocoded_at = EXCLUDED.geocoded_at
                    RETURNING id
                """, (
                    kind_str, geo.city, geo.street_used or rec.street_name, geo.house_used or rec.house,
                    lat, lon, lon, lat,  # ST_MakePoint(lon, lat)
                    conf,
                ))
                row = await cur.fetchone()
                if not row:
                    continue
                place_id = row[0]
                written_places += 1

            # Upsert person: если sicid уже есть — обновляем все поля (переезд, смена данных)
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO person
                        (sicid, place_id, snapshot_id, gender_id, vozrast, trud_vozrast,
                         deti_do18, working, lsi, asp, student, pensioners, ip, kandas,
                         corpus, regname, rainame)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sicid) DO UPDATE SET
                        place_id     = EXCLUDED.place_id,
                        snapshot_id  = EXCLUDED.snapshot_id,
                        gender_id    = EXCLUDED.gender_id,
                        vozrast      = EXCLUDED.vozrast,
                        trud_vozrast = EXCLUDED.trud_vozrast,
                        deti_do18    = EXCLUDED.deti_do18,
                        working      = EXCLUDED.working,
                        lsi          = EXCLUDED.lsi,
                        asp          = EXCLUDED.asp,
                        student      = EXCLUDED.student,
                        pensioners   = EXCLUDED.pensioners,
                        ip           = EXCLUDED.ip,
                        kandas       = EXCLUDED.kandas,
                        corpus       = EXCLUDED.corpus,
                        regname      = EXCLUDED.regname,
                        rainame      = EXCLUDED.rainame
                """, (
                    sid, place_id, snap_id,
                    attrs.get("gender_id"), attrs.get("vozrast"), attrs.get("trud_vozrast"),
                    attrs.get("deti_do18"), attrs.get("working"), attrs.get("lsi"),
                    attrs.get("asp"), attrs.get("student"), attrs.get("pensioners"),
                    attrs.get("ip"), attrs.get("kandas"),
                    attrs.get("corpus", ""), attrs.get("regname", ""), attrs.get("rainame", ""),
                ))
                written_persons += 1

            if written_persons % 1000 == 0:
                await conn.commit()

        await conn.commit()

        final_stats = dict(stats)
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE import_snapshot
                SET status='done', progress=%s, finished_at=NOW(),
                    stats=%s::jsonb
                WHERE id=%s
            """, (progress, str(final_stats).replace("'", '"'), snap_id))
        await conn.commit()

        logger.info(
            "Snapshot %d done. Places written: %d, Persons: %d. Stats: %s",
            snap_id, written_places, written_persons, final_stats,
        )

        # Обновляем агрегированную статистику в place.stats и пересобираем статический файл
        logger.info("Updating place.stats aggregates...")
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE place p SET stats = sub.s
                FROM (
                    SELECT place_id,
                        jsonb_build_object(
                            'total',       COUNT(*),
                            'male',        SUM(CASE WHEN gender_id=1 THEN 1 ELSE 0 END),
                            'female',      SUM(CASE WHEN gender_id=2 THEN 1 ELSE 0 END),
                            'trud_vozrast',SUM(COALESCE(trud_vozrast,0)),
                            'deti_do18',   SUM(COALESCE(deti_do18,0)),
                            'working',     SUM(COALESCE(working,0)),
                            'lsi',         SUM(COALESCE(lsi,0)),
                            'asp',         SUM(COALESCE(asp,0)),
                            'student',     SUM(COALESCE(student,0)),
                            'pensioners',  SUM(COALESCE(pensioners,0)),
                            'ip',          SUM(COALESCE(ip,0)),
                            'kandas',      SUM(COALESCE(kandas,0)),
                            'regname',     mode() WITHIN GROUP (ORDER BY regname),
                            'rainame',     mode() WITHIN GROUP (ORDER BY rainame)
                        ) AS s
                    FROM person
                    GROUP BY place_id
                ) sub
                WHERE p.id = sub.place_id
            """)
        await conn.commit()
        logger.info("place.stats updated.")

        try:
            from worker.gen_static import generate
            count = generate()
            logger.info("Static file regenerated: %d places.", count)
        except Exception as exc:
            logger.warning("gen_static failed (non-fatal): %s", exc)


async def run_pending(concurrency: int) -> None:
    """Обрабатывает все pending snapshot в очереди."""
    dsn = (
        f"host={settings.POSTGRES_HOST} port={settings.POSTGRES_PORT} "
        f"dbname={settings.POSTGRES_DB} user={settings.POSTGRES_USER} "
        f"password={settings.POSTGRES_PASSWORD}"
    )
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM import_snapshot WHERE status='pending' ORDER BY imported_at"
            )
            rows = await cur.fetchall()

    if not rows:
        logger.info("No pending snapshots.")
        return

    for (snap_id,) in rows:
        await process_snapshot(snap_id, concurrency)


async def run_daemon(concurrency: int, poll_interval: int = 10) -> None:
    """Бесконечный цикл: обрабатывает pending снапшоты, затем ждёт poll_interval секунд."""
    logger.info("Worker daemon started. Polling every %ds.", poll_interval)
    while True:
        try:
            await run_pending(concurrency)
        except Exception as exc:
            logger.error("Daemon iteration error: %s", exc)
        await asyncio.sleep(poll_interval)


@click.command()
@click.option("--snapshot-id", type=int, default=None, help="Конкретный snapshot_id")
@click.option("--concurrency", default=100, show_default=True)
@click.option("--daemon", is_flag=True, default=False, help="Запустить в режиме демона (бесконечный цикл)")
def main(snapshot_id: int | None, concurrency: int, daemon: bool) -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    if snapshot_id:
        asyncio.run(process_snapshot(snapshot_id, concurrency))
    elif daemon:
        asyncio.run(run_daemon(concurrency))
    else:
        asyncio.run(run_pending(concurrency))


if __name__ == "__main__":
    main()
