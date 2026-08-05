"""Боевой геокод населения (pop_dwelling) через 2ГИС.

Пишет lat/lon + coord_source='2gis' + precision (высокая/средняя/низкая/не_найдено).
Резюмируемый: берёт только необработанные (lat IS NULL AND precision IS NULL).
Логику геокода и классификатор берём из geocode_pop_test.

Запуск (ХОСТ): python backend/worker/geocode_pop_dwellings.py <2GIS_KEY>
               [--concurrency 30] [--chunk 8000] [--limit N]
"""
from __future__ import annotations
import argparse, asyncio, time
from importlib.machinery import SourceFileLoader

import httpx, psycopg

t = SourceFileLoader("t", "backend/worker/geocode_pop_test.py").load_module()
DSN = t.DSN


async def run(key, conc, chunk, limit):
    sync = psycopg.connect(DSN, autocommit=False)
    sync.execute("ALTER TABLE pop_dwelling ADD COLUMN IF NOT EXISTS precision varchar(16)")
    sync.commit()

    q = ("SELECT dwelling_id, kind, geocode_addr FROM pop_dwelling "
         "WHERE lat IS NULL AND precision IS NULL AND geocode_addr IS NOT NULL AND geocode_addr<>''")
    if limit:
        q += f" LIMIT {int(limit)}"
    pending = sync.execute(q).fetchall()
    total = len(pending)
    print(f"к геокоду: {total:,}  (concurrency={conc}, chunk={chunk})", flush=True)
    if not total:
        sync.close(); return

    sem = asyncio.Semaphore(conc)
    tally = {"высокая": 0, "средняя": 0, "низкая": 0, "не_найдено": 0}
    t0 = time.time(); done = 0; stop = {"v": False}

    async with httpx.AsyncClient(headers={"User-Agent": "geo-pop/1.0"}, verify=False) as client:
        for i in range(0, total, chunk):
            if stop["v"]:
                break
            batch = pending[i:i + chunk]
            results = [None] * len(batch)

            async def work(j, did, kind, addr):
                async with sem:
                    res = await t.geocode(client, key, addr)
                if res.get("err"):
                    if "403" in str(res["err"]):
                        stop["v"] = True
                    return                      # transient/403 — не отмечаем
                if res.get("miss"):
                    results[j] = (None, None, None, "не_найдено", did)
                else:
                    lvl = t.classify(kind, addr, res)
                    results[j] = (res["lat"], res["lon"], "2gis", lvl, did)

            await asyncio.gather(*(work(j, *b) for j, b in enumerate(batch)))
            ups = [r for r in results if r]
            if ups:
                with sync.cursor() as cur:
                    cur.executemany(
                        "UPDATE pop_dwelling SET lat=%s, lon=%s, coord_source=%s, precision=%s "
                        "WHERE dwelling_id=%s", ups)
                sync.commit()
                for r in ups:
                    tally[r[3]] += 1
            done += len(batch)
            rate = done / max(time.time() - t0, 1e-6)
            eta = (total - done) / max(rate, 1e-6)
            print(f"  {done:,}/{total:,}  {rate:.0f}/с  ETA {eta/3600:.1f}ч  | "
                  + "  ".join(f"{k}={v:,}" for k, v in tally.items()), flush=True)
            if stop["v"]:
                print("!!! 403 от 2ГИС (ключ исчерпан/невалиден) — стоп. Прогон резюмируемый.", flush=True)
                break

    sync.close()
    print(f"\nобработано за проход: {done:,} за {(time.time()-t0)/3600:.2f}ч", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--chunk", type=int, default=8000)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    import urllib3
    try: urllib3.disable_warnings()
    except Exception: pass
    asyncio.run(run(a.key, a.concurrency, a.chunk, a.limit))


if __name__ == "__main__":
    main()
