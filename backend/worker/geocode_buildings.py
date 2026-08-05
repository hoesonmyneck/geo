"""
Геокодирование building (дома/сёла) через 2GIS Catalog API → lat/lon/geom.

- Берёт уникальные geocode_addr где lat IS NULL (резюмируемо + кэш по адресу:
  один запрос применяется ко ВСЕМ building с этим адресом одним UPDATE).
- Многопоточный (asyncio + семафор). Параллелизм настраивается --concurrency.
- verify=False (в контейнере нет CA bundle; 2GIS публичный).
- Детект meta.code (2GIS маскирует ошибки под HTTP 200).

Запуск (локально, порт 5432 проброшен):
    set TWOGIS_API_KEY=... (или --key)
    python backend/worker/geocode_buildings.py --concurrency 10 [--limit N]
"""
from __future__ import annotations
import argparse, asyncio, os, sys, time

import httpx
import psycopg

DSN = os.environ.get("GEO_DSN", "host=localhost port=5432 dbname=geo user=geo password=geopassword123")
API_URL = "https://catalog.api.2gis.com/3.0/items/geocode"


async def geocode_one(client, key, addr, retries=3):
    """Возвращает (lat, lon, precision) или None."""
    params = {"q": addr, "fields": "items.point,items.adm_div", "key": key}
    for attempt in range(retries):
        try:
            r = await client.get(API_URL, params=params, timeout=15.0)
        except (httpx.TimeoutException, httpx.NetworkError):
            await asyncio.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code != 200:
            if r.status_code == 429:
                await asyncio.sleep(5)
                continue
            return None
        data = r.json()
        meta = data.get("meta") or {}
        mcode = meta.get("code")
        if mcode == 403:
            msg = ((meta.get("error") or {}).get("message")) or "forbidden"
            print(f"  meta 403: {msg}. Ключ невалиден/исчерпан. Стоп.", file=sys.stderr)
            raise SystemExit(1)
        if mcode == 429:
            await asyncio.sleep(5)
            continue
        items = (data.get("result") or {}).get("items") or []
        if not items:
            return None
        it = items[0]
        pt = it.get("point")
        if not pt:
            return None
        # уровень точности: type записи (building/street/adm_div...)
        prec = it.get("type") or None
        return float(pt["lat"]), float(pt["lon"]), prec
    return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("TWOGIS_API_KEY"))
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.key:
        print("Нет ключа: --key или TWOGIS_API_KEY", file=sys.stderr)
        sys.exit(1)

    # уникальные адреса без координат
    sync = psycopg.connect(DSN)
    q = "SELECT geocode_addr FROM building WHERE lat IS NULL AND geocode_addr IS NOT NULL GROUP BY geocode_addr"
    addrs = [r[0] for r in sync.execute(q).fetchall()]
    sync.close()
    if args.limit:
        addrs = addrs[:args.limit]
    print(f"Уникальных адресов к геокоду: {len(addrs):,} (concurrency {args.concurrency})")
    if not addrs:
        return

    t0 = time.time()
    counter = {"ok": 0, "miss": 0, "done": 0}
    sem = asyncio.Semaphore(args.concurrency)
    lock = asyncio.Lock()

    aconn = await psycopg.AsyncConnection.connect(DSN, autocommit=True)

    async def process(addr):
        async with sem:
            coords = await geocode_one(client, args.key, addr)
        async with lock:
            counter["done"] += 1
            if coords:
                counter["ok"] += 1
            else:
                counter["miss"] += 1
            n = counter["done"]
        if coords and not args.dry_run:
            lat, lon, prec = coords
            await aconn.execute(
                """UPDATE building
                   SET lat=%s, lon=%s,
                       geom=ST_SetSRID(ST_MakePoint(%s,%s),4326),
                       precision=%s, coord_source='2gis', geocoded_at=now()
                   WHERE geocode_addr=%s AND lat IS NULL""",
                (lat, lon, lon, lat, prec, addr),
            )
        if n % 500 == 0:
            rate = n / max(time.time() - t0, 1e-6)
            eta = (len(addrs) - n) / max(rate, 1e-6)
            print(f"  {n:,}/{len(addrs):,} ok={counter['ok']:,} miss={counter['miss']:,} "
                  f"{rate:.0f}/s ETA {eta/3600:.1f}ч", file=sys.stderr)

    async with httpx.AsyncClient(headers={"User-Agent": "geo-building/1.0"}, verify=False) as client:
        # чанки, чтобы не создавать 886k тасков разом
        CHUNK = 20_000
        for i in range(0, len(addrs), CHUNK):
            batch = addrs[i:i + CHUNK]
            await asyncio.gather(*(process(a) for a in batch))

    await aconn.close()
    print(f"\nГотово за {(time.time()-t0)/3600:.2f}ч. ok={counter['ok']:,} miss={counter['miss']:,}")


if __name__ == "__main__":
    import urllib3
    try:
        urllib3.disable_warnings()
    except Exception:
        pass
    asyncio.run(main())
