"""
Геокодирует адреса учреждений ЦОССУ через 2GIS Catalog API.

Логика:
- Берём только записи где lat IS NULL.
- Дедуплицируем по org_bin (одно учреждение = один запрос в API).
- Найденные координаты записываются во ВСЕ строки этого org_bin
  (отделения одного учреждения находятся по одному адресу).
- Source = "2gis", чтобы потом видеть откуда взялись координаты.
- Между запросами пауза, чтобы не упереться в rate limit.
- Скрипт идемпотентен — при повторном запуске берёт только то что без координат.

Получение ключа:
    1. Зарегистрируйся на https://dev.2gis.ru
    2. Создай ключ для "Каталог" API (демо-доступ)
    3. Положи в переменную окружения TWOGIS_API_KEY

Запуск:
    docker compose exec -e TWOGIS_API_KEY=ТВОЙ_КЛЮЧ backend \\
        python /app/worker/geocode_cossu_2gis.py

Опции:
    --limit N     обработать только N учреждений (для тестов)
    --delay 0.3   пауза между запросами в секундах (по умолчанию 0.3 → ~3 запроса/сек)
    --dry-run     не сохранять в БД, только показать что нашлось
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

sys.path.insert(0, "/app")

import httpx
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.db.models import Cossu

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("geocode-cossu")

API_URL = "https://catalog.api.2gis.com/3.0/items/geocode"


async def geocode_one(client: httpx.AsyncClient, key: str, address: str) -> tuple[float, float] | None:
    """Один запрос в 2GIS Geocoder.
    Возвращает (lat, lon) или None если не найдено / ошибка / лимит.
    """
    params = {"q": address, "fields": "items.point", "key": key}
    for attempt in range(3):
        try:
            r = await client.get(API_URL, params=params, timeout=15.0)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning(f"  network: {e} (попытка {attempt + 1}/3)")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if r.status_code == 200:
            data = r.json()
            # 2GIS маскирует ошибки под HTTP 200 — реальный код в meta.code
            meta = data.get("meta") or {}
            mcode = meta.get("code")
            if mcode == 403:
                msg = ((meta.get("error") or {}).get("message")) or "forbidden"
                log.error(f"  meta 403: {msg}. Ключ невалиден/исчерпан. Останавливаюсь.")
                raise SystemExit(1)
            if mcode == 429:
                log.warning("  meta 429 Too Many Requests, жду 5 сек...")
                await asyncio.sleep(5)
                continue
            items = (data.get("result") or {}).get("items") or []
            if not items:
                return None
            point = items[0].get("point")
            if not point:
                return None
            return float(point["lat"]), float(point["lon"])

        if r.status_code == 403:
            log.error(f"  403 Forbidden: ключ невалидный или исчерпан лимит. Останавливаюсь.")
            raise SystemExit(1)

        if r.status_code == 429:
            log.warning(f"  429 Too Many Requests, жду 5 сек...")
            await asyncio.sleep(5)
            continue

        log.warning(f"  HTTP {r.status_code}: {r.text[:120]}")
        return None
    return None


def _build_query(row) -> str:
    """Собирает строку для геокодера. Предпочитаем полный адрес,
    но добавляем регион в начале если его там нет."""
    addr = (row.additional_address or "").strip()
    full = (row.fulladdress or "").strip()
    # fulladdress обычно длиннее и информативнее
    base = full if len(full) > len(addr) else addr
    region = (row.region or "").strip()
    if region and region.lower() not in base.lower():
        base = f"{region}, {base}"
    return base.strip(" ,")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit",   type=int,   default=None, help="макс. учреждений к обработке")
    parser.add_argument("--delay",   type=float, default=0.3,  help="пауза между запросами, сек")
    parser.add_argument("--dry-run", action="store_true",      help="не сохранять, только лог")
    args = parser.parse_args()

    key = os.environ.get("TWOGIS_API_KEY")
    if not key:
        log.error("Не задан TWOGIS_API_KEY")
        sys.exit(1)

    async with AsyncSessionLocal() as db:
        # Выбираем по одной строке на org_bin (DISTINCT ON) среди записей без координат
        # Использую SQL напрямую через text — DISTINCT ON это специфика PostgreSQL
        from sqlalchemy import text
        result = await db.execute(text("""
            SELECT DISTINCT ON (org_bin)
                   org_bin, fulladdress, additional_address, region
            FROM cossu
            WHERE lat IS NULL
            ORDER BY org_bin, id
        """))
        rows = result.all()

        if args.limit:
            rows = rows[:args.limit]

        if not rows:
            log.info("Все учреждения уже имеют координаты. Нечего делать.")
            return

        log.info(f"Учреждений к геокодингу: {len(rows)}")
        if args.dry_run:
            log.info("(DRY RUN — БД не меняется)")

        ok = miss = 0
        # verify=False — в backend-контейнере нет системного CA bundle
        # (SSL: CERTIFICATE_VERIFY_FAILED). 2GIS — публичный геокод-API, риск MITM минимален.
        async with httpx.AsyncClient(headers={"User-Agent": "geo-cossu/1.0"}, verify=False) as client:
            for i, row in enumerate(rows, 1):
                addr = _build_query(row)
                if not addr:
                    log.warning(f"[{i:>3}/{len(rows)}] org_bin={row.org_bin}: пустой адрес")
                    miss += 1
                    continue

                coords = await geocode_one(client, key, addr)
                if coords:
                    lat, lon = coords
                    log.info(f"[{i:>3}/{len(rows)}] {row.org_bin}: {lat:.5f}, {lon:.5f}  ← {addr[:70]}")
                    if not args.dry_run:
                        await db.execute(
                            sa_update(Cossu)
                            .where(Cossu.org_bin == row.org_bin)
                            .values(lat=lat, lon=lon, coord_source="2gis")
                        )
                        await db.commit()
                    ok += 1
                else:
                    log.warning(f"[{i:>3}/{len(rows)}] {row.org_bin}: НЕ НАЙДЕНО  ← {addr[:70]}")
                    miss += 1

                await asyncio.sleep(args.delay)

        log.info("")
        log.info(f"ИТОГО: успешно={ok}, не найдено={miss}, всего={ok + miss}")


if __name__ == "__main__":
    asyncio.run(main())
